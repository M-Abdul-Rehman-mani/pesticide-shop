"""Role-aware user creation and account activation management."""

from __future__ import annotations

import uuid
from typing import cast

from PySide6.QtCore import QTimer
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
from sqlalchemy import String, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import UserRole
from app.models.user import User
from app.security.authentication import AuthenticatedUser, AuthenticationService
from app.security.permissions import Permission, has_permission
from app.ui.widgets import (
    RowsTableModel,
    configure_table,
    populate_row_actions,
    record_count_text,
    show_error,
    show_record_details,
    show_success,
)
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
        self._details: list[tuple[object, ...]] = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Users")
        title.setObjectName("PageTitle")
        add = QPushButton("Create User")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search username, name, email, or role")
        self.status_filter = QComboBox()
        self.status_filter.addItems(("All", "Active", "Inactive"))
        toggle = QPushButton("Enable / Disable")
        toggle.setProperty("secondary", True)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search)
        header.addWidget(self.status_filter)
        header.addWidget(toggle)
        header.addWidget(add)
        self.model = RowsTableModel(
            (
                "Username",
                "Full Name",
                "Email",
                "Role",
                "Active",
                "Last Login",
                "Created",
                "Actions",
            ),
            self,
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=1)
        layout.addLayout(header)
        layout.addWidget(self.table)
        self.state_label = QLabel("Loading users…")
        self.state_label.setObjectName("RecordCount")
        layout.addWidget(self.state_label)
        add.clicked.connect(self._add)
        toggle.clicked.connect(self._toggle)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        query, status = self.search.text().strip(), self.status_filter.currentText()
        self.state_label.setText("Loading users…")

        def operation() -> tuple[
            list[tuple[object, ...]], list[uuid.UUID], list[bool], list[tuple[object, ...]]
        ]:
            with self._session_factory() as session:
                statement = select(User)
                if status != "All":
                    statement = statement.where(User.is_active.is_(status == "Active"))
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            User.username.ilike(pattern),
                            User.full_name.ilike(pattern),
                            User.email.ilike(pattern),
                            User.role.cast(String).ilike(pattern),
                        )
                    )
                users = list(session.scalars(statement.order_by(User.username)))
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
                            "",
                        )
                        for user in users
                    ],
                    [user.id for user in users],
                    [user.is_active for user in users],
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
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._active, self._details = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[bool],
                list[tuple[object, ...]],
            ],
            result,
        )
        self.model.set_rows(rows)
        self.state_label.setText(record_count_text(len(rows), "user"))
        populate_row_actions(
            self.table,
            7,
            len(rows),
            (("View", self._view), ("Enable / disable", self._toggle_row)),
        )

    def _add(self) -> None:
        dialog = UserDialog(has_permission(self._actor.role, Permission.MANAGE_OWNER_USERS), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        username = dialog.username.text()
        full_name = dialog.full_name.text()
        email = dialog.email.text()
        password = dialog.password.text()
        role = cast(UserRole, dialog.role.currentData())

        def operation() -> None:
            with self._session_factory.begin() as session:
                AuthenticationService(session).create_user(
                    actor=self._actor,
                    username=username,
                    full_name=full_name,
                    email=email,
                    password=password,
                    role=role,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("User created."),
            failed=lambda error: show_error(self, error),
        )

    def _toggle(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Select user", "Select a user first.")
            return
        row = selected[0].row()
        self._toggle_row(row)

    def _toggle_row(self, row: int) -> None:
        if row >= len(self._ids):
            return
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
            succeeded=lambda _result: self._saved("User status updated."),
            failed=lambda error: show_error(self, error),
        )

    def _saved(self, message: str) -> None:
        show_success(self, message)
        self.refresh()

    def _view(self, row: int) -> None:
        if row >= len(self._details):
            return
        username, full_name, email, role, active, last_login, created = self._details[row]
        show_record_details(
            self,
            str(full_name),
            (
                ("Username", username),
                ("Email", email),
                ("Role", role),
                ("Active", active),
                ("Last login", last_login),
                ("Created", created),
            ),
        )
