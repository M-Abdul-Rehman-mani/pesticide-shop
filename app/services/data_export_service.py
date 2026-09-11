"""Whole-database export to a spreadsheet workbook and a CSV archive.

The PostgreSQL dump written by :mod:`app.services.backup_service` is the backup
that can be restored, but it can only be read by PostgreSQL. Shops also need a
copy they can open, email to an accountant, or archive off-site, so every table
is exported here as one worksheet and one CSV. The file name carries the span of
the data it holds, which is what makes a folder of these readable at a glance.
"""

from __future__ import annotations

import csv
import io
import uuid
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Column, DateTime, Table, func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.database.base import Base
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService

#: Columns never written to an export, whatever the caller's role. Password
#: hashes are useless outside the application and dangerous everywhere else.
REDACTED_COLUMNS: frozenset[tuple[str, str]] = frozenset({("users", "password_hash")})

#: Placeholder written in place of a redacted value so the column still lines up.
REDACTED_TEXT = "[redacted]"

#: Tables holding operational noise rather than shop records.
EXCLUDED_TABLES: frozenset[str] = frozenset({"alembic_version"})


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    table: str
    label: str
    rows: int


@dataclass(frozen=True, slots=True)
class DataExportResult:
    workbook: Path
    csv_archive: Path
    period_start: date | None
    period_end: date | None
    datasets: tuple[DatasetSummary, ...]

    @property
    def total_rows(self) -> int:
        return sum(dataset.rows for dataset in self.datasets)


def _label(name: str) -> str:
    return name.replace("_", " ").title()


def _header(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _export_tables() -> tuple[Table, ...]:
    return tuple(
        table for table in Base.metadata.sorted_tables if table.name not in EXCLUDED_TABLES
    )


def _columns(table: Table) -> tuple[Column[object], ...]:
    """Order a table's columns with its key first, as a reader expects."""

    key = tuple(table.primary_key.columns)
    return key + tuple(column for column in table.columns if column not in key)


def _period_column(table: Table) -> object | None:
    """Pick the column that dates a row, preferring the document's own date."""

    for name in ("sale_date", "purchase_date", "movement_date", "return_date", "created_at"):
        if name in table.columns:
            return table.columns[name]
    for column in table.columns:
        if isinstance(column.type, DateTime):
            return column
    return None


class DataExportService:
    """Exports every mapped table to one workbook and one archive of CSVs."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def export(self, directory: Path, actor: AuthenticatedUser) -> DataExportResult:
        require_permission(actor.role, Permission.MANAGE_SETTINGS)
        tables = _export_tables()
        start, end = self._period(tables)
        target = directory.expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        stem = self._unique_stem(target, self._file_stem(start, end))
        workbook_path = target / f"{stem}.xlsx"
        archive_path = target / f"{stem}.csv.zip"
        datasets = self._write(tables, workbook_path, archive_path)
        AuditService(self._session).record(
            actor_id=actor.id,
            action="DATA_EXPORT_CREATED",
            entity_type="DataExport",
            entity_id=None,
            new_value={
                "workbook": workbook_path.name,
                "archive": archive_path.name,
                "rows": sum(dataset.rows for dataset in datasets),
            },
        )
        return DataExportResult(workbook_path, archive_path, start, end, datasets)

    def _write(
        self, tables: Sequence[Table], workbook_path: Path, archive_path: Path
    ) -> tuple[DatasetSummary, ...]:
        # Imported lazily so a missing spreadsheet library cannot stop the
        # application from starting; the export is the only feature that needs it.
        from app.reports.excel_exporter import ExcelExporter

        summaries: list[DatasetSummary] = []
        sheets: list[tuple[str, Sequence[str], Sequence[Sequence[object]]]] = []
        partial = archive_path.with_name(f".{archive_path.name}.part")
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                for table in tables:
                    headers = [_header(column.name) for column in _columns(table)]
                    rows = list(self._rows(table))
                    sheets.append((_label(table.name), headers, rows))
                    archive.writestr(f"{table.name}.csv", self._csv(headers, rows))
                    summaries.append(DatasetSummary(table.name, _label(table.name), len(rows)))
            ExcelExporter().export_workbook(workbook_path, sheets)
            partial.replace(archive_path)
        except Exception:
            partial.unlink(missing_ok=True)
            workbook_path.unlink(missing_ok=True)
            raise
        return tuple(summaries)

    def _rows(self, table: Table) -> Iterator[list[object]]:
        columns = _columns(table)
        names = [column.name for column in columns]
        statement = select(*columns).order_by(*columns[:1])
        redacted = {
            index
            for index, column in enumerate(columns)
            if (table.name, column.name) in REDACTED_COLUMNS
        }
        # Encrypted settings are stored as ciphertext, but the flag beside them
        # says which ones are secret, so the export refuses to carry those at all.
        secret_flag = names.index("is_secret") if table.name == "app_settings" else None
        value_column = (
            names.index("value") if secret_flag is not None and "value" in names else None
        )
        for row in self._session.execute(statement).yield_per(500):
            values = [self._scalar(value) for value in row]
            for index in redacted:
                values[index] = REDACTED_TEXT
            if value_column is not None and secret_flag is not None and row[secret_flag]:
                values[value_column] = REDACTED_TEXT
            yield values

    def _scalar(self, value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (uuid.UUID, dict, list)):
            return str(value)
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(ZoneInfo(self._settings.app_timezone)).replace(tzinfo=None)
        return value

    @staticmethod
    def _csv(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(headers)
        for row in rows:
            writer.writerow(
                [
                    ""
                    if value is None
                    else (
                        f"{value:f}"
                        if isinstance(value, Decimal)
                        else value.isoformat(sep=" ")
                        if isinstance(value, datetime)
                        else value.isoformat()
                        if isinstance(value, date)
                        else value
                    )
                    for value in row
                ]
            )
        # Excel opens UTF-8 CSV correctly only when the byte order mark is present.
        return "﻿" + buffer.getvalue()

    def _period(self, tables: Sequence[Table]) -> tuple[date | None, date | None]:
        """Find the span the exported rows cover, as local dates."""

        earliest: datetime | None = None
        latest: datetime | None = None
        for table in tables:
            column = _period_column(table)
            if column is None:
                continue
            bounds = self._session.execute(select(func.min(column), func.max(column))).one()
            for value, keep_lowest in ((bounds[0], True), (bounds[1], False)):
                if not isinstance(value, datetime):
                    continue
                if keep_lowest and (earliest is None or value < earliest):
                    earliest = value
                if not keep_lowest and (latest is None or value > latest):
                    latest = value
        return self._local_date(earliest), self._local_date(latest)

    def _local_date(self, value: datetime | None) -> date | None:
        if value is None:
            return None
        zone = ZoneInfo(self._settings.app_timezone)
        return (value.astimezone(zone) if value.tzinfo else value).date()

    def _file_stem(self, start: date | None, end: date | None) -> str:
        today = datetime.now(ZoneInfo(self._settings.app_timezone)).date()
        first = start or today
        last = max(end or today, first)
        return f"shop-data_from_{first:%Y-%m-%d}_to_{last:%Y-%m-%d}"

    @staticmethod
    def _unique_stem(directory: Path, stem: str) -> str:
        """Keep an earlier export of the same span instead of overwriting it.

        Both files share one stem, so the spreadsheet and the archive of an export
        always carry the same name.
        """

        def taken(candidate: str) -> bool:
            return any(
                (directory / f"{candidate}{suffix}").exists() for suffix in (".xlsx", ".csv.zip")
            )

        if not taken(stem):
            return stem
        for attempt in range(2, 100):
            if not taken(f"{stem}_{attempt}"):
                return f"{stem}_{attempt}"
        raise FileExistsError(f"Too many exports already exist for {stem}.")
