"""Role-aware user creation and account activation management."""

from __future__ import annotations

import uuid
from typing import cast

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import UserRole
from app.models.user import User
from app.security.authentication import AuthenticatedUser, AuthenticationService
from app.security.permissions import Permission, has_permission
from app.ui.widgets import RowsTableModel, show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date


class UserDialog(QDialog):
    def __init__(self, allow_owner: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create User")
        layout = QFormLayout(self)
        self.username = QLineEdit()
        self.full_name = QLineEdit()
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.role = QComboBox()
        for role in UserRole:
            if role is not UserRole.OWNER or allow_owner:
                self.role.addItem(role.value.replace("_", " ").title(), role)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("Username", self.username)
        layout.addRow("Full name", self.full_name)
        layout.addRow("Email", self.email)
        layout.addRow("Temporary password", self.password)
        layout.addRow("Role", self.role)
        layout.addRow(buttons)


class UsersScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        actor: AuthenticatedUser,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._actor = actor
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._active: list[bool] = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Users")
        title.setObjectName("PageTitle")
        add = QPushButton("Create User")
        toggle = QPushButton("Enable / Disable")
        toggle.setProperty("secondary", True)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(toggle)
        header.addWidget(add)
        self.model = RowsTableModel(
            ("Username", "Full Name", "Email", "Role", "Active", "Last Login", "Created"), self
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        layout.addLayout(header)
        layout.addWidget(self.table)
        add.clicked.connect(self._add)
        toggle.clicked.connect(self._toggle)
        self.refresh()

    def refresh(self) -> None:
        def operation() -> tuple[list[tuple[object, ...]], list[uuid.UUID], list[bool]]:
            with self._session_factory() as session:
                users = list(session.scalars(select(User).order_by(User.username)))
                return (
                    [
                        (
                            user.username,
                            user.full_name,
                            user.email,
                            user.role.value,
                            "Yes" if user.is_active else "No",
                            format_date(user.last_login_at),
                            format_date(user.created_at),
                        )
                        for user in users
                    ],
                    [user.id for user in users],
                    [user.is_active for user in users],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._active = cast(
            tuple[list[tuple[object, ...]], list[uuid.UUID], list[bool]], result
        )
        self.model.set_rows(rows)

    def _add(self) -> None:
        dialog = UserDialog(has_permission(self._actor.role, Permission.MANAGE_OWNER_USERS), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                AuthenticationService(session).create_user(
                    actor=self._actor,
                    username=dialog.username.text(),
                    full_name=dialog.full_name.text(),
                    email=dialog.email.text(),
                    password=dialog.password.text(),
                    role=dialog.role.currentData(),
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.refresh(),
            failed=lambda error: show_error(self, error),
        )

    def _toggle(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Select user", "Select a user first.")
            return
        row = selected[0].row()
        new_state = not self._active[row]
        action = "enable" if new_state else "disable"
        if (
            QMessageBox.question(
                self, "Confirm account change", f"Are you sure you want to {action} this user?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                AuthenticationService(session).set_active(
                    actor=self._actor, user_id=self._ids[row], is_active=new_state
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.refresh(),
            failed=lambda error: show_error(self, error),
        )
