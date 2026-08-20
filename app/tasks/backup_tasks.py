"""Scheduled PostgreSQL backup task."""

from app.config.settings import get_settings
from app.services.backup_service import BackupService
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.backup_tasks.create_database_backup")
def create_database_backup() -> str:
    return str(BackupService(get_settings()).create_backup().path)
