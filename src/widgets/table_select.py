"""Select or unselect currently visible table rows, skipping filtered and hidden ones."""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QTableView,
    QTableWidget,
)
from shiboken6 import isValid

SELECT_ALL_ROWS_TEXT = "Select All Rows"
UNSELECT_ALL_ROWS_TEXT = "Unselect All Rows"
SELECT_ALL_ROWS_TOOLTIP = (
    "Select every row currently shown in the table. "
    "Filtered and hidden rows are left unchanged."
)
UNSELECT_ALL_ROWS_TOOLTIP = (
    "Clear the selection on every row currently shown in the table. "
    "Filtered and hidden rows are left unchanged."
)


def visible_table_rows(view: QAbstractItemView) -> list[int]:
    """Return model row indexes that are currently shown in ``view``."""
    model = view.model()
    if model is None:
        return []
    is_hidden = getattr(view, "isRowHidden", None)
    rows: list[int] = []
    for row in range(model.rowCount()):
        if callable(is_hidden) and is_hidden(row):
            continue
        rows.append(row)
    return rows


def select_visible_rows(view: QAbstractItemView) -> int:
    """Select all visible rows and tick any enabled checkboxes on those rows."""
    return _apply_visible_rows(view, selected=True)


def unselect_visible_rows(view: QAbstractItemView) -> int:
    """Clear selection and checkboxes on all currently visible rows."""
    return _apply_visible_rows(view, selected=False)


def toggle_visible_rows(view: QAbstractItemView) -> bool:
    """Select or unselect visible rows. Returns True if rows are now selected."""
    if all_visible_rows_selected(view):
        unselect_visible_rows(view)
        return False
    select_visible_rows(view)
    return True


def all_visible_rows_selected(view: QAbstractItemView) -> bool:
    """True when every visible, enabled, checkable row is selected."""
    rows = visible_table_rows(view)
    if not rows:
        return False
    checkbox_state = _all_visible_checkboxes_checked(view, rows)
    if checkbox_state is not None:
        return checkbox_state
    return _all_visible_rows_highlighted(view, rows)


def select_all_rows_button(view: QAbstractItemView, parent=None) -> QPushButton:
    button = QPushButton(SELECT_ALL_ROWS_TEXT, parent)
    button.setMinimumHeight(34)
    button.setToolTip(SELECT_ALL_ROWS_TOOLTIP)
    _controller_for(view).add_button(button)
    return button


def add_select_all_rows_action(menu: QMenu, view: QAbstractItemView):
    controller = _controller_for(view)
    selected = controller.is_all_selected()
    action = menu.addAction(UNSELECT_ALL_ROWS_TEXT if selected else SELECT_ALL_ROWS_TEXT)
    action.setToolTip(UNSELECT_ALL_ROWS_TOOLTIP if selected else SELECT_ALL_ROWS_TOOLTIP)
    action.triggered.connect(controller.toggle)
    return action


def install_select_all_rows(view: QAbstractItemView) -> None:
    """Add a context-menu action that selects or unselects currently visible rows."""
    if getattr(view, "_select_all_rows_helper", None) is not None:
        return
    view._select_all_rows_helper = _SelectAllMenuHelper(view)


def wire_select_all_rows(
    view: QAbstractItemView,
    toolbar=None,
    *,
    owns_menu: bool = True,
) -> QPushButton | None:
    """Attach Select All Rows to a table, and optionally its filter toolbar."""
    if owns_menu:
        install_select_all_rows(view)
    if toolbar is None:
        return None
    button = select_all_rows_button(view)
    toolbar.addWidget(button)
    return button


def attach_select_all_rows_header(layout, view: QAbstractItemView, summary_widget) -> None:
    """Place a Select All Rows button beside a table summary label."""
    row = QHBoxLayout()
    row.addWidget(summary_widget, 1)
    row.addWidget(select_all_rows_button(view), 0, Qt.AlignTop)
    layout.addLayout(row)
    install_select_all_rows(view)


class SelectAllTableView(QTableView):
    """Table view whose Select All action covers visible rows only."""

    def selectAll(self) -> None:
        select_visible_rows(self)


class SelectAllTableWidget(QTableWidget):
    """Table widget whose Select All action covers visible rows only."""

    def selectAll(self) -> None:
        select_visible_rows(self)


class _SelectAllController(QObject):
    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view
        self._buttons: list[QPushButton] = []
        self._model = None
        self._selection = None
        self._updating = False
        self._bind_model()
        self._bind_selection()

    def add_button(self, button: QPushButton) -> None:
        self._buttons.append(button)
        button.clicked.connect(self.toggle)
        self._refresh()

    def is_all_selected(self) -> bool:
        return all_visible_rows_selected(self._view)

    def toggle(self) -> None:
        toggle_visible_rows(self._view)

    def begin_update(self) -> None:
        self._updating = True

    def end_update(self) -> None:
        self._updating = False
        self._refresh()

    def _bind_model(self) -> None:
        model = self._view.model()
        if model is self._model:
            return
        if self._model is not None:
            for signal in (
                self._model.dataChanged,
                self._model.modelReset,
                self._model.layoutChanged,
                self._model.rowsInserted,
                self._model.rowsRemoved,
            ):
                try:
                    signal.disconnect(self._refresh)
                except (TypeError, RuntimeError):
                    pass
        self._model = model
        if model is None:
            return
        model.dataChanged.connect(self._refresh)
        model.modelReset.connect(self._refresh)
        model.layoutChanged.connect(self._refresh)
        model.rowsInserted.connect(self._refresh)
        model.rowsRemoved.connect(self._refresh)

    def _bind_selection(self) -> None:
        selection = self._view.selectionModel()
        if selection is self._selection:
            return
        if self._selection is not None:
            try:
                self._selection.selectionChanged.disconnect(self._refresh)
            except (TypeError, RuntimeError):
                pass
        self._selection = selection
        if selection is not None:
            selection.selectionChanged.connect(self._refresh)

    def _refresh(self, *args) -> None:
        if self._updating:
            return
        if not isValid(self) or not isValid(self._view):
            return
        self._bind_model()
        self._bind_selection()
        selected = self.is_all_selected()
        text = UNSELECT_ALL_ROWS_TEXT if selected else SELECT_ALL_ROWS_TEXT
        tooltip = UNSELECT_ALL_ROWS_TOOLTIP if selected else SELECT_ALL_ROWS_TOOLTIP
        for button in self._buttons:
            button.setText(text)
            button.setToolTip(tooltip)


class _SelectAllMenuHelper(QObject):
    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, pos) -> None:
        menu = QMenu(self._view)
        add_select_all_rows_action(menu, self._view)
        viewport = self._view.viewport()
        origin = viewport if viewport is not None else self._view
        menu.exec(origin.mapToGlobal(pos))


def _controller_for(view: QAbstractItemView) -> _SelectAllController:
    controller = getattr(view, "_select_all_controller", None)
    if controller is None:
        controller = _SelectAllController(view)
        view._select_all_controller = controller
    return controller


def _apply_visible_rows(view: QAbstractItemView, *, selected: bool) -> int:
    model = view.model()
    if model is None:
        return 0
    rows = visible_table_rows(view)
    if not rows:
        return 0

    view.setUpdatesEnabled(False)
    controller = getattr(view, "_select_all_controller", None)
    if controller is not None:
        controller.begin_update()
    try:
        _check_visible_rows(view, rows, checked=selected)
        if selected:
            _highlight_visible_rows(view, rows)
        else:
            _unhighlight_visible_rows(view, rows)
    finally:
        view.setUpdatesEnabled(True)
        if controller is not None:
            controller.end_update()
    return len(rows)


def _all_visible_checkboxes_checked(view: QAbstractItemView, rows: list[int]) -> bool | None:
    """Return True/False if visible rows have checkboxes, otherwise None."""
    found = False
    for row in rows:
        cell = _first_checkable_cell(view, row)
        if cell is None:
            continue
        found = True
        if not _cell_is_checked(view, cell):
            return False
    if not found:
        return None
    return True


def _all_visible_rows_highlighted(view: QAbstractItemView, rows: list[int]) -> bool:
    if view.selectionMode() in (QAbstractItemView.NoSelection, QAbstractItemView.SingleSelection):
        return False
    selection_model = view.selectionModel()
    if selection_model is None:
        return False
    for row in rows:
        if not selection_model.isRowSelected(row):
            return False
    return True


def _first_checkable_cell(view: QAbstractItemView, row: int):
    model = view.model()
    if model is None:
        return None
    widget = view if isinstance(view, QTableWidget) else None
    for column in range(model.columnCount()):
        if widget is not None:
            item = widget.item(row, column)
            if item is None:
                continue
            flags = item.flags()
            if flags & Qt.ItemIsUserCheckable and flags & Qt.ItemIsEnabled:
                return ("item", item)
            continue
        index = model.index(row, column)
        if not index.isValid():
            continue
        flags = model.flags(index)
        if flags & Qt.ItemIsUserCheckable and flags & Qt.ItemIsEnabled:
            return ("index", index)
    return None


def _cell_is_checked(view: QAbstractItemView, cell) -> bool:
    kind, payload = cell
    if kind == "item":
        return payload.checkState() == Qt.Checked
    state = view.model().data(payload, Qt.CheckStateRole)
    return state in (Qt.Checked, Qt.CheckState.Checked, 2)


def _check_visible_rows(view: QAbstractItemView, rows: list[int], *, checked: bool) -> None:
    model = view.model()
    if model is None:
        return
    column_count = model.columnCount()
    state = Qt.Checked if checked else Qt.Unchecked
    state_int = 2 if checked else 0
    widget = view if isinstance(view, QTableWidget) else None
    for row in rows:
        for column in range(column_count):
            if widget is not None:
                item = widget.item(row, column)
                if item is None:
                    continue
                flags = item.flags()
                if flags & Qt.ItemIsUserCheckable and flags & Qt.ItemIsEnabled:
                    item.setCheckState(state)
                    break
                continue
            index = model.index(row, column)
            if not index.isValid():
                continue
            flags = model.flags(index)
            if flags & Qt.ItemIsUserCheckable and flags & Qt.ItemIsEnabled:
                # QAbstractItemModel stores CheckStateRole as an int.
                model.setData(index, state_int, Qt.CheckStateRole)
                break


def _highlight_visible_rows(view: QAbstractItemView, rows: list[int]) -> None:
    mode = view.selectionMode()
    if mode in (QAbstractItemView.NoSelection, QAbstractItemView.SingleSelection):
        return
    model = view.model()
    selection_model = view.selectionModel()
    if model is None or selection_model is None:
        return
    selection_model.select(
        _row_selection(model, rows),
        QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
    )


def _unhighlight_visible_rows(view: QAbstractItemView, rows: list[int]) -> None:
    mode = view.selectionMode()
    if mode in (QAbstractItemView.NoSelection, QAbstractItemView.SingleSelection):
        return
    model = view.model()
    selection_model = view.selectionModel()
    if model is None or selection_model is None:
        return
    selection_model.select(
        _row_selection(model, rows),
        QItemSelectionModel.Deselect | QItemSelectionModel.Rows,
    )


def _row_selection(model, rows: list[int]) -> QItemSelection:
    last_column = max(0, model.columnCount() - 1)
    selection = QItemSelection()
    range_start = previous = rows[0]
    for row in rows[1:]:
        if row == previous + 1:
            previous = row
            continue
        selection.select(
            model.index(range_start, 0),
            model.index(previous, last_column),
        )
        range_start = previous = row
    selection.select(
        model.index(range_start, 0),
        model.index(previous, last_column),
    )
    return selection
