"""
Model-View-Controller components for Album Art validation.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata, relative_track_path
from ..models.table_filter import FastFilterProxyModel, track_search_haystack
from ..theme import Colours, colours_for_status
from ..utils.album_art_validation import MAX_ART_DIMENSION


class ArtColumn:
    # Checkbox
    CHECK = 0
    # Track Info
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    # Status
    STATUS = 4
    REASON = 5
    # Artwork
    FORMAT = 6
    SCAN_TYPE = 7
    RESOLUTION = 8
    DATA_SIZE = 9
    SOURCE = 10
    # Validation
    FORMAT_CHECK = 11
    SCAN_CHECK = 12
    RESOLUTION_CHECK = 13
    METADATA = 14
    # Location
    FILE = 15

    COUNT = 16

    HEADERS = [
        "", "Title", "Artist", "Album",
        "Status", "Reason",
        "Format", "Scan Type", "Resolution", "Data Size", "Source",
        "Format Check", "Scan Check", "Resolution Check", "Metadata",
        "File Path"
    ]

    # Columns that use COMPATIBLE/INCOMPATIBLE/UNKNOWN status coloring
    COMPAT_STATUS_COLUMNS = {FORMAT_CHECK, SCAN_CHECK, RESOLUTION_CHECK}


class AlbumArtTableModel(QAbstractTableModel):
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
            track.art_status,
            track.art_reason,
            track.art_format,
            track.art_scan_type,
            track.art_resolution,
            track.art_size,
            track.art_source,
            track.art_format_compat,
            track.art_scan_compat,
            track.art_resolution_compat,
            track.art_metadata_status,
        )

    def search_haystack(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def filter_status(self, row: int) -> str:
        if 0 <= row < len(self._tracks):
            return self._tracks[row].art_status
        return ""

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid(): return 0
        return len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid(): return 0
        return ArtColumn.COUNT

    def _get_compat_status_for_column(self, track: TrackMetadata, col: int) -> str | None:
        """Return the compatibility status string for a given compat-status column."""
        if col == ArtColumn.FORMAT_CHECK:
            return track.art_format_compat
        elif col == ArtColumn.SCAN_CHECK:
            return track.art_scan_compat
        elif col == ArtColumn.RESOLUTION_CHECK:
            return track.art_resolution_compat
        return None

    def _status_token_for_cell(self, track: TrackMetadata, col: int) -> str | None:
        if col == ArtColumn.STATUS:
            return track.art_status
        if col in ArtColumn.COMPAT_STATUS_COLUMNS:
            return self._get_compat_status_for_column(track, col)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        if index.column() == ArtColumn.CHECK:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False

        if role == Qt.CheckStateRole and index.column() == ArtColumn.CHECK:
            track = self._tracks[index.row()]
            track.art_is_checked = (value in (Qt.Checked, Qt.CheckState.Checked, 2))
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True

        return False

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid(): return None

        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._tracks): return None

        track = self._tracks[row]

        if role == Qt.CheckStateRole and col == ArtColumn.CHECK:
            return Qt.Checked if track.art_is_checked else Qt.Unchecked

        if role == Qt.DisplayRole:
            if col == ArtColumn.CHECK: return ""
            elif col == ArtColumn.TITLE: return track.title
            elif col == ArtColumn.ARTIST: return track.artist
            elif col == ArtColumn.ALBUM: return track.album
            elif col == ArtColumn.STATUS: return track.art_status
            elif col == ArtColumn.REASON: return track.art_reason
            elif col == ArtColumn.FORMAT: return track.art_format
            elif col == ArtColumn.SCAN_TYPE: return track.art_scan_type
            elif col == ArtColumn.RESOLUTION: return track.art_resolution
            elif col == ArtColumn.DATA_SIZE: return track.art_size
            elif col == ArtColumn.SOURCE: return track.art_source
            elif col == ArtColumn.FORMAT_CHECK: return track.art_format_compat
            elif col == ArtColumn.SCAN_CHECK: return track.art_scan_compat
            elif col == ArtColumn.RESOLUTION_CHECK: return track.art_resolution_compat
            elif col == ArtColumn.METADATA: return track.art_metadata_status
            elif col == ArtColumn.FILE: return relative_track_path(track.filepath, self._root_path)

        elif role == Qt.BackgroundRole:
            bg, _ = colours_for_status(self._status_token_for_cell(track, col))
            if bg:
                return QColor(bg)

        elif role == Qt.TextAlignmentRole:
            center_cols = (
                ArtColumn.FORMAT, ArtColumn.SCAN_TYPE, ArtColumn.RESOLUTION,
                ArtColumn.DATA_SIZE, ArtColumn.SOURCE,
                ArtColumn.FORMAT_CHECK, ArtColumn.SCAN_CHECK, ArtColumn.RESOLUTION_CHECK,
            )
            if col in center_cols:
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        elif role == Qt.ForegroundRole:
            _, fg = colours_for_status(self._status_token_for_cell(track, col))
            return QColor(fg or Colours.TEXT_PRIMARY)

        elif role == Qt.ToolTipRole:
            # Show full reason text as tooltip
            if col == ArtColumn.STATUS: return track.art_reason
            elif col == ArtColumn.FORMAT_CHECK: return track.art_format_compat_reason
            elif col == ArtColumn.SCAN_CHECK: return track.art_scan_compat_reason
            elif col == ArtColumn.RESOLUTION_CHECK: return track.art_resolution_compat_reason
            elif col == ArtColumn.METADATA:
                if track.art_metadata_status not in ("-", "OK"):
                    return "Artwork lookups need both an artist and an album tag"

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(ArtColumn.HEADERS):
                return ArtColumn.HEADERS[section]
        return None

    # Stats logic
    def total_tracks(self) -> int:
        return len(self._tracks)

    def count_by_status(self, status: str) -> int:
        return sum(1 for t in self._tracks if t.art_status == status)

    def count_oversized(self) -> int:
        return sum(
            1 for t in self._tracks
            if t.art_width > MAX_ART_DIMENSION or t.art_height > MAX_ART_DIMENSION
        )


class AlbumArtFilterProxyModel(FastFilterProxyModel):
    # Combo labels do not always match the raw status token.
    STATUS_LABEL_MAP = {
        "compatible": "compatible",
        "incompatible": "incompatible",
        "missing artwork": "missing",
        "skipped": "skipped",
    }
