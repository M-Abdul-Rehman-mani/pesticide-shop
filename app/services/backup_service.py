"""Safe PostgreSQL backup and owner-only restore operations."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.utils.exceptions import InfrastructureError, ValidationError


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    size_bytes: int
    removed_expired: tuple[Path, ...]


class BackupService:
    def __init__(self, settings: Settings, session: Session | None = None) -> None:
        self._settings = settings
        self._session = session

    def create_backup(self, actor_id: uuid.UUID | None = None) -> BackupResult:
        pg_dump = self._database_tool("pg_dump")
        directory = self._settings.backup_directory.expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(ZoneInfo(self._settings.app_timezone)).strftime("%Y-%m-%d_%H%M%S")
        extension = ".dump" if self._settings.backup_compress else ".sql"
        destination = directory / f"backup_{timestamp}{extension}"
        temporary = directory / f".{destination.name}.part"
        command = [
            pg_dump,
            "--host",
            self._settings.database_host,
            "--port",
            str(self._settings.database_port),
            "--username",
            self._settings.database_user,
            "--dbname",
            self._settings.database_name,
            "--no-password",
            "--file",
            str(temporary),
        ]
        if self._settings.backup_compress:
            command.extend(["--format", "custom", "--compress", "9"])
        else:
            command.extend(["--format", "plain"])
        environment = os.environ.copy()
        environment["PGPASSWORD"] = self._settings.database_password.get_secret_value()
        try:
            subprocess.run(
                command,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise InfrastructureError("pg_dump completed without creating a valid backup file.")
            temporary.replace(destination)
        except subprocess.CalledProcessError as exc:
            temporary.unlink(missing_ok=True)
            error = (exc.stderr or "pg_dump failed").strip().splitlines()[-1]
            raise InfrastructureError(f"Database backup failed: {error}") from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        removed = self._remove_expired_backups(directory, keep=destination)
        if self._session:
            AuditService(self._session).record(
                actor_id=actor_id,
                action="DATABASE_BACKUP_CREATED",
                entity_type="DatabaseBackup",
                entity_id=None,
                new_value={"filename": destination.name, "size": destination.stat().st_size},
            )
        return BackupResult(destination, destination.stat().st_size, removed)

    def restore(self, backup_path: Path, actor: AuthenticatedUser) -> None:
        require_permission(actor.role, Permission.RESTORE_DATABASE)
        directory = self._settings.backup_directory.expanduser().resolve()
        source = backup_path.expanduser().resolve()
        if source.parent != directory or not source.is_file():
            raise ValidationError("Select an existing backup from the configured backup directory.")
        environment = os.environ.copy()
        environment["PGPASSWORD"] = self._settings.database_password.get_secret_value()
        common = [
            "--host",
            self._settings.database_host,
            "--port",
            str(self._settings.database_port),
            "--username",
            self._settings.database_user,
            "--dbname",
            self._settings.database_name,
            "--no-password",
        ]
        if source.suffix == ".dump":
            executable = self._database_tool("pg_restore")
            command = [
                executable,
                *common,
                "--clean",
                "--if-exists",
                "--single-transaction",
                str(source),
            ]
        elif source.suffix == ".sql":
            executable = self._database_tool("psql")
            command = [executable, *common, "--single-transaction", "--file", str(source)]
        else:
            raise ValidationError("Only .dump and .sql backup files can be restored.")
        try:
            subprocess.run(
                command,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        except subprocess.CalledProcessError as exc:
            error = (exc.stderr or "restore failed").strip().splitlines()[-1]
            raise InfrastructureError(f"Database restore failed: {error}") from exc

    def _database_tool(self, name: str) -> str:
        directory = self._settings.postgres_tools_directory
        if directory is not None:
            suffix = ".exe" if os.name == "nt" else ""
            candidate = directory.expanduser().resolve() / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
            raise InfrastructureError(
                f"{name} was not found in configured PostgreSQL tools directory: {directory}"
            )
        executable = shutil.which(name)
        if executable:
            return executable
        raise InfrastructureError(
            f"{name} was not found. Install PostgreSQL client tools matching the server major "
            "version or set POSTGRES_TOOLS_DIRECTORY."
        )

    def _remove_expired_backups(self, directory: Path, *, keep: Path) -> tuple[Path, ...]:
        cutoff = (
            datetime.now().timestamp()
            - timedelta(days=self._settings.backup_retention_days).total_seconds()
        )
        removed: list[Path] = []
        for candidate in sorted(directory.glob("backup_*.*")):
            if candidate == keep or candidate.suffix not in {".dump", ".sql"}:
                continue
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                removed.append(candidate)
        return tuple(removed)
