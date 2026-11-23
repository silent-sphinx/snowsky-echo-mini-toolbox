"""Table model for the Various Artists normalisation tool."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models.drive_data import TrackMetadata, relative_track_path
from ..models.table_filter import FastFilterProxyModel, track_search_haystack
from ..theme import Colours, colours_for_status
from ..utils.various_artists import tags_to_normalise


class VariousArtistsColumn:
    CHECK = 0
    TITLE = 1
    ALBUM = 2
    ARTIST = 3
    ALBUM_ARTIST = 4
    FILE = 5

    COUNT = 6

    HEADERS = ["", "Title", "Album", "Artist", "Album Artist", "File Path"]

    # Cells that change are highlighted, keyed by the tag they hold.
    TAG_FOR_COLUMN = {ARTIST: "artist", ALBUM_ARTIST: "albumartist"}


class VariousArtistsTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[TrackMetadata, dict[str, str]]] = []
        self._root_path = ""
        self._haystacks: list[str] = []

    def update_data(self, tracks: list[TrackMetadata], root_path: str, fields: str, target: str) -> None:
        self.beginResetModel()
        self._root_path = root_path
        rows = []
        for track in sorted(tracks, key=lambda t: t.filepath):
            changes = tags_to_normalise(track, fields, target)
            if changes:
                track.va_is_checked = True
                rows.append((track, changes))
        self._rows = rows
        self._haystacks = [
            track_search_haystack(track, root_path, track.album_artist) for track, _ in rows
        ]
        self.endResetModel()

    def tracks(self) -> list[TrackMetadata]:
        return [track for track, _ in self._rows]

    def checked_changes(self) -> list[tuple[TrackMetadata, dict[str, str]]]:
        return [(track, changes) for track, changes in self._rows if track.va_is_checked]

    def total_rows(self) -> int:
        return len(self._rows)

    def search_haystack(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else VariousArtistsColumn.COUNT

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == VariousArtistsColumn.CHECK:
            flags |= Qt.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == VariousArtistsColumn.CHECK:
            track, _ = self._rows[index.row()]
            track.va_is_checked = value in (Qt.Checked, Qt.CheckState.Checked, 2)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None

        col = index.column()
        track, changes = self._rows[index.row()]
        changes_cell = VariousArtistsColumn.TAG_FOR_COLUMN.get(col) in changes

        if role == Qt.CheckStateRole and col == VariousArtistsColumn.CHECK:
            return Qt.Checked if track.va_is_checked else Qt.Unchecked

        if role == Qt.DisplayRole:
            if col == VariousArtistsColumn.TITLE:
                return track.title
            if col == VariousArtistsColumn.ALBUM:
                return track.album
            if col == VariousArtistsColumn.ARTIST:
                return track.artist
            if col == VariousArtistsColumn.ALBUM_ARTIST:
                return track.album_artist or "-"
            if col == VariousArtistsColumn.FILE:
                return relative_track_path(track.filepath, self._root_path)
            return ""

        if role == Qt.BackgroundRole and changes_cell:
            bg, _ = colours_for_status("LIMITED")
            if bg:
                return QColor(bg)

        if role == Qt.ForegroundRole:
            if changes_cell:
                _, fg = colours_for_status("LIMITED")
                if fg:
                    return QColor(fg)
            return QColor(Colours.TEXT_PRIMARY)

        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ToolTipRole and col == VariousArtistsColumn.FILE:
            return track.filepath

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < VariousArtistsColumn.COUNT:
                return VariousArtistsColumn.HEADERS[section]
        return None


class VariousArtistsFilterProxyModel(FastFilterProxyModel):
    pass
