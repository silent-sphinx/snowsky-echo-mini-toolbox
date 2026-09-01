"""
Model-View-Controller components for Music Compatibility.
"""

import os
from PySide6.QtCore import QAbstractTableModel, QSortFilterProxyModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata
from ..theme import Colours

class CompColumn:
    TITLE = 0
    ARTIST = 1
    ALBUM = 2
    STATUS = 3
    REASON = 4
    EQ = 5
    CODEC = 6
    SAMPLE_RATE = 7
    BIT_DEPTH = 8
    CHANNELS = 9
    EXTENSION = 10
    BLOCK_SIZE = 11
    DSD = 12
    STREAMS = 13
    FILENAME = 14
    METADATA = 15
    FILE = 16
    
    COUNT = 17
    
    HEADERS = [
        "Title", "Artist", "Album", "Status", "Reason", "EQ", "Codec", "Sample Rate",
        "Bit Depth", "Channels", "Extension", "Block Size", "DSD",
        "Streams", "File Name", "Metadata", "File Path"
    ]

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
        
    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid(): return None
        
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._tracks): return None
        
        track = self._tracks[row]
        
        if role == Qt.DisplayRole:
            if col == CompColumn.TITLE: return track.title
            elif col == CompColumn.ARTIST: return track.artist
            elif col == CompColumn.ALBUM: return track.album
            elif col == CompColumn.STATUS: return track.comp_status
            elif col == CompColumn.REASON: return track.comp_reason
            elif col == CompColumn.EQ: return track.comp_eq
            elif col == CompColumn.CODEC: return track.comp_codec
            elif col == CompColumn.SAMPLE_RATE: return track.comp_sample_rate
            elif col == CompColumn.BIT_DEPTH: return track.comp_bit_depth
            elif col == CompColumn.CHANNELS: return track.comp_channels
            elif col == CompColumn.EXTENSION: return track.extension
            elif col == CompColumn.BLOCK_SIZE: return track.comp_block_size
            elif col == CompColumn.DSD: return track.comp_dsd_profile
            elif col == CompColumn.STREAMS: return track.comp_streams
            elif col == CompColumn.FILENAME: return track.comp_filename
            elif col == CompColumn.METADATA: return track.comp_metadata
            elif col == CompColumn.FILE: return os.path.relpath(track.filepath, self._root_path)
            
        elif role == Qt.BackgroundRole:
            c_green = QColor("#2E7D32")
            c_red = QColor("#7A2C2C")
            c_yellow = QColor("#998A00")
            c_gray = QColor("#3C3C3C")
            
            if col == CompColumn.STATUS:
                if track.comp_status == "SUPPORTED": return c_green
                elif track.comp_status == "LIMITED": return c_yellow
                elif track.comp_status in ("UNSUPPORTED", "UNKNOWN"): return c_red
                elif track.comp_status == "SKIPPED": return c_gray
            elif col == CompColumn.EQ:
                if track.comp_eq == "EQ Compatible": return c_green
                elif track.comp_eq == "Not EQ Compatible": return c_red
                elif track.comp_eq == "UNKNOWN": return c_yellow
            elif col == CompColumn.FILENAME:
                if track.comp_filename == "COMPATIBLE": return c_green
                elif track.comp_filename == "INCOMPATIBLE": return c_red
            elif col == CompColumn.METADATA:
                if track.comp_metadata == "COMPATIBLE": return c_green
                elif track.comp_metadata == "INCOMPATIBLE": return c_red
                
        elif role == Qt.TextAlignmentRole:
            if col in (CompColumn.SAMPLE_RATE, CompColumn.BIT_DEPTH, CompColumn.CHANNELS, CompColumn.BLOCK_SIZE, CompColumn.DSD, CompColumn.STREAMS):
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
            
        elif role == Qt.ForegroundRole:
            if role == Qt.BackgroundRole: pass # handled above
            # Return black text for yellow backgrounds so it's readable, white otherwise
            if col == CompColumn.STATUS and track.comp_status == "LIMITED": return QColor(Colours.BACKGROUND_PRIMARY)
            if col == CompColumn.EQ and track.comp_eq == "UNKNOWN": return QColor(Colours.BACKGROUND_PRIMARY)
            return QColor(Colours.TEXT_PRIMARY)
            
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
