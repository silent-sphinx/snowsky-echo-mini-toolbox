"""
High-performance table model for displaying bulk music metadata.

Uses QAbstractTableModel for efficient rendering of large track lists
with custom data roles for status badges, formatting, and alignment.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..demo_data import TrackMetadata
from ..theme import Colours


class Column:
    """Column index constants."""
    CHECKBOX = 0
    TRACK_NUM = 1
    TITLE = 2
    ARTIST = 3
    ALBUM = 4
    GENRE = 5
    YEAR = 6
    DURATION = 7
    CODEC = 8
    BITRATE = 9
    SAMPLE_RATE = 10
    FILE_PATH = 11

    COUNT = 12

    HEADERS = [
        "",          # checkbox
        "#",         # track number
        "Title",
        "Artist",
        "Album",
        "Genre",
        "Year",
        "Duration",
        "Codec",
        "Bitrate",
        "Sample Rate",
        "File Path",
    ]


# Custom data role for passing raw values to delegates
RAW_VALUE_ROLE = Qt.UserRole + 1
MISSING_FIELD_ROLE = Qt.UserRole + 2


class MetadataTableModel(QAbstractTableModel):
    """Table model for bulk music metadata display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list[TrackMetadata] = []
        self._checked: set[int] = set()  # row indices

    def update_data(self, tracks: list[TrackMetadata]) -> None:
        """Replace all data with a new list of tracks."""
        self.beginResetModel()
        self._tracks = list(tracks)
        self._checked.clear()
        self.endResetModel()

    def tracks(self) -> list[TrackMetadata]:
        return self._tracks

    def track_at(self, row: int) -> TrackMetadata | None:
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    # ── Required overrides ──────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return Column.COUNT

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row < 0 or row >= len(self._tracks):
            return None

        track = self._tracks[row]

        # ── Display role ────────────────────────────────────────────
        if role == Qt.DisplayRole:
            if col == Column.CHECKBOX:
                return ""
            elif col == Column.TRACK_NUM:
                return f"{track.track_number:02d}" if track.track_number else "—"
            elif col == Column.TITLE:
                return track.title or "—"
            elif col == Column.ARTIST:
                return track.artist or "—"
            elif col == Column.ALBUM:
                return track.album or "—"
            elif col == Column.GENRE:
                return track.genre or "—"
            elif col == Column.YEAR:
                return str(track.year) if track.year else "—"
            elif col == Column.DURATION:
                mins, secs = divmod(track.duration_seconds, 60)
                return f"{mins}:{secs:02d}"
            elif col == Column.CODEC:
                return track.codec.upper()
            elif col == Column.BITRATE:
                if track.bitrate_kbps:
                    return f"{track.bitrate_kbps} kbps"
                return "Lossless"
            elif col == Column.SAMPLE_RATE:
                khz = track.sample_rate_hz / 1000
                if khz == int(khz):
                    return f"{int(khz)} kHz"
                return f"{khz:.1f} kHz"
            elif col == Column.FILE_PATH:
                return track.file_path

        # ── Raw value role (for delegates) ──────────────────────────
        elif role == RAW_VALUE_ROLE:
            if col == Column.DURATION:
                return track.duration_seconds
            elif col == Column.BITRATE:
                return track.bitrate_kbps
            elif col == Column.SAMPLE_RATE:
                return track.sample_rate_hz
            elif col == Column.CODEC:
                return track.codec

        # ── Missing field role ──────────────────────────────────────
        elif role == MISSING_FIELD_ROLE:
            if col == Column.TITLE:
                return track.title is None
            elif col == Column.ARTIST:
                return track.artist is None
            elif col == Column.ALBUM:
                return track.album is None
            return False

        # ── Checkbox state ──────────────────────────────────────────
        elif role == Qt.CheckStateRole and col == Column.CHECKBOX:
            return Qt.Checked if row in self._checked else Qt.Unchecked

        # ── Foreground colour ───────────────────────────────────────
        elif role == Qt.ForegroundRole:
            # Dim missing values
            if col == Column.TITLE and track.title is None:
                return QColor(Colours.STATUS_MISSING_TEXT)
            elif col == Column.ARTIST and track.artist is None:
                return QColor(Colours.STATUS_MISSING_TEXT)
            elif col == Column.ALBUM and track.album is None:
                return QColor(Colours.STATUS_MISSING_TEXT)
            elif col == Column.TRACK_NUM:
                return QColor(Colours.TEXT_TERTIARY)
            elif col == Column.FILE_PATH:
                return QColor(Colours.TEXT_TERTIARY)
            elif col == Column.CODEC:
                return QColor(Colours.ACCENT)

        # ── Background for missing fields ───────────────────────────
        elif role == Qt.BackgroundRole:
            if col == Column.TITLE and track.title is None:
                return QColor(Colours.STATUS_MISSING + "18")
            elif col == Column.ARTIST and track.artist is None:
                return QColor(Colours.STATUS_MISSING + "18")
            elif col == Column.ALBUM and track.album is None:
                return QColor(Colours.STATUS_MISSING + "18")

        # ── Text alignment ──────────────────────────────────────────
        elif role == Qt.TextAlignmentRole:
            if col in (Column.TRACK_NUM, Column.YEAR, Column.DURATION,
                       Column.BITRATE, Column.SAMPLE_RATE):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            elif col == Column.CODEC:
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        # ── Tooltip ─────────────────────────────────────────────────
        elif role == Qt.ToolTipRole:
            if col == Column.FILE_PATH:
                return track.file_path
            elif col == Column.TITLE and track.title is None:
                return "Missing title metadata"
            elif col == Column.ARTIST and track.artist is None:
                return "Missing artist metadata"
            elif col == Column.ALBUM and track.album is None:
                return "Missing album metadata"

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(Column.HEADERS):
                return Column.HEADERS[section]
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == Column.CHECKBOX:
            row = index.row()
            if value == Qt.Checked:
                self._checked.add(row)
            else:
                self._checked.discard(row)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == Column.CHECKBOX:
            flags |= Qt.ItemIsUserCheckable
        return flags

    # ── Statistics ──────────────────────────────────────────────────

    def total_tracks(self) -> int:
        return len(self._tracks)

    def missing_title_count(self) -> int:
        return sum(1 for t in self._tracks if t.title is None)

    def missing_artist_count(self) -> int:
        return sum(1 for t in self._tracks if t.artist is None)

    def missing_album_count(self) -> int:
        return sum(1 for t in self._tracks if t.album is None)

    def missing_any_count(self) -> int:
        return sum(
            1 for t in self._tracks
            if t.title is None or t.artist is None or t.album is None
        )
