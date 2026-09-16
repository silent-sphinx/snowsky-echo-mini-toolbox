"""Shared fast filtering for the large library tables."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt

from .drive_data import TrackMetadata, relative_track_path


def join_search_haystack(*values: object) -> str:
    """Lowercased blob used for substring search without going through data()."""
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip().lower()
        if text and text != "-":
            parts.append(text)
    return " ".join(parts)


def track_search_haystack(track: TrackMetadata, root_path: str, *extra: object) -> str:
    return join_search_haystack(
        track.title,
        track.artist,
        track.album,
        track.filename,
        track.filepath,
        relative_track_path(track.filepath, root_path),
        *extra,
    )


class FastFilterProxyModel(QSortFilterProxyModel):
    """Filter by a prebuilt haystack and skip a full re-sort on every keystroke."""

    STATUS_LABEL_MAP: dict[str, str] = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_query = ""
        self._choice_filter = ""
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(False)

    def set_search_query(self, query: str) -> None:
        query = query.lower()
        if query == self._search_query:
            return
        self._search_query = query
        self.invalidateFilter()

    def set_status_filter(self, status: str) -> None:
        status = status.lower()
        if status == self._choice_filter:
            return
        self._choice_filter = status
        self.invalidateFilter()

    def set_category_filter(self, category: str) -> None:
        self.set_status_filter(category)

    def visible_row_count(self) -> int:
        return self.rowCount()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if left.column() == 0:
            return int(left.data(Qt.CheckStateRole) or 0) < int(right.data(Qt.CheckStateRole) or 0)
        left_val = left.data(Qt.DisplayRole)
        right_val = right.data(Qt.DisplayRole)
        return str(left_val or "").casefold() < str(right_val or "").casefold()

    def _choice_is_active(self) -> bool:
        choice = self._choice_filter
        return bool(choice) and not choice.startswith("all ")

    def _source_status(self, source_row: int) -> str:
        model = self.sourceModel()
        getter = getattr(model, "filter_status", None)
        if callable(getter):
            return str(getter(source_row) or "")
        return ""

    def _source_haystack(self, source_row: int) -> str:
        model = self.sourceModel()
        getter = getattr(model, "search_haystack", None)
        if callable(getter):
            return getter(source_row) or ""
        return ""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if source_parent.isValid():
            return True
        if self._choice_is_active():
            expected = self.STATUS_LABEL_MAP.get(self._choice_filter, self._choice_filter)
            status = self._source_status(source_row).lower()
            if status != expected:
                return False
        if self._search_query and self._search_query not in self._source_haystack(source_row):
            return False
        return True
