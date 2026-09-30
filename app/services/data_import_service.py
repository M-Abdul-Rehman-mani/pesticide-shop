"""Load a CSV data export (``*.csv.zip``) back into the database.

The archive written by :mod:`app.services.data_export_service` holds one CSV per
table. Loading it is all-or-nothing: every table is read and converted before the
first row is written, rows go in parent tables first, every table's row count is
checked against its file, and any failure rolls the whole import back.

Two things an export never carries are handled here:

* **Password hashes.** Each user gets one temporary password and must change it
  at the next login. The password is returned so it can be shown to the owner.
* **Secret settings** such as the SMTP password. They are left out and have to be
  entered again under Settings.

Times in the export are local times. They are read in the time zone named in the
archive's manifest, or the computer's own zone for an export made before the
manifest existed.
"""

from __future__ import annotations

import ast
import base64
import csv
import io
import json
import secrets
import string
import sys
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Integer,
    LargeBinary,
    Numeric,
    Table,
    Uuid,
    func,
    select,
    text,
)
from sqlalchemy import (
    null as sql_null,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.data_export_service import (
    BINARY_PREFIX,
    MANIFEST_NAME,
    REDACTED_TEXT,
    DatasetSummary,
    _columns,
    _export_tables,
    _header,
    _label,
)
from app.utils.exceptions import ConflictError, ValidationError
from app.utils.security import hash_password

#: Largest single CSV accepted, uncompressed. A real shop's biggest table is far
#: smaller; the cap stops a damaged or hostile archive from exhausting memory.
_MAX_MEMBER = 512 * 2**20

#: Rows inserted per statement: large enough to be quick, small enough for memory.
_BATCH = 500

#: Attachments are base64 inside one CSV field, far past csv's 128 KiB default.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


@dataclass(frozen=True, slots=True)
class DataImportResult:
    datasets: tuple[DatasetSummary, ...]
    #: Set when user passwords were reset; every imported user signs in with it once.
    temporary_password: str | None
    users_reset: int
    secrets_skipped: int
    timezone: str

    @property
    def total_rows(self) -> int:
        return sum(dataset.rows for dataset in self.datasets)


class DataImportService:
    def __init__(
        self, session: Session, settings: Settings, *, lock_timeout_seconds: int = 10
    ) -> None:
        self._session = session
        self._settings = settings
        self._lock_timeout = max(1, int(lock_timeout_seconds))

    def import_archive(
        self,
        archive_path: Path,
        *,
        replace: bool,
        actor: AuthenticatedUser | None = None,
    ) -> DataImportResult:
        """Load an export into this database.

        ``replace`` deletes every existing record first. Without it the database
        must be empty, which is the case straight after ``alembic upgrade head``.
        ``actor`` is the signed-in owner; the command-line script passes none.
        """

        if actor is not None:
            require_permission(actor.role, Permission.RESTORE_DATABASE)
        tables = _export_tables()
        files, manifest = self._read_archive(archive_path)
        timezone = self._timezone(manifest)
        zone = ZoneInfo(timezone)
        temporary_password = _temporary_password()
        password_hash = hash_password(temporary_password)

        # Everything is parsed before anything is written, so a bad file fails
        # while the database is still untouched.
        prepared: list[tuple[Table, list[dict[str, Any]]]] = []
        users_reset = 0
        secrets_skipped = 0
        for table in tables:
            name = f"{table.name}.csv"
            if name not in files:
                raise ValidationError(
                    f"The export has no {name}; it is incomplete or from another application."
                )
            rows = self._parse_table(table, files[name], zone)
            if table.name == "users":
                for row in rows:
                    if row.get("password_hash") in (None, "", REDACTED_TEXT):
                        row["password_hash"] = password_hash
                        row["must_change_password"] = True
                        users_reset += 1
            if table.name == "app_settings":
                kept = [row for row in rows if row.get("value") != REDACTED_TEXT]
                secrets_skipped = len(rows) - len(kept)
                rows = kept
            prepared.append((table, rows))

        if replace:
            # Another open copy of the application holds locks the TRUNCATE must
            # wait for; fail with a clear message instead of waiting forever.
            self._session.execute(text(f"SET LOCAL lock_timeout = '{self._lock_timeout}s'"))
            names = ", ".join(f'"{table.name}"' for table in tables)
            # TRUNCATE, unlike DELETE, is not blocked by the immutable-history
            # triggers; it runs inside this transaction, so a failure restores all.
            try:
                self._session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            except OperationalError as exc:
                raise ConflictError(
                    "The database is in use. Close every other copy of the application and "
                    "stop the worker, then import again. Nothing was changed."
                ) from exc
        else:
            occupied = [table.name for table in tables if self._count(table)]
            if occupied:
                raise ConflictError(
                    "This database already holds records ("
                    + ", ".join(occupied[:4])
                    + (", …" if len(occupied) > 4 else "")
                    + "). Import into an empty database, or choose to replace everything."
                )

        summaries: list[DatasetSummary] = []
        for table, rows in prepared:
            for start in range(0, len(rows), _BATCH):
                self._session.execute(table.insert(), rows[start : start + _BATCH])
            loaded = self._count(table)
            if loaded != len(rows):
                raise ValidationError(
                    f"{_label(table.name)}: expected {len(rows)} rows but found {loaded}."
                )
            summaries.append(DatasetSummary(table.name, _label(table.name), len(rows)))
        # Rows were written below the ORM, so objects this session already holds
        # describe the old data; forget them so the next read sees the import.
        self._session.expire_all()

        actor_exists = actor is not None and any(
            row.get("id") == actor.id
            for table, rows in prepared
            if table.name == "users"
            for row in rows
        )
        AuditService(self._session).record(
            actor_id=actor.id if actor is not None and actor_exists else None,
            action="DATA_IMPORTED",
            entity_type="DataExport",
            entity_id=None,
            new_value={
                "archive": archive_path.name,
                "replace": replace,
                "rows": sum(summary.rows for summary in summaries),
                "users_reset": users_reset,
            },
        )
        self._session.flush()
        return DataImportResult(
            tuple(summaries),
            temporary_password if users_reset else None,
            users_reset,
            secrets_skipped,
            timezone,
        )

    # -- reading ---------------------------------------------------------

    @staticmethod
    def _read_archive(path: Path) -> tuple[dict[str, str], Mapping[str, Any]]:
        source = path.expanduser()
        if not source.is_file():
            raise ValidationError(f"{source} was not found.")
        try:
            with zipfile.ZipFile(source) as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                names = [info.filename.rsplit("/", 1)[-1] for info in members]
                duplicated = sorted({name for name in names if names.count(name) > 1})
                if duplicated:
                    raise ValidationError(
                        "The archive holds more than one " + ", ".join(duplicated) + "."
                    )
                oversized = [info.filename for info in members if info.file_size > _MAX_MEMBER]
                if oversized:
                    raise ValidationError(
                        f"{oversized[0]} is larger than {_MAX_MEMBER // 2**20} MB; "
                        "this is not a data export from this application."
                    )
                files = {
                    name: archive.read(info).decode("utf-8-sig")
                    for name, info in zip(names, members, strict=True)
                }
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise ValidationError(
                "Choose the .csv.zip archive written by Back Up To Excel & CSV."
            ) from exc
        manifest: Mapping[str, Any] = {}
        if MANIFEST_NAME in files:
            try:
                manifest = json.loads(files.pop(MANIFEST_NAME))
            except json.JSONDecodeError as exc:
                raise ValidationError("The export's manifest.json is damaged.") from exc
        return files, manifest

    def _timezone(self, manifest: Mapping[str, Any]) -> str:
        zone = manifest.get("timezone") or self._settings.app_timezone
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError(f"The export names an unknown time zone: {zone}") from exc
        return str(zone)

    def _count(self, table: Table) -> int:
        return int(self._session.scalar(select(func.count()).select_from(table)) or 0)

    def _parse_table(self, table: Table, content: str, zone: ZoneInfo) -> list[dict[str, Any]]:
        reader = csv.reader(io.StringIO(content, newline=""))
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValidationError(f"{table.name}.csv is empty; it has no header row.") from exc
        by_header = {_header(column.name): column for column in _columns(table)}
        unknown = [header for header in headers if header not in by_header]
        if unknown:
            raise ValidationError(
                f"{table.name}.csv has columns this version does not know: "
                + ", ".join(unknown)
                + ". Update the application before importing it."
            )
        columns = [by_header[header] for header in headers]
        missing = [
            column.name
            for column in table.columns
            if column not in columns
            and not column.nullable
            and column.server_default is None
            and column.default is None
        ]
        if missing:
            raise ValidationError(
                f"{table.name}.csv is missing required columns: " + ", ".join(missing)
            )
        converters = [_converter(column, zone) for column in columns]
        rows: list[dict[str, Any]] = []
        for line, values in enumerate(reader, start=2):
            if not values:
                continue
            if len(values) != len(columns):
                raise ValidationError(
                    f"{table.name}.csv line {line}: {len(values)} values for "
                    f"{len(columns)} columns."
                )
            row: dict[str, Any] = {}
            for column, convert, raw in zip(columns, converters, values, strict=True):
                try:
                    row[column.name] = convert(raw)
                except (ValueError, InvalidOperation, SyntaxError, KeyError) as exc:
                    raise ValidationError(
                        f"{table.name}.csv line {line}, {_header(column.name)}: "
                        f"{raw[:40]!r} is not a valid value."
                    ) from exc
            rows.append(row)
        return rows


def _converter(column: Column[Any], zone: ZoneInfo) -> Callable[[str], Any]:
    """Turn one exported cell back into the value the column stores."""

    kind = column.type
    empty: Any = None if column.nullable else ""

    def blank_or(parse: Callable[[str], Any]) -> Callable[[str], Any]:
        return lambda raw: empty if raw == "" else parse(raw)

    if isinstance(kind, Uuid):
        return blank_or(uuid.UUID)
    if isinstance(kind, Boolean):
        return blank_or(_boolean)
    if isinstance(kind, Integer):
        return blank_or(int)
    if isinstance(kind, Numeric):
        return blank_or(Decimal)
    if isinstance(kind, DateTime):
        if kind.timezone:
            return blank_or(lambda raw: _local_datetime(raw, zone))
        return blank_or(datetime.fromisoformat)
    if isinstance(kind, Date):
        return blank_or(date.fromisoformat)
    if isinstance(kind, Enum):
        enum_class = kind.enum_class
        if enum_class is not None:
            return blank_or(lambda raw: enum_class(raw))
        return blank_or(str)
    if isinstance(kind, JSONB):
        # A bare None would be stored as the JSON value null; an empty cell was SQL NULL.
        return lambda raw: sql_null() if raw == "" else _json(raw)
    if isinstance(kind, LargeBinary):
        return blank_or(_binary)
    # Text columns: an empty cell is an empty string where NULL is not allowed.
    return lambda raw: raw if raw != "" or not column.nullable else None


def _boolean(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    raise ValueError(raw)


def _local_datetime(raw: str, zone: ZoneInfo) -> datetime:
    value = datetime.fromisoformat(raw)
    return value if value.tzinfo is not None else value.replace(tzinfo=zone)


def _json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Exports made before the manifest wrote Python's own repr of the value.
        return ast.literal_eval(raw)


def _binary(raw: str) -> bytes:
    if raw.startswith(BINARY_PREFIX):
        return base64.b64decode(raw[len(BINARY_PREFIX) :], validate=True)
    # Older exports wrote bytes as their Python literal, b'...'.
    value = ast.literal_eval(raw)
    if not isinstance(value, bytes):
        raise ValueError(raw)
    return value


def _temporary_password() -> str:
    """A password that meets the strength rules: upper, lower, digit, 14 characters."""

    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(11))
    return (
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + body
    )


def summarise(result: DataImportResult) -> Sequence[str]:
    """Lines telling the owner what was loaded and what must be done next."""

    lines = [f"Imported {result.total_rows:,} records from {len(result.datasets)} tables."]
    if result.temporary_password:
        lines.append(
            f"All {result.users_reset} user passwords were reset. Everyone signs in once with "
            f"the temporary password {result.temporary_password} and then chooses a new one."
        )
    if result.secrets_skipped:
        lines.append("Re-enter the SMTP password under Settings > Email.")
    return lines
