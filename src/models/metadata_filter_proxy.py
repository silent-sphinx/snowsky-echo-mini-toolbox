"""
Filter proxy model for the metadata table.

Supports free-text search, missing metadata field filters,
and codec/format filtering.
"""

from PySide6.QtCore import QSortFilterProxyModel, Qt, QModelIndex


class MetadataFilterProxyModel(QSortFilterProxyModel):
    """Proxy model that filters metadata rows by search text and missing-field checkboxes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_query = ""
        self._show_missing_title = False
        self._show_missing_artist = False
        self._show_missing_album = False
        self._codec_filter = ""  # empty = all codecs

        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        from .metadata_table_model import Column

        if left.column() == Column.CHECKBOX:
            return int(left.data(Qt.CheckStateRole) or 0) < int(right.data(Qt.CheckStateRole) or 0)
        left_val = left.data(Qt.DisplayRole)
        right_val = right.data(Qt.DisplayRole)
        return str(left_val or "").casefold() < str(right_val or "").casefold()

    # ── Filter setters ──────────────────────────────────────────────

    def set_search_query(self, query: str) -> None:
        self._search_query = query.lower().strip()
        self.invalidateFilter()

    def set_show_missing_title(self, show: bool) -> None:
        self._show_missing_title = show
        self.invalidateFilter()

    def set_show_missing_artist(self, show: bool) -> None:
        self._show_missing_artist = show
        self.invalidateFilter()

    def set_show_missing_album(self, show: bool) -> None:
        self._show_missing_album = show
        self.invalidateFilter()

    def set_codec_filter(self, codec: str) -> None:
        self._codec_filter = codec.lower().strip()
        self.invalidateFilter()

    # ── Filter logic ────────────────────────────────────────────────

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not model:
            return True

        # Import here to avoid circular imports
        from .metadata_table_model import MISSING_FIELD_ROLE, Column

        # Check missing-field filters (if any are active, show ONLY rows that match)
        any_missing_filter_active = (
            self._show_missing_title or
            self._show_missing_artist or
            self._show_missing_album
        )

        if any_missing_filter_active:
            row_matches_missing = False

            if self._show_missing_title:
                idx = model.index(source_row, Column.TITLE, source_parent)
                if model.data(idx, MISSING_FIELD_ROLE):
                    row_matches_missing = True

            if self._show_missing_artist:
                idx = model.index(source_row, Column.ARTIST, source_parent)
                if model.data(idx, MISSING_FIELD_ROLE):
                    row_matches_missing = True

            if self._show_missing_album:
                idx = model.index(source_row, Column.ALBUM, source_parent)
                if model.data(idx, MISSING_FIELD_ROLE):
                    row_matches_missing = True

            if not row_matches_missing:
                return False

        # Check codec filter
        if self._codec_filter:
            codec_idx = model.index(source_row, Column.CODEC, source_parent)
            codec_val = model.data(codec_idx, Qt.DisplayRole)
            if codec_val and codec_val.lower() != self._codec_filter:
                return False

        # Check search query
        if self._search_query:
            row_matches = False
            for col in range(1, model.columnCount(source_parent)):  # skip checkbox col
                idx = model.index(source_row, col, source_parent)
                val = model.data(idx, Qt.DisplayRole)
                if val and self._search_query in str(val).lower():
                    row_matches = True
                    break
            if not row_matches:
                return False

        return True

    def visible_row_count(self) -> int:
        """Return the number of currently visible (filtered) rows."""
        return self.rowCount()
