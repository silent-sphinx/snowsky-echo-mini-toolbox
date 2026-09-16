"""Model-View-Controller components for File Cleanup."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.table_filter import FastFilterProxyModel, join_search_haystack
from ..theme import Colours, colours_for_status
from ..utils.file_cleanup import (
    CATEGORY_INDEX,
    CleanupTypeStats,
    format_bytes,
    sort_cleanup_rows,
    tooltip_for_row,
)


class CleanupColumn:
    CHECK = 0
    EXTENSION = 1
    CATEGORY = 2
    DESCRIPTION = 3
    COUNT = 4
    SIZE = 5

    COUNT_COLUMNS = 6

    HEADERS = [
        "", "Extension", "Category", "Description", "Files", "Total Size",
    ]


class FileCleanupTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[CleanupTypeStats] = []
        self._haystacks: list[str] = []

    def update_data(self, rows: list[CleanupTypeStats]) -> None:
        self.beginResetModel()
        self._rows = sort_cleanup_rows(list(rows))
        self._haystacks = [
            join_search_haystack(
                row.file_type,
                row.category,
                row.description,
                row.count,
                format_bytes(row.size_bytes),
            )
            for row in self._rows
        ]
        self.endResetModel()

    def rows(self) -> list[CleanupTypeStats]:
        return self._rows

    def search_haystack(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def filter_status(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return self._rows[row].category
        return ""

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return CleanupColumn.COUNT_COLUMNS

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        if index.column() == CleanupColumn.CHECK:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == CleanupColumn.CHECK:
            row = self._rows[index.row()]
            row.is_checked = value in (Qt.Checked, Qt.CheckState.Checked, 2)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        row_index = index.row()
        col = index.column()
        if row_index < 0 or row_index >= len(self._rows):
            return None

        row = self._rows[row_index]

        if role == Qt.CheckStateRole and col == CleanupColumn.CHECK:
            return Qt.Checked if row.is_checked else Qt.Unchecked

        if role == Qt.DisplayRole:
            if col == CleanupColumn.CHECK:
                return ""
            if col == CleanupColumn.EXTENSION:
                return row.file_type
            if col == CleanupColumn.CATEGORY:
                return row.category
            if col == CleanupColumn.DESCRIPTION:
                return row.description
            if col == CleanupColumn.COUNT:
                return row.count
            if col == CleanupColumn.SIZE:
                return format_bytes(row.size_bytes)

        if role == Qt.UserRole:
            if col == CleanupColumn.SIZE:
                return row.size_bytes
            if col == CleanupColumn.COUNT:
                return row.count
            if col == CleanupColumn.CATEGORY:
                return CATEGORY_INDEX.get(row.category, 999)
            return row.key

        if role == Qt.BackgroundRole:
            bg, _ = colours_for_status(row.category)
            if bg and col in (CleanupColumn.CATEGORY, CleanupColumn.EXTENSION):
                return QColor(bg)

        if role == Qt.ForegroundRole:
            _, fg = colours_for_status(row.category)
            if fg and col in (CleanupColumn.CATEGORY, CleanupColumn.EXTENSION):
                return QColor(fg)
            return QColor(Colours.TEXT_PRIMARY)

        if role == Qt.TextAlignmentRole:
            if col in (CleanupColumn.CATEGORY, CleanupColumn.COUNT, CleanupColumn.SIZE):
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ToolTipRole:
            hidden_tip = tooltip_for_row(row.category, row.file_type)
            if col in (CleanupColumn.EXTENSION, CleanupColumn.CATEGORY, CleanupColumn.DESCRIPTION):
                return hidden_tip or row.description
            if col == CleanupColumn.SIZE:
                return format_bytes(row.size_bytes)
            if col == CleanupColumn.COUNT:
                noun = "file" if row.count == 1 else "files"
                return f"{row.count:,} {noun}"

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(CleanupColumn.HEADERS):
                return CleanupColumn.HEADERS[section]
        return None

    def total_files(self) -> int:
        return sum(row.count for row in self._rows)

    def total_bytes(self) -> int:
        return sum(row.size_bytes for row in self._rows)

    def type_count(self) -> int:
        return len(self._rows)

    def count_by_category(self, category: str) -> int:
        return sum(row.count for row in self._rows if row.category == category)

    def bytes_by_category(self, category: str) -> int:
        return sum(row.size_bytes for row in self._rows if row.category == category)


class FileCleanupFilterProxyModel(FastFilterProxyModel):
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        if col == CleanupColumn.CHECK:
            return int(left.data(Qt.CheckStateRole) or 0) < int(right.data(Qt.CheckStateRole) or 0)
        if col in (CleanupColumn.COUNT, CleanupColumn.SIZE, CleanupColumn.CATEGORY):
            return int(left.data(Qt.UserRole) or 0) < int(right.data(Qt.UserRole) or 0)
        left_val = left.data(Qt.DisplayRole)
        right_val = right.data(Qt.DisplayRole)
        return str(left_val or "").casefold() < str(right_val or "").casefold()
