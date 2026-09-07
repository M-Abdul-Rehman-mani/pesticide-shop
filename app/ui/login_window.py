"""Native login and mandatory first-login password change dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app.security.authentication import AuthenticatedUser, AuthenticationService
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker


class LoginDialog(QDialog):
    def __init__(
        self, session_factory: sessionmaker[Session], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._worker: FunctionWorker | None = None
        self.authenticated_user: AuthenticatedUser | None = None
        self.setWindowTitle("Pesticide Shop Manager — Sign in")
        self.setMinimumSize(420, 460)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 24)
        root.setSpacing(8)
        mark = QLabel("C")
        mark.setObjectName("LoginMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(48, 48)
        mark_row = QHBoxLayout()
        mark_row.addStretch()
        mark_row.addWidget(mark)
        mark_row.addStretch()
        heading = QLabel("Welcome to CropCare")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setObjectName("PageTitle")
        subtitle = QLabel("Sign in to manage sales, stock, dealers, and reports")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("PageSubtitle")
        form_card = QFrame()
        form_card.setObjectName("LoginCard")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(18, 16, 18, 16)
        form_layout.setSpacing(8)
        form_title = QLabel("Account details")
        form_title.setObjectName("SectionTitle")
        username_label = QLabel("Username")
        self.username = QLineEdit()
        self.username.setPlaceholderText("Enter your username")
        self.username.setClearButtonEnabled(True)
        password_label = QLabel("Password")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Enter your password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        show_password = QCheckBox("Show password")
        show_password.toggled.connect(
            lambda visible: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        self.sign_in = QPushButton("Sign in")
        self.message = QLabel("")
        self.message.setStyleSheet("color: #b33b32;")
        self.message.setWordWrap(True)
        form_layout.addWidget(form_title)
        form_layout.addWidget(username_label)
        form_layout.addWidget(self.username)
        form_layout.addWidget(password_label)
        form_layout.addWidget(self.password)
        form_layout.addWidget(show_password)
        form_layout.addWidget(self.message)
        form_layout.addWidget(self.sign_in)
        security = QLabel("Your session locks automatically after inactivity.")
        security.setObjectName("RecordCount")
        security.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addLayout(mark_row)
        root.addWidget(heading)
        root.addWidget(subtitle)
        root.addSpacing(14)
        root.addWidget(form_card)
        root.addWidget(security)
        root.addStretch()
        self.sign_in.clicked.connect(self._authenticate)
        self.password.returnPressed.connect(self._authenticate)
        self.username.setFocus()

    def _authenticate(self) -> None:
        username = self.username.text()
        password = self.password.text()
        if not username or not password:
            self.message.setText("Enter your username and password.")
            return
        self.sign_in.setEnabled(False)
        self.message.setText("Signing in…")

        def operation() -> AuthenticatedUser:
            with self._session_factory.begin() as session:
                return AuthenticationService(session).authenticate(username, password)

        self._worker = start_worker(
            operation,
            succeeded=self._authenticated,
            failed=self._failed,
            finished=lambda: self.sign_in.setEnabled(True),
        )

    def _authenticated(self, user: object) -> None:
        assert isinstance(user, AuthenticatedUser)
        self.authenticated_user = user
        self.accept()

    def _failed(self, error: Exception) -> None:
        self.password.clear()
        self.message.clear()
        show_error(self, error)


class ChangePasswordDialog(QDialog):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        user: AuthenticatedUser,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._user = user
        self._worker: FunctionWorker | None = None
        self.setWindowTitle("Change password")
        self.setModal(True)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "You must replace the development or temporary password before continuing."
        )
        explanation.setWordWrap(True)
        form = QFormLayout()
        self.current = QLineEdit()
        self.current.setEchoMode(QLineEdit.EchoMode.Password)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Current password", self.current)
        form.addRow("New password", self.new)
        form.addRow("Confirm password", self.confirm)
        buttons = QHBoxLayout()
        save = QPushButton("Change password")
        cancel = QPushButton("Cancel")
        cancel.setProperty("secondary", True)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addWidget(explanation)
        layout.addLayout(form)
        layout.addLayout(buttons)
        save.clicked.connect(lambda: self._save(save))
        cancel.clicked.connect(self.reject)

    def _save(self, button: QPushButton) -> None:
        if self.new.text() != self.confirm.text():
            show_error(self, ValueError("New password confirmation does not match."))
            return
        current = self.current.text()
        new = self.new.text()
        button.setEnabled(False)

        def operation() -> None:
            with self._session_factory.begin() as session:
                AuthenticationService(session).change_password(
                    actor=self._user, current_password=current, new_password=new
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.accept(),
            failed=lambda error: show_error(self, error),
            finished=lambda: button.setEnabled(True),
        )
