"""Celery worker and scheduler configuration."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config.settings import get_settings

settings = get_settings()
report_hour, report_minute = (int(value) for value in settings.daily_report_time.split(":"))

celery_app = Celery(
    "pesticide_shop",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=(
        "app.tasks.email_tasks",
        "app.tasks.report_tasks",
        "app.tasks.backup_tasks",
    ),
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.app_timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.tasks.email_tasks.*": {"queue": "email"},
        "app.tasks.backup_tasks.*": {"queue": "maintenance"},
        "app.tasks.report_tasks.*": {"queue": "reports"},
    },
    beat_schedule={
        "dispatch-email-outbox": {
            "task": "app.tasks.email_tasks.dispatch_pending_emails",
            "schedule": 30.0,
        },
        "daily-owner-report": {
            "task": "app.tasks.report_tasks.generate_daily_owner_report",
            "schedule": crontab(minute=report_minute, hour=report_hour),
        },
        "daily-database-backup": {
            "task": "app.tasks.backup_tasks.create_database_backup",
            "schedule": crontab(
                minute=(report_minute + 30) % 60,
                hour=(report_hour + (report_minute + 30) // 60) % 24,
            ),
        },
    },
)
celery_app.autodiscover_tasks(["app.tasks"])
