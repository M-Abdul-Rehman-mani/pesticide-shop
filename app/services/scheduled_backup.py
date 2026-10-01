"""The daily automatic database backup, run by the open application.

There is no background service: whichever computer has the program open when the
backup is due takes it. A PostgreSQL advisory lock lets only one computer run it at
a time, and the moment of the last successful automatic backup is kept in the
database so every computer agrees on whether today's backup is done. A computer
that was off at backup time catches up the next time the program is opened.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.enums import SettingCategory
from app.models.settings import AppSetting
from app.services.audit_service import AuditService
from app.services.backup_service import BackupResult, BackupService

LAST_BACKUP_KEY = "last_automatic_backup"
#: Arbitrary constant naming the advisory lock that serialises automatic backups.
_BACKUP_LOCK_ID = 7_315_042_118


def last_scheduled_moment(now: datetime, at: time) -> datetime:
    """The most recent backup time at or before ``now`` (``now`` is zone-aware)."""

    today = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
    return today if today <= now else today - timedelta(days=1)


def backup_due(last_backup: datetime | None, now: datetime, at: time) -> bool:
    """True when no automatic backup has run since the latest scheduled time."""

    return last_backup is None or last_backup < last_scheduled_moment(now, at)


def _last_backup(session: Session) -> datetime | None:
    value = session.scalar(
        select(AppSetting.value).where(
            AppSetting.category == SettingCategory.BACKUP, AppSetting.key == LAST_BACKUP_KEY
        )
    )
    return datetime.fromisoformat(value) if value else None


def _record_backup(session: Session, moment: datetime) -> None:
    setting = session.scalar(
        select(AppSetting).where(
            AppSetting.category == SettingCategory.BACKUP, AppSetting.key == LAST_BACKUP_KEY
        )
    )
    if setting is None:
        session.add(
            AppSetting(
                category=SettingCategory.BACKUP, key=LAST_BACKUP_KEY, value=moment.isoformat()
            )
        )
    else:
        setting.value = moment.isoformat()


def run_due_backup(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> BackupResult | None:
    """Back the database up if today's automatic backup has not been taken yet.

    Returns the backup made, or ``None`` when nothing was due or another computer
    is already taking it. A failed backup raises and is retried on a later call.
    """

    zone = ZoneInfo(settings.app_timezone)
    moment = (now or datetime.now(zone)).astimezone(zone)
    at = settings.backup_time_of_day
    with session_factory() as session:
        if not backup_due(_last_backup(session), moment, at):
            return None
    with session_factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(_BACKUP_LOCK_ID))):
            return None
        # Checked again under the lock: another computer may have just finished.
        if not backup_due(_last_backup(session), moment, at):
            return None
        result = BackupService(settings).create_backup()
        _record_backup(session, moment)
        AuditService(session).record(
            actor_id=None,
            action="DATABASE_BACKUP_CREATED",
            entity_type="DatabaseBackup",
            entity_id=None,
            new_value={
                "filename": result.path.name,
                "size": result.size_bytes,
                "automatic": True,
            },
        )
    return result
