"""Shared UI components and safe error presentation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableView,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.utils.exceptions import ApplicationError

logger = logging.getLogger(__name__)


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        *,
        hint: str = "",
        accent: str = "green",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setProperty("accent", accent)
        self.setMinimumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("MetricTitle")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("MetricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("MetricHint")
            layout.addWidget(hint_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class PageHeader(QWidget):
    """Consistent page title, description, and optional right-aligned actions."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        copy.addWidget(self.title_label)
        copy.addWidget(self.subtitle_label)
        layout.addLayout(copy, 1)
        self.action_layout = QHBoxLayout()
        self.action_layout.setSpacing(8)
        layout.addLayout(self.action_layout)

    def add_action(self, widget: QWidget) -> None:
        self.action_layout.addWidget(widget)


def configure_table(
    table: QTableView | QTableWidget,
    *,
    stretch_column: int | None = 0,
    minimum_section_size: int = 86,
    editable: bool = False,
) -> None:
    """Apply readable, keyboard-friendly defaults to a data table."""

    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.SelectedClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
        if editable
        else QAbstractItemView.EditTrigger.NoEditTriggers
    )
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(42)
    header = table.horizontalHeader()
    header.setMinimumSectionSize(minimum_section_size)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    if stretch_column is not None and stretch_column < header.count():
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)


class RowsTableModel(QAbstractTableModel):
    """Read-only table model; screens paginate before assigning rows."""

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._headers = tuple(headers)
        self._rows: list[Sequence[object]] = []

    def set_rows(self, rows: Sequence[Sequence[object]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None
        return str(self._rows[index.row()][index.column()])

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._headers[section]
        return None

    def row(self, index: int) -> Sequence[object]:
        return self._rows[index]


def show_error(parent: QWidget, error: Exception) -> None:
    if isinstance(error, ApplicationError):
        QMessageBox.warning(parent, "Unable to complete operation", error.message)
        return
    error_id = uuid.uuid4().hex[:8].upper()
    logger.error(
        "Unexpected UI operation error %s",
        error_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    QMessageBox.critical(
        parent,
        "Unexpected error",
        f"The operation could not be completed. Reference: {error_id}\n"
        "Technical details were written to the application log.",
    )
