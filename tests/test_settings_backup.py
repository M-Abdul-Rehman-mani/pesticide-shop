from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.email.configuration import load_smtp_config
from app.models.enums import SettingCategory, UserRole
from app.models.settings import AppSetting
from app.printing.shop_profile import load_shop_profile
from app.security.authentication import AuthenticatedUser
from app.services.backup_service import BackupService
from app.services.settings_service import SettingsService
from app.utils.exceptions import InfrastructureError, PermissionDeniedError, ValidationError


def test_encrypted_settings_validation_and_smtp_loading(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    settings = get_settings()
    service = SettingsService(db_session, settings.app_secret_key.get_secret_value())
    service.set(
        actor=owner,
        category=SettingCategory.GENERAL,
        key="currency",
        value="usd",
    )
    service.set(
        actor=owner,
        category=SettingCategory.EMAIL,
        key="smtp_host",
        value="smtp.test.invalid",
    )
    service.set(
        actor=owner,
        category=SettingCategory.EMAIL,
        key="smtp_from_email",
        value="SHOP@TEST.INVALID",
    )
    secret = service.set(
        actor=owner,
        category=SettingCategory.EMAIL,
        key="smtp_password",
        value="smtp-test-secret",
        is_secret=True,
    )
    db_session.flush()
    assert service.get(SettingCategory.GENERAL, "currency") == "USD"
    assert service.get(SettingCategory.EMAIL, "smtp_password") == "smtp-test-secret"
    stored = db_session.scalar(select(AppSetting).where(AppSetting.id == secret.id))
    assert stored is not None
    assert stored.value != "smtp-test-secret"
    smtp = load_smtp_config(db_session, settings)
    assert smtp.host == "smtp.test.invalid"
    assert smtp.from_email == "shop@test.invalid"
    assert smtp.password == "smtp-test-secret"

    invalid_values = (
        (SettingCategory.GENERAL, "currency", "RUPEES"),
        (SettingCategory.GENERAL, "timezone", "Mars/Olympus"),
        (SettingCategory.GENERAL, "invoice_prefix", "bad prefix!"),
        (SettingCategory.EMAIL, "smtp_port", "70000"),
        (SettingCategory.PRINTER, "receipt_width", "72"),
        (SettingCategory.SECURITY, "session_timeout", "2"),
        (SettingCategory.BACKUP, "directory", ""),
    )
    for category, key, value in invalid_values:
        with pytest.raises(ValidationError):
            service.set(actor=owner, category=category, key=key, value=value)


def test_settings_permissions(db_session: Session, owner: AuthenticatedUser) -> None:
    salesperson = replace(owner, role=UserRole.SALESPERSON)
    with pytest.raises(PermissionDeniedError):
        SettingsService(db_session, get_settings().app_secret_key.get_secret_value()).set(
            actor=salesperson,
            category=SettingCategory.SHOP,
            key="name",
            value="No access",
        )


def test_shop_profile_fields_are_optional_and_load_for_receipts(
    db_session: Session,
    owner: AuthenticatedUser,
) -> None:
    settings = get_settings()
    service = SettingsService(db_session, settings.app_secret_key.get_secret_value())
    for key, value in {
        "name": "",
        "owner_name": "Shop Owner",
        "address": "",
        "phone": "",
        "email": "",
        "website": "example.test",
        "tax_information": "",
        "logo_path": "",
    }.items():
        service.set(
            actor=owner,
            category=SettingCategory.SHOP,
            key=key,
            value=value,
        )
    db_session.flush()

    profile = load_shop_profile(db_session, settings)

    assert profile.name == ""
    assert profile.owner_name == "Shop Owner"
    assert profile.website == "example.test"
    assert profile.address == ""
    assert profile.logo_path is None


def test_backup_success_then_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_backup = tmp_path / "backup_2020-01-01_000000.dump"
    old_backup.write_bytes(b"old-valid-backup")
    expired = (datetime.now() - timedelta(days=90)).timestamp()
    os.utime(old_backup, (expired, expired))
    settings = get_settings().model_copy(
        update={
            "backup_directory": tmp_path,
            "backup_retention_days": 30,
            "backup_compress": True,
        }
    )
    monkeypatch.setattr("app.services.backup_service.shutil.which", lambda name: f"/{name}")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        destination = Path(command[command.index("--file") + 1])
        destination.write_bytes(b"valid-postgresql-backup")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.services.backup_service.subprocess.run", fake_run)
    result = BackupService(settings).create_backup(uuid.uuid4())
    assert result.path.is_file()
    assert result.size_bytes > 0
    assert result.removed_expired == (old_backup,)
    assert not old_backup.exists()


def test_failed_backup_preserves_existing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_backup = tmp_path / "backup_2020-01-01_000000.sql"
    old_backup.write_bytes(b"keep-me")
    settings = get_settings().model_copy(update={"backup_directory": tmp_path})
    monkeypatch.setattr("app.services.backup_service.shutil.which", lambda _name: "/pg_dump")

    def fail(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, stderr="simulated pg_dump failure")

    monkeypatch.setattr("app.services.backup_service.subprocess.run", fail)
    with pytest.raises(InfrastructureError, match="simulated"):
        BackupService(settings).create_backup()
    assert old_backup.read_bytes() == b"keep-me"
    assert not list(tmp_path.glob("*.part"))


def test_restore_is_owner_only_and_restricted_to_backup_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: AuthenticatedUser,
) -> None:
    settings = get_settings().model_copy(update={"backup_directory": tmp_path})
    service = BackupService(settings)
    backup = tmp_path / "backup_2026-08-14_210000.dump"
    backup.write_bytes(b"backup")
    salesperson = replace(owner, role=UserRole.SALESPERSON)
    with pytest.raises(PermissionDeniedError):
        service.restore(backup, salesperson)
    with pytest.raises(ValidationError, match="configured"):
        service.restore(tmp_path.parent / "outside.dump", owner)
    commands: list[list[str]] = []
    monkeypatch.setattr("app.services.backup_service.shutil.which", lambda name: f"/{name}")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.services.backup_service.subprocess.run", fake_run)
    service.restore(backup, owner)
    assert commands[0][0] == "/pg_restore"
    assert "--single-transaction" in commands[0]
