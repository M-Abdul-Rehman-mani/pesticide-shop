"""Shared UI components and safe error presentation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import StrEnum
from typing import TypeVar

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QTableView,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

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
        self.setMinimumHeight(78)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
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


#: Marks a table whose column widths were restored from saved preferences, so
#: automatic sizing leaves that remembered layout alone.
_USER_SIZED = "userSizedColumns"


def mark_columns_user_sized(table: QTableView | QTableWidget) -> None:
    """Record that a table's column widths came from the user's saved layout."""

    table.setProperty(_USER_SIZED, True)


def _fit_columns(table: QTableView | QTableWidget, stretch_column: int | None) -> None:
    """Widen every resizable column to its contents after the data changes.

    Wide tables otherwise keep the header's default section width and clip most
    values to an ellipsis. Sizing is skipped for a table whose widths were
    restored from the user's saved layout.
    """

    if not isValid(table) or table.property(_USER_SIZED):
        # A model can outlive its view during teardown, so confirm the C++ widget
        # is still alive before touching it.
        return
    header = table.horizontalHeader()
    for column in range(header.count()):
        if (
            column != stretch_column
            and header.sectionResizeMode(column) is QHeaderView.ResizeMode.Interactive
        ):
            table.resizeColumnToContents(column)


def configure_table(
    table: QTableView | QTableWidget,
    *,
    stretch_column: int | None = 0,
    minimum_section_size: int = 86,
    editable: bool = False,
    fit_columns: bool = False,
    widget_columns: Mapping[int, int] | None = None,
) -> None:
    """Apply readable, keyboard-friendly defaults to a data table.

    ``widget_columns`` maps a column to the width it needs. A cell holding a widget
    has no item text, so content-based sizing collapses it and clips the spin box or
    button inside; those columns are given an explicit width instead.
    """

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
    table.verticalHeader().setDefaultSectionSize(34)
    header = table.horizontalHeader()
    header.setMinimumSectionSize(minimum_section_size)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    # Sampling keeps content-based sizing cheap on tables of several hundred rows.
    header.setResizeContentsPrecision(60)
    if fit_columns:
        # Narrow reference tables pin every other column to its content so dates
        # and money are never clipped, whatever the data.
        for column in range(header.count()):
            if column != stretch_column:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    if stretch_column is not None and stretch_column < header.count():
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    for column, width in (widget_columns or {}).items():
        if column < header.count():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
    model = table.model()
    if model is not None:
        model.modelReset.connect(lambda: _fit_columns(table, stretch_column))
        _fit_columns(table, stretch_column)


#: An unambiguous date format. The locale default renders "9/8/26", which reads
#: as a different day depending on the machine's regional settings.
DATE_DISPLAY_FORMAT = "dd-MMM-yyyy"


def configure_date_edit(*editors: QDateEdit) -> None:
    """Give date fields a calendar popup and one unambiguous display format."""

    for editor in editors:
        editor.setCalendarPopup(True)
        editor.setDisplayFormat(DATE_DISPLAY_FORMAT)


def wrap_scroll(widget: QWidget) -> QScrollArea:
    """Let dense pages scroll instead of clipping on laptop-height screens."""

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


_EnumT = TypeVar("_EnumT", bound=StrEnum)


def populate_enum_combo(
    combo: QComboBox,
    members: Iterable[_EnumT],
    *,
    current: _EnumT | None = None,
) -> None:
    """Fill a combo with enum members, labelled for display.

    The member's *value* is stored rather than the member itself: Qt keeps item
    data as a variant, and a ``StrEnum`` round-trips through it as a plain string.
    Pair this with :func:`selected_enum` to get the member back.
    """

    for member in members:
        combo.addItem(member.value.replace("_", " ").title(), member.value)
    if current is not None:
        index = combo.findData(current.value)
        if index >= 0:
            combo.setCurrentIndex(index)


def selected_enum(combo: QComboBox, enum_type: type[_EnumT]) -> _EnumT:
    """Return the chosen member, converting Qt's stored string back to the enum."""

    return enum_type(str(combo.currentData()))


def configure_searchable_combo(combo: QComboBox, placeholder: str) -> None:
    """Turn a normal choice list into a type-to-filter selector."""

    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setMaxVisibleItems(14)
    line_edit = combo.lineEdit()
    if line_edit is not None:
        line_edit.setPlaceholderText(placeholder)
        line_edit.setClearButtonEnabled(True)
    completer = combo.completer()
    if completer is not None:
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    if line_edit is not None:
        # Selecting an entry leaves the cursor at the end of a long label, which
        # scrolls the name out of view. Show the start of it instead.
        combo.currentIndexChanged.connect(lambda _index: line_edit.setCursorPosition(0))


def populate_row_actions(
    table: QTableView,
    action_column: int,
    row_count: int,
    actions: Sequence[tuple[str, Callable[[int], None]]],
) -> None:
    """Place a compact three-dot menu in each row of an actions column."""

    for row in range(row_count):
        button = QToolButton(table)
        button.setText("⋮")
        button.setAccessibleName(f"Actions for row {row + 1}")
        button.setToolTip("View or manage this record")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for label, callback in actions:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, selected_row=row, handler=callback: handler(selected_row)
            )
        button.setMenu(menu)
        table.setIndexWidget(table.model().index(row, action_column), button)


def show_record_details(
    parent: QWidget,
    title: str,
    fields: Sequence[tuple[str, object]],
) -> None:
    """Show a simple, readable record details popup."""

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(420)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    for label, value in fields:
        value_label = QLabel(str(value) if value not in (None, "") else "—")
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(label, value_label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addLayout(form)
    layout.addWidget(buttons)
    dialog.exec()


def record_count_text(count: int, singular: str, plural: str | None = None) -> str:
    """Return a record count that reads correctly for one row as well as many."""

    plural = plural or f"{singular}s"
    if not count:
        return f"No {plural} found."
    return f"{count:,} {singular if count == 1 else plural} shown"


def show_success(parent: QWidget, message: str) -> None:
    """Show a consistent, non-blocking success message in the application status bar."""

    window = parent.window()
    if isinstance(window, QMainWindow):
        window.statusBar().showMessage(message, 5000)
    else:
        QMessageBox.information(parent, "Saved", message)


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
