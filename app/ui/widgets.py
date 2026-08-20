"""Shared UI components and safe error presentation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox, QVBoxLayout, QWidget

from app.utils.exceptions import ApplicationError

logger = logging.getLogger(__name__)


class MetricCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        title_label = QLabel(title)
        title_label.setObjectName("MetricTitle")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("MetricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


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
