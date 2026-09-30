"""A CSV data export loads back into the database exactly as it was taken."""

from __future__ import annotations

import base64
import csv
import io
import json
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.base import Base
from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, PaymentMethod, SettingCategory, UserRole
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.user import User
from app.security.authentication import AuthenticatedUser
from app.services.audit_service import AuditService
from app.services.data_export_service import DataExportService
from app.services.data_import_service import DataImportService, summarise
from app.services.dto import CreatePesticideSaleCommand, PaymentInput, PesticideSaleLineInput
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.practice_records import ensure_practice_records
from app.services.settings_service import SettingsService
from app.utils.exceptions import ConflictError, ValidationError
from app.utils.security import verify_password
from tests.test_dashboard import _stock

#: Values an export can never carry, compared separately.
_NOT_EXPORTED = {("users", "password_hash"), ("users", "must_change_password")}


def _snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in Base.metadata.sorted_tables:
        rows = session.execute(select(table).order_by(*table.primary_key.columns)).mappings()
        tables[table.name] = [
            {key: value for key, value in row.items() if (table.name, key) not in _NOT_EXPORTED}
            for row in rows
        ]
    return tables


def _busy_shop(
    session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    """Every kind of record, including JSON, binary, secret, and practice data."""

    settings = get_settings()
    SettingsService(session, settings.app_secret_key.get_secret_value()).set(
        actor=owner,
        category=SettingCategory.EMAIL,
        key="smtp_password",
        value="smtp-test-secret",
        is_secret=True,
    )
    SettingsService(session, settings.app_secret_key.get_secret_value()).set(
        actor=owner, category=SettingCategory.GENERAL, key="currency", value="PKR"
    )
    ensure_practice_records(session, owner)
    dealer = Dealer(
        name="Hanan",
        business_name='Hanan Spray Center, "Main" Branch',
        phone="0307-6558192",
        address="Baba Market 90/F\nTehsil Hasilpur",
        credit_limit=Decimal("100000.00"),
        balance=Decimal("0.00"),
        is_active=True,
    )
    session.add(dealer)
    session.flush()
    batch = _stock(session, owner, supplier, product, batch_number="RT-1", quantity=40)
    sales = PesticideSaleService(session)
    sales.create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 3, discount=Decimal("12.50")),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("100.00")),),
            dealer_id=dealer.id,
            order_number="0421",
        ),
        owner,
    )
    sales.create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 2),),
            payments=(PaymentInput(PaymentMethod.BANK_TRANSFER, Decimal("300.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    session.add(
        EmailHistory(
            recipient="owner@test.invalid",
            subject="Invoice ü — ñ",
            template="invoice",
            entity_type="Sale",
            entity_id=uuid.uuid4(),
            status=EmailStatus.PENDING,
            body_text="Body",
            attachment_name="INV-1.pdf",
            attachment_data=b"%PDF-1.7\x00\xff binary \r\n bytes",
        )
    )
    session.flush()


def test_export_then_import_restores_every_record(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
    tmp_path: Path,
) -> None:
    _busy_shop(db_session, owner, supplier, product, customer)
    before = _snapshot(db_session)
    settings = get_settings()
    exported = DataExportService(db_session, settings).export(tmp_path, owner)
    db_session.flush()
    # The snapshot was taken before exporting, so the export's own audit entry
    # (written after the archive) is in neither side of the comparison.
    result = DataImportService(db_session, settings).import_archive(
        exported.csv_archive, replace=True, actor=owner
    )
    after = _snapshot(db_session)

    secret_settings = [row for row in before["app_settings"] if row["is_secret"]]
    assert len(secret_settings) == 1 and result.secrets_skipped == 1
    before["app_settings"] = [row for row in before["app_settings"] if not row["is_secret"]]
    imported_audit = [row for row in after["audit_logs"] if row["action"] == "DATA_IMPORTED"]
    assert len(imported_audit) == 1
    after["audit_logs"] = [row for row in after["audit_logs"] if row["action"] != "DATA_IMPORTED"]
    for table in before:
        assert after[table] == before[table], f"{table} did not round-trip"
    assert sum(len(rows) for rows in before.values()) > 30, "the shop really was busy"

    # Nobody keeps an old password: all sign in once with the temporary one.
    assert result.temporary_password is not None
    users = list(db_session.scalars(select(User)))
    assert result.users_reset == len(users) == 1
    assert all(user.must_change_password for user in users)
    assert verify_password(users[0].password_hash, result.temporary_password)
    lines = summarise(result)
    assert result.temporary_password in lines[1]
    assert "SMTP password" in lines[2]


def test_import_refuses_a_database_that_already_has_records(
    db_session: Session, owner: AuthenticatedUser, customer: Customer, tmp_path: Path
) -> None:
    settings = get_settings()
    exported = DataExportService(db_session, settings).export(tmp_path, owner)
    with pytest.raises(ConflictError, match="already holds records"):
        DataImportService(db_session, settings).import_archive(exported.csv_archive, replace=False)
    assert db_session.get(Customer, customer.id) is not None, "nothing was touched"


def _rewrite(archive: Path, target: Path, change: dict[str, str | None]) -> Path:
    """Copy an archive, replacing (or with None, dropping) named members."""

    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(target, "w") as copy:
        for info in source.infolist():
            if info.filename in change:
                replacement = change[info.filename]
                if replacement is not None:
                    copy.writestr(info.filename, replacement)
            else:
                copy.writestr(info, source.read(info))
    return target


def test_a_damaged_export_is_rejected_before_anything_changes(
    db_session: Session, owner: AuthenticatedUser, customer: Customer, tmp_path: Path
) -> None:
    settings = get_settings()
    exported = DataExportService(db_session, settings).export(tmp_path, owner).csv_archive
    importer = DataImportService(db_session, settings)

    missing = _rewrite(exported, tmp_path / "missing.zip", {"customers.csv": None})
    with pytest.raises(ValidationError, match=r"no customers\.csv"):
        importer.import_archive(missing, replace=True)

    with zipfile.ZipFile(exported) as archive:
        rows = list(csv.reader(io.StringIO(archive.read("customers.csv").decode("utf-8-sig"))))
    rows[1][rows[0].index("Id")] = "not-a-uuid"
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    bad_value = _rewrite(exported, tmp_path / "bad.zip", {"customers.csv": buffer.getvalue()})
    with pytest.raises(ValidationError, match=r"customers\.csv line 2, Id"):
        importer.import_archive(bad_value, replace=True)

    not_zip = tmp_path / "notes.csv.zip"
    not_zip.write_text("hello")
    with pytest.raises(ValidationError, match=r"csv\.zip archive"):
        importer.import_archive(not_zip, replace=True)

    # Every failure happened while reading, so the live records are untouched.
    assert db_session.get(Customer, customer.id) is not None


def test_an_export_from_before_the_manifest_still_imports(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
    tmp_path: Path,
) -> None:
    """Older archives: no manifest, JSON as Python repr, bytes as b'...' literals."""

    _busy_shop(db_session, owner, supplier, product, customer)
    settings = get_settings()
    exported = DataExportService(db_session, settings).export(tmp_path, owner).csv_archive
    with zipfile.ZipFile(exported) as archive:
        audit = list(csv.reader(io.StringIO(archive.read("audit_logs.csv").decode("utf-8-sig"))))
        emails = list(
            csv.reader(io.StringIO(archive.read("email_history.csv").decode("utf-8-sig")))
        )
    for column in ("Old value", "New value"):
        index = audit[0].index(column)
        for row in audit[1:]:
            if row[index]:
                row[index] = repr(json.loads(row[index]))
    data = emails[0].index("Attachment data")
    for row in emails[1:]:
        if row[data]:
            row[data] = repr(base64.b64decode(row[data].removeprefix("base64:")))

    def as_csv(rows: list[list[str]]) -> str:
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        return buffer.getvalue()

    # Made before the TEST-record columns existed: no "Is test" anywhere.
    older_tables: dict[str, str | None] = {}
    with zipfile.ZipFile(exported) as archive:
        for name in ("customers.csv", "dealers.csv", "products.csv", "suppliers.csv"):
            rows = list(csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
            drop = rows[0].index("Is test")
            older_tables[name] = as_csv([row[:drop] + row[drop + 1 :] for row in rows])

    legacy = _rewrite(
        exported,
        tmp_path / "legacy.csv.zip",
        {
            "manifest.json": None,
            "audit_logs.csv": as_csv(audit),
            "email_history.csv": as_csv(emails),
            **older_tables,
        },
    )
    email_before = db_session.scalar(
        select(EmailHistory.attachment_data).where(EmailHistory.attachment_name == "INV-1.pdf")
    )
    result = DataImportService(db_session, settings).import_archive(legacy, replace=True)
    assert result.timezone == settings.app_timezone
    assert (
        db_session.scalar(
            select(EmailHistory.attachment_data).where(EmailHistory.attachment_name == "INV-1.pdf")
        )
        == email_before
    )
    # Without the column every record is real, as it was before TEST records existed.
    assert not any(db_session.scalars(select(Product.is_test)))


def test_empty_audit_values_stay_sql_null(
    db_session: Session, owner: AuthenticatedUser, customer: Customer, tmp_path: Path
) -> None:
    from app.models.audit import AuditLog

    settings = get_settings()
    AuditService(db_session).record(
        actor_id=owner.id, action="NO_VALUES", entity_type="Test", entity_id=None
    )
    db_session.flush()
    exported = DataExportService(db_session, settings).export(tmp_path, owner).csv_archive
    DataImportService(db_session, settings).import_archive(exported, replace=True)
    row = db_session.execute(
        select(AuditLog.id).where(AuditLog.action == "NO_VALUES", AuditLog.old_value.is_(None))
    ).first()
    assert row is not None, "an empty value must come back as SQL NULL, not JSON null"


def test_an_archive_with_two_files_of_one_name_is_rejected(
    db_session: Session, owner: AuthenticatedUser, customer: Customer, tmp_path: Path
) -> None:
    settings = get_settings()
    exported = DataExportService(db_session, settings).export(tmp_path, owner).csv_archive
    doubled = tmp_path / "doubled.csv.zip"
    with zipfile.ZipFile(exported) as source, zipfile.ZipFile(doubled, "w") as copy:
        for info in source.infolist():
            copy.writestr(info, source.read(info))
        copy.writestr("old/customers.csv", source.read("customers.csv"))
    with pytest.raises(ValidationError, match="more than one customers"):
        DataImportService(db_session, settings).import_archive(doubled, replace=True)


def test_an_import_gives_up_when_the_database_is_in_use(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Another open copy holding a lock makes the import fail fast, not hang."""

    settings = get_settings()
    with Session(database_engine) as exporting, exporting.begin():
        owner = User(
            username="lock-owner",
            full_name="Lock Owner",
            email="lock@test.invalid",
            password_hash="x",
            role=UserRole.OWNER,
            is_active=True,
        )
        exporting.add(owner)
        exporting.flush()
        archive = (
            DataExportService(exporting, settings)
            .export(tmp_path, AuthenticatedUser.from_model(owner))
            .csv_archive
        )
        exporting.rollback()

    with database_engine.connect() as other, other.begin():
        other.execute(text("LOCK TABLE customers IN ACCESS SHARE MODE"))
        with Session(database_engine) as importing, importing.begin():
            importer = DataImportService(importing, settings, lock_timeout_seconds=1)
            with pytest.raises(ConflictError, match="database is in use"):
                importer.import_archive(archive, replace=True)
