"""Model-View-Controller components for Lyrics Manager."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata, relative_track_path
from ..models.table_filter import FastFilterProxyModel, track_search_haystack
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
        self._haystacks: list[str] = []

    def update_data(self, tracks: list[TrackMetadata], root_path: str) -> None:
        self.beginResetModel()
        self._tracks = [t for t in tracks if t.extension.lower() != ".lrc"]
        self._root_path = root_path
        self._tracks.sort(key=lambda t: t.filepath)
        self._haystacks = [self._haystack_for(track) for track in self._tracks]
        self.endResetModel()

    def tracks(self) -> list[TrackMetadata]:
        return self._tracks

    def _haystack_for(self, track: TrackMetadata) -> str:
        return track_search_haystack(
            track,
            self._root_path,
            track.lyrics_status,
            track.lyrics_reason,
            track.lyrics_embedded,
            track.lyrics_lrc,
            track.lyrics_source,
            track.lyrics_preview,
        )

    def search_haystack(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def filter_status(self, row: int) -> str:
        if 0 <= row < len(self._tracks):
            return self._tracks[row].lyrics_status
        return ""

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
                return relative_track_path(track.filepath, self._root_path)

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


class LyricsFilterProxyModel(FastFilterProxyModel):
    STATUS_LABEL_MAP = {
        "compatible": "compatible",
        "embedded only": "incompatible",
        "missing lyrics": "missing",
        "errors": "unknown",
    }
