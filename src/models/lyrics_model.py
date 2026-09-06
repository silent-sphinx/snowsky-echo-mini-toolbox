"""Model-View-Controller components for Lyrics Manager."""

import os
from PySide6.QtCore import QAbstractTableModel, QSortFilterProxyModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata
from ..theme import Colours, colours_for_status


class LyricsColumn:
    CHECK = 0
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    STATUS = 4
    REASON = 5
    EMBEDDED = 6
    LRC = 7
    SOURCE = 8
    PREVIEW = 9
    FILE = 10

    COUNT = 11

    HEADERS = [
        "", "Title", "Artist", "Album",
        "Status", "Reason",
        "Embedded", "LRC File", "Source", "Preview",
        "File Path",
    ]

    STATUS_COLUMNS = {STATUS, EMBEDDED, LRC}


class LyricsTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list[TrackMetadata] = []
        self._root_path = ""

    def update_data(self, tracks: list[TrackMetadata], root_path: str) -> None:
        self.beginResetModel()
        self._tracks = [t for t in tracks if t.extension.lower() != ".lrc"]
        self._root_path = root_path
        self._tracks.sort(key=lambda t: t.filepath)
        self.endResetModel()

    def tracks(self) -> list[TrackMetadata]:
        return self._tracks

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return LyricsColumn.COUNT

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        if index.column() == LyricsColumn.CHECK:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False

        if role == Qt.CheckStateRole and index.column() == LyricsColumn.CHECK:
            track = self._tracks[index.row()]
            track.lyrics_is_checked = value in (Qt.Checked, Qt.CheckState.Checked, 2)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True

        return False

    def _status_token_for_cell(self, track: TrackMetadata, col: int) -> str | None:
        if col == LyricsColumn.STATUS:
            return track.lyrics_status
        if col == LyricsColumn.EMBEDDED:
            if track.lyrics_embedded == "Yes":
                return "COMPATIBLE"
            if track.lyrics_embedded == "Error":
                return "INCOMPATIBLE"
            if track.lyrics_embedded == "No":
                return "SKIPPED"
            return None
        if col == LyricsColumn.LRC:
            if track.lyrics_lrc and track.lyrics_lrc != "-":
                return "COMPATIBLE"
            return "INCOMPATIBLE"
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._tracks):
            return None

        track = self._tracks[row]

        if role == Qt.CheckStateRole and col == LyricsColumn.CHECK:
            return Qt.Checked if track.lyrics_is_checked else Qt.Unchecked

        if role == Qt.DisplayRole:
            if col == LyricsColumn.CHECK:
                return ""
            if col == LyricsColumn.TITLE:
                return track.title
            if col == LyricsColumn.ARTIST:
                return track.artist
            if col == LyricsColumn.ALBUM:
                return track.album
            if col == LyricsColumn.STATUS:
                return track.lyrics_status
            if col == LyricsColumn.REASON:
                return track.lyrics_reason
            if col == LyricsColumn.EMBEDDED:
                return track.lyrics_embedded
            if col == LyricsColumn.LRC:
                return track.lyrics_lrc
            if col == LyricsColumn.SOURCE:
                return track.lyrics_source
            if col == LyricsColumn.PREVIEW:
                return track.lyrics_preview
            if col == LyricsColumn.FILE:
                try:
                    return os.path.relpath(track.filepath, self._root_path)
                except Exception:
                    return track.filepath

        if role == Qt.BackgroundRole:
            bg, _ = colours_for_status(self._status_token_for_cell(track, col))
            if bg:
                return QColor(bg)

        if role == Qt.ForegroundRole:
            _, fg = colours_for_status(self._status_token_for_cell(track, col))
            return QColor(fg or Colours.TEXT_PRIMARY)

        if role == Qt.TextAlignmentRole:
            if col in (LyricsColumn.STATUS, LyricsColumn.EMBEDDED, LyricsColumn.LRC):
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ToolTipRole:
            if col == LyricsColumn.STATUS:
                return track.lyrics_reason
            if col == LyricsColumn.REASON:
                return track.lyrics_reason
            if col == LyricsColumn.PREVIEW and track.lyrics_text:
                return track.lyrics_preview
            if col == LyricsColumn.LRC and track.lyrics_lrc not in ("", "-"):
                return f"Matching sidecar: {track.lyrics_lrc}"

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(LyricsColumn.HEADERS):
                return LyricsColumn.HEADERS[section]
        return None

    def total_tracks(self) -> int:
        return len(self._tracks)

    def count_by_status(self, status: str) -> int:
        return sum(1 for t in self._tracks if t.lyrics_status == status)


class LyricsFilterProxyModel(QSortFilterProxyModel):
    STATUS_LABEL_MAP = {
        "compatible": "compatible",
        "embedded only": "incompatible",
        "missing lyrics": "missing",
        "errors": "unknown",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_query = ""
        self._status_filter = ""
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if left.column() == LyricsColumn.CHECK:
            return int(left.data(Qt.CheckStateRole) or 0) < int(right.data(Qt.CheckStateRole) or 0)
        left_val = left.data(Qt.DisplayRole)
        right_val = right.data(Qt.DisplayRole)
        return str(left_val or "").casefold() < str(right_val or "").casefold()

    def set_search_query(self, query: str):
        self._search_query = query.lower()
        self.invalidateFilter()

    def set_status_filter(self, status: str):
        self._status_filter = status.lower()
        self.invalidateFilter()

    def visible_row_count(self) -> int:
        return self.rowCount()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not model:
            return True

        if self._status_filter and self._status_filter != "all statuses":
            expected = self.STATUS_LABEL_MAP.get(self._status_filter, self._status_filter)
            status_index = model.index(source_row, LyricsColumn.STATUS, source_parent)
            status_val = model.data(status_index, Qt.DisplayRole)
            if not status_val or status_val.lower() != expected:
                return False

        if self._search_query:
            row_matches = False
            for col in range(model.columnCount(source_parent)):
                index = model.index(source_row, col, source_parent)
                val = model.data(index, Qt.DisplayRole)
                if val and self._search_query in str(val).lower():
                    row_matches = True
                    break
            if not row_matches:
                return False

        return True
