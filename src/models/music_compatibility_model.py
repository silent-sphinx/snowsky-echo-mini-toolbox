"""
Model-View-Controller components for Music Compatibility.
"""

import os
from PySide6.QtCore import QAbstractTableModel, QSortFilterProxyModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata
from ..theme import Colours, colours_for_status

class CompColumn:
    # Checkbox
    CHECK = 0
    # Track Info
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    # Status
    STATUS = 4
    REASON = 5
    # Properties
    EXTENSION = 6
    CODEC = 7
    SAMPLE_RATE = 8
    BIT_DEPTH = 9
    CHANNELS = 10
    DSD = 11
    BLOCK_SIZE = 12
    STREAMS = 13
    EQ = 14
    # Validation
    CHANNEL_COMPAT = 15
    WAV_CODEC = 16
    DSD_BITDEPTH = 17
    TAG_ENCODING = 18
    TAG_LENGTH = 19
    FILENAME = 20
    METADATA = 21
    # Location
    FILE = 22
    
    COUNT = 23
    
    HEADERS = [
        "", "Title", "Artist", "Album", 
        "Status", "Reason", 
        "Extension", "Codec", "Sample Rate", "Bit Depth", "Channels", "DSD", "Block Size", "Streams", "EQ",
        "Ch. Compat", "WAV Codec", "DSD Bit Depth", "Tag Encoding", "Tag Length", "File Name", "Metadata", 
        "File Path"
    ]

    # Columns that use COMPATIBLE/INCOMPATIBLE/UNKNOWN status coloring
    COMPAT_STATUS_COLUMNS = {
        CHANNEL_COMPAT, DSD_BITDEPTH, WAV_CODEC,
        TAG_ENCODING, TAG_LENGTH, FILENAME, METADATA
    }

class MusicCompatibilityTableModel(QAbstractTableModel):
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
        if parent.isValid(): return 0
        return len(self._tracks)
        
    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid(): return 0
        return CompColumn.COUNT

    def _get_compat_status_for_column(self, track: TrackMetadata, col: int) -> str | None:
        """Return the compatibility status string for a given compat-status column."""
        if col == CompColumn.CHANNEL_COMPAT:
            return track.comp_channel_compat
        elif col == CompColumn.DSD_BITDEPTH:
            return track.comp_dsd_bitdepth
        elif col == CompColumn.WAV_CODEC:
            return track.comp_wav_codec
        elif col == CompColumn.TAG_ENCODING:
            return track.comp_tag_encoding
        elif col == CompColumn.TAG_LENGTH:
            return track.comp_tag_length
        elif col == CompColumn.FILENAME:
            return track.comp_filename
        elif col == CompColumn.METADATA:
            return track.comp_metadata
        return None

    def _status_token_for_cell(self, track: TrackMetadata, col: int) -> str | None:
        if col == CompColumn.STATUS:
            return track.comp_status
        if col == CompColumn.EQ:
            return track.comp_eq
        if col in CompColumn.COMPAT_STATUS_COLUMNS:
            return self._get_compat_status_for_column(track, col)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        if index.column() == CompColumn.CHECK:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable
        
    def setData(self, index: QModelIndex, value: any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
            
        if role == Qt.CheckStateRole and index.column() == CompColumn.CHECK:
            track = self._tracks[index.row()]
            track.is_checked = (value in (Qt.Checked, Qt.CheckState.Checked, 2))
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
            
        return False
        
    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid(): return None
        
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._tracks): return None
        
        track = self._tracks[row]
        
        if role == Qt.CheckStateRole and col == CompColumn.CHECK:
            return Qt.Checked if track.is_checked else Qt.Unchecked
        
        if role == Qt.DisplayRole:
            if col == CompColumn.CHECK: return ""
            elif col == CompColumn.TITLE: return track.title
            elif col == CompColumn.ARTIST: return track.artist
            elif col == CompColumn.ALBUM: return track.album
            elif col == CompColumn.STATUS: return track.comp_status
            elif col == CompColumn.REASON: return track.comp_reason
            elif col == CompColumn.EQ: return track.comp_eq
            elif col == CompColumn.CODEC: return track.comp_codec
            elif col == CompColumn.SAMPLE_RATE: return track.comp_sample_rate
            elif col == CompColumn.BIT_DEPTH: return track.comp_bit_depth
            elif col == CompColumn.CHANNELS: return track.comp_channels
            elif col == CompColumn.CHANNEL_COMPAT: return track.comp_channel_compat
            elif col == CompColumn.EXTENSION: return track.extension
            elif col == CompColumn.BLOCK_SIZE: return track.comp_block_size
            elif col == CompColumn.DSD: return track.comp_dsd_profile
            elif col == CompColumn.DSD_BITDEPTH: return track.comp_dsd_bitdepth
            elif col == CompColumn.WAV_CODEC: return track.comp_wav_codec
            elif col == CompColumn.TAG_ENCODING: return track.comp_tag_encoding
            elif col == CompColumn.TAG_LENGTH: return track.comp_tag_length
            elif col == CompColumn.STREAMS: return track.comp_streams
            elif col == CompColumn.FILENAME: return track.comp_filename
            elif col == CompColumn.METADATA: return track.comp_metadata
            elif col == CompColumn.FILE: return os.path.relpath(track.filepath, self._root_path)
            
        elif role == Qt.BackgroundRole:
            bg, _ = colours_for_status(self._status_token_for_cell(track, col))
            if bg:
                return QColor(bg)

        elif role == Qt.TextAlignmentRole:
            center_cols = (
                CompColumn.SAMPLE_RATE, CompColumn.BIT_DEPTH, CompColumn.CHANNELS,
                CompColumn.BLOCK_SIZE, CompColumn.DSD, CompColumn.STREAMS,
                CompColumn.CHANNEL_COMPAT, CompColumn.DSD_BITDEPTH, CompColumn.WAV_CODEC,
                CompColumn.TAG_ENCODING, CompColumn.TAG_LENGTH,
            )
            if col in center_cols:
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
            
        elif role == Qt.ForegroundRole:
            _, fg = colours_for_status(self._status_token_for_cell(track, col))
            return QColor(fg or Colours.TEXT_PRIMARY)

        elif role == Qt.ToolTipRole:
            # Show full reason text as tooltip
            if col == CompColumn.STATUS: return track.comp_reason
            elif col == CompColumn.EQ:
                if track.comp_eq == "Not EQ Compatible": return "File sample rate or bit depth exceeds EQ limit (192kHz/16-bit)"
            elif col == CompColumn.CHANNEL_COMPAT: return track.comp_channel_compat_reason
            elif col == CompColumn.DSD_BITDEPTH: return track.comp_dsd_bitdepth_reason
            elif col == CompColumn.WAV_CODEC: return track.comp_wav_codec_reason
            elif col == CompColumn.TAG_ENCODING: return track.comp_tag_encoding_reason
            elif col == CompColumn.TAG_LENGTH: return track.comp_tag_length_reason
            elif col == CompColumn.FILENAME: return track.comp_filename_reason
            elif col == CompColumn.METADATA: return track.comp_metadata_reason

        return None
        
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(CompColumn.HEADERS):
                return CompColumn.HEADERS[section]
        return None
        
    # Stats logic
    def total_tracks(self) -> int:
        return len(self._tracks)
        
    def count_by_status(self, status: str) -> int:
        return sum(1 for t in self._tracks if t.comp_status == status)

    def count_by_eq(self, eq_status: str) -> int:
        return sum(1 for t in self._tracks if t.comp_eq == eq_status)


class MusicCompatibilityFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_query = ""
        self._status_filter = ""
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if left.column() == CompColumn.CHECK:
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
            status_index = model.index(source_row, CompColumn.STATUS, source_parent)
            status_val = model.data(status_index, Qt.DisplayRole)
            if not status_val or status_val.lower() != self._status_filter:
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
