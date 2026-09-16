"""Model-View-Controller components for File Rename."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata, relative_track_path
from ..models.table_filter import FastFilterProxyModel, track_search_haystack
from ..theme import Colours, colours_for_status
from ..utils.file_rename import is_rename_track


class RenameColumn:
    CHECK = 0
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    STATUS = 4
    REASON = 5
    CURRENT = 6
    SUGGESTED = 7
    TRACK_NO = 8
    FILE = 9

    COUNT = 10

    HEADERS = [
        "", "Title", "Artist", "Album",
        "Status", "Reason",
        "Current File", "Suggested File", "Track No",
        "File Path",
    ]

    STATUS_COLUMNS = {STATUS}


class FileRenameTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list[TrackMetadata] = []
        self._root_path = ""
        self._haystacks: list[str] = []

    def update_data(self, tracks: list[TrackMetadata], root_path: str) -> None:
        self.beginResetModel()
        self._tracks = [t for t in tracks if is_rename_track(t)]
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
            track.rename_status,
            track.rename_reason,
            track.rename_current or track.filename,
            track.rename_suggested,
            track.rename_track_no,
        )

    def search_haystack(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def filter_status(self, row: int) -> str:
        if 0 <= row < len(self._tracks):
            return self._tracks[row].rename_status
        return ""

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return RenameColumn.COUNT

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        track = self._tracks[index.row()]
        if index.column() == RenameColumn.CHECK:
            if track.rename_status == "RENAME":
                return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False

        if role == Qt.CheckStateRole and index.column() == RenameColumn.CHECK:
            track = self._tracks[index.row()]
            if track.rename_status != "RENAME":
                return False
            track.rename_is_checked = value in (Qt.Checked, Qt.CheckState.Checked, 2)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True

        return False

    def _status_token_for_cell(self, track: TrackMetadata, col: int) -> str | None:
        if col == RenameColumn.STATUS:
            return track.rename_status
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._tracks):
            return None

        track = self._tracks[row]

        if role == Qt.CheckStateRole and col == RenameColumn.CHECK:
            if track.rename_status != "RENAME":
                return None
            return Qt.Checked if track.rename_is_checked else Qt.Unchecked

        if role == Qt.DisplayRole:
            if col == RenameColumn.CHECK:
                return ""
            if col == RenameColumn.TITLE:
                return track.title
            if col == RenameColumn.ARTIST:
                return track.artist
            if col == RenameColumn.ALBUM:
                return track.album
            if col == RenameColumn.STATUS:
                return track.rename_status
            if col == RenameColumn.REASON:
                return track.rename_reason
            if col == RenameColumn.CURRENT:
                return track.rename_current or track.filename
            if col == RenameColumn.SUGGESTED:
                return track.rename_suggested or "-"
            if col == RenameColumn.TRACK_NO:
                return track.rename_track_no or "-"
            if col == RenameColumn.FILE:
                return relative_track_path(track.filepath, self._root_path)

        if role == Qt.BackgroundRole:
            bg, _ = colours_for_status(self._status_token_for_cell(track, col))
            if bg:
                return QColor(bg)

        if role == Qt.ForegroundRole:
            _, fg = colours_for_status(self._status_token_for_cell(track, col))
            return QColor(fg or Colours.TEXT_PRIMARY)

        if role == Qt.TextAlignmentRole:
            if col in (RenameColumn.STATUS, RenameColumn.TRACK_NO):
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ToolTipRole:
            if col in (RenameColumn.STATUS, RenameColumn.REASON):
                return track.rename_reason
            if col == RenameColumn.CURRENT:
                return track.rename_current or track.filename
            if col == RenameColumn.SUGGESTED:
                return track.rename_suggested
            if col == RenameColumn.FILE:
                return track.filepath

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(RenameColumn.HEADERS):
                return RenameColumn.HEADERS[section]
        return None

    def total_tracks(self) -> int:
        return len(self._tracks)

    def count_by_status(self, status: str) -> int:
        return sum(1 for t in self._tracks if t.rename_status == status)


class FileRenameFilterProxyModel(FastFilterProxyModel):
    STATUS_LABEL_MAP = {
        "needs rename": "rename",
        "matching": "matching",
        "missing metadata": "missing",
        "conflicts": "conflict",
    }
