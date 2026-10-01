"""Email retries, the daily owner report, and the daily backup, all run by the program itself."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings, get_settings
from app.email.email_service import OutgoingEmail
from app.email.outbox import MAX_DELIVERY_ATTEMPTS, deliver_email, deliver_pending, retry_delay
from app.models.audit import AuditLog
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, SettingCategory
from app.reports.daily_report import DAILY_REPORT_ENTITY, daily_report_id, queue_daily_report
from app.security.authentication import AuthenticatedUser
from app.services.backup_service import BackupResult
from app.services.scheduled_backup import backup_due, last_scheduled_moment, run_due_backup
from app.services.settings_service import SettingsService
from app.utils.exceptions import ValidationError


class RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    def send(self, message: OutgoingEmail) -> None:
        self.sent.append(message)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> RecordingTransport:
    recorder = RecordingTransport()
    monkeypatch.setattr("app.email.smtp_client.SMTPEmailService", lambda _config: recorder)
    return recorder


@pytest.fixture
def factory(db_session: Session) -> sessionmaker[Session]:
    return sessionmaker[Session](
        bind=db_session.get_bind(),
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )


def _configure_email(session: Session, owner: AuthenticatedUser, *, owners: str = "") -> None:
    stored = SettingsService(session, get_settings().app_secret_key.get_secret_value())
    stored.set(actor=owner, category=SettingCategory.EMAIL, key="smtp_host", value="smtp.invalid")
    stored.set(
        actor=owner,
        category=SettingCategory.EMAIL,
        key="smtp_from_email",
        value="shop@shop.invalid",
    )
    if owners:
        stored.set(actor=owner, category=SettingCategory.EMAIL, key="owner_email", value=owners)
    session.flush()


def _email(
    session: Session,
    *,
    status: EmailStatus,
    attempts: int = 0,
    last_attempt: datetime | None = None,
) -> EmailHistory:
    row = EmailHistory(
        recipient="someone@shop.invalid",
        subject="Queued",
        template="test",
        entity_type="Test",
        entity_id=uuid.uuid4(),
        status=status,
        attempts=attempts,
        last_attempt_at=last_attempt,
        body_text="Hello",
    )
    session.add(row)
    session.flush()
    return row


# --------------------------------------------------------------------- email retries


def test_retry_waits_longer_after_each_failure_up_to_an_hour() -> None:
    assert [retry_delay(n) for n in (0, 1, 3)] == [
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=8),
    ]
    assert retry_delay(12) == timedelta(hours=1)


def test_due_messages_are_sent_and_the_rest_wait(
    db_session: Session,
    owner: AuthenticatedUser,
    factory: sessionmaker[Session],
    transport: RecordingTransport,
) -> None:
    _configure_email(db_session, owner)
    now = datetime.now(UTC)
    queued = _email(db_session, status=EmailStatus.PENDING)
    just_failed = _email(
        db_session, status=EmailStatus.FAILED, attempts=2, last_attempt=now - timedelta(minutes=1)
    )
    failed_long_ago = _email(
        db_session, status=EmailStatus.FAILED, attempts=2, last_attempt=now - timedelta(minutes=5)
    )
    given_up = _email(
        db_session,
        status=EmailStatus.FAILED,
        attempts=MAX_DELIVERY_ATTEMPTS,
        last_attempt=now - timedelta(days=1),
    )
    stuck = _email(
        db_session, status=EmailStatus.SENDING, attempts=1, last_attempt=now - timedelta(hours=1)
    )

    outcome = deliver_pending(factory, get_settings(), now=now)

    assert outcome.sent == 3
    db_session.expire_all()
    for row in (queued, failed_long_ago, stuck):
        db_session.refresh(row)
        assert row.status is EmailStatus.SENT
    for row in (just_failed, given_up):
        db_session.refresh(row)
        assert row.status is EmailStatus.FAILED


def test_nothing_is_marked_failed_while_smtp_is_not_set_up(
    db_session: Session, factory: sessionmaker[Session], transport: RecordingTransport
) -> None:
    row = _email(db_session, status=EmailStatus.PENDING)

    outcome = deliver_pending(factory, get_settings())

    assert outcome.skipped and transport.sent == []
    db_session.refresh(row)
    assert row.status is EmailStatus.PENDING


def test_a_manual_retry_sends_even_a_message_that_was_given_up_on(
    db_session: Session,
    owner: AuthenticatedUser,
    factory: sessionmaker[Session],
    transport: RecordingTransport,
) -> None:
    _configure_email(db_session, owner)
    row = _email(db_session, status=EmailStatus.FAILED, attempts=MAX_DELIVERY_ATTEMPTS)

    assert deliver_email(factory, get_settings(), row.id).sent == 1
    assert deliver_email(factory, get_settings(), row.id).attempted == 0


# --------------------------------------------------------------------- daily owner report


def test_the_daily_report_needs_an_owner_address(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    with pytest.raises(ValidationError, match="owner email"):
        queue_daily_report(db_session, get_settings(), date.today(), owner)


def test_the_daily_report_is_emailed_to_every_owner_as_a_pdf(
    db_session: Session,
    owner: AuthenticatedUser,
    factory: sessionmaker[Session],
    transport: RecordingTransport,
) -> None:
    from app.email.outbox import deliver_pending_for

    _configure_email(db_session, owner, owners="owner@shop.invalid, partner@shop.invalid")
    report_date = date(2026, 9, 30)

    report_id = queue_daily_report(db_session, get_settings(), report_date, owner)
    outcome = deliver_pending_for(
        factory, get_settings(), entity_type=DAILY_REPORT_ENTITY, entity_id=report_id
    )

    assert report_id == daily_report_id(report_date)
    assert outcome.sent == 2
    assert {message.recipient for message in transport.sent} == {
        "owner@shop.invalid",
        "partner@shop.invalid",
    }
    for message in transport.sent:
        assert message.subject == "Daily Shop Report - 30-Sep-2026"
        assert message.attachments[0].filename == "daily-report-2026-09-30.pdf"
        assert message.attachments[0].content.startswith(b"%PDF")


# --------------------------------------------------------------------- daily backup

ZONE = ZoneInfo("Asia/Karachi")
AT = time(21, 30)


def test_the_backup_is_due_once_per_scheduled_time() -> None:
    evening = datetime(2026, 10, 1, 22, 0, tzinfo=ZONE)
    morning = datetime(2026, 10, 2, 9, 0, tzinfo=ZONE)

    assert last_scheduled_moment(evening, AT) == datetime(2026, 10, 1, 21, 30, tzinfo=ZONE)
    assert last_scheduled_moment(morning, AT) == datetime(2026, 10, 1, 21, 30, tzinfo=ZONE)
    assert backup_due(None, morning, AT)
    assert backup_due(datetime(2026, 10, 1, 9, 0, tzinfo=ZONE), evening, AT)
    assert not backup_due(datetime(2026, 10, 1, 21, 45, tzinfo=ZONE), morning, AT)


def _settings() -> Settings:
    return get_settings().model_copy(update={"backup_time": "21:30"})


def test_the_open_program_takes_the_missed_backup_once(
    db_session: Session, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    class FakeBackup:
        def __init__(self, _settings: Settings) -> None:
            pass

        def create_backup(self) -> BackupResult:
            calls.append(1)
            return BackupResult(Path("backup_test.dump"), 1234, ())

    monkeypatch.setattr("app.services.scheduled_backup.BackupService", FakeBackup)
    zone = ZoneInfo(_settings().app_timezone)
    now = datetime.now(zone).replace(hour=22, minute=0)

    first = run_due_backup(factory, _settings(), now=now)
    second = run_due_backup(factory, _settings(), now=now + timedelta(minutes=15))

    assert first is not None and first.path.name == "backup_test.dump"
    assert second is None
    assert calls == [1]
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "DATABASE_BACKUP_CREATED")
    ).all()
    assert any(entry.new_value and entry.new_value.get("automatic") for entry in audit)
    # The next evening it is due again.
    assert run_due_backup(factory, _settings(), now=now + timedelta(days=1)) is not None
    assert calls == [1, 1]


def test_a_failed_backup_is_tried_again_later(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    class FailingThenWorking:
        def __init__(self, _settings: Settings) -> None:
            pass

        def create_backup(self) -> BackupResult:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("pg_dump: server version mismatch")
            return BackupResult(Path("backup_ok.dump"), 10, ())

    monkeypatch.setattr("app.services.scheduled_backup.BackupService", FailingThenWorking)
    now = datetime.now(ZoneInfo(_settings().app_timezone)).replace(hour=22, minute=0)

    with pytest.raises(RuntimeError):
        run_due_backup(factory, _settings(), now=now)
    assert run_due_backup(factory, _settings(), now=now + timedelta(minutes=15)) is not None
    assert len(attempts) == 2


@pytest.mark.ui
def test_the_settings_button_emails_the_daily_report(
    qtbot: object,
    db_session: Session,
    owner: AuthenticatedUser,
    factory: sessionmaker[Session],
    transport: RecordingTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import QDate, QThreadPool
    from PySide6.QtWidgets import QMessageBox

    from app.ui.settings.screen import SettingsScreen

    _configure_email(db_session, owner, owners="owner@shop.invalid")
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda _parent, _title, text: shown.append(text)
    )
    screen = SettingsScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)  # type: ignore[attr-defined]
    QThreadPool.globalInstance().waitForDone(5000)

    screen.report_date.setDate(QDate(2026, 9, 30))
    screen.daily_report_button.click()
    qtbot.waitUntil(lambda: bool(shown), timeout=10000)  # type: ignore[attr-defined]
    QThreadPool.globalInstance().waitForDone(5000)

    assert shown == ["The report for 30-Sep-2026 was emailed to the owner."]
    assert [message.recipient for message in transport.sent] == ["owner@shop.invalid"]
    assert screen.daily_report_button.isEnabled()
