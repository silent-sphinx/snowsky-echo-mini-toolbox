"""
Model-View-Controller components for Metadata Browser.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from ..models.drive_data import DriveDataModel, TrackMetadata, relative_track_path
from ..models.table_filter import FastFilterProxyModel, track_search_haystack
from ..theme import Colours, colours_for_status
from ..utils.metadata_status import (
    is_metadata_track,
    is_missing_album,
    is_missing_artist,
    is_missing_title,
    metadata_status,
)
from ..utils.metadata_writer import save_metadata
from ..utils.tag_normalization import tag_or_empty


class MetaColumn:
    CHECK = 0
    # Core tags — identity of the song
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    ALBUM_ARTIST = 4
    # Release tags
    TRACK = 5
    GENRE = 6
    YEAR = 7
    # Completeness
    STATUS = 8
    REASON = 9
    # Location
    FILE = 10

    COUNT = 11

    HEADERS = [
        "", "Title", "Artist", "Album", "Album Artist",
        "Track", "Genre", "Year",
        "Status", "Reason",
        "File Path",
    ]

    EDITABLE = {TITLE, ARTIST, ALBUM, ALBUM_ARTIST, TRACK, GENRE, YEAR}

    # column -> (writer key, TrackMetadata attribute)
    _EDIT_FIELDS = {
        TITLE: ("title", "title"),
        ARTIST: ("artist", "artist"),
        ALBUM: ("album", "album"),
        ALBUM_ARTIST: ("albumartist", "album_artist"),
        TRACK: ("tracknumber", "track_num"),
        GENRE: ("genre", "genre"),
        YEAR: ("date", "year"),
    }

    _PLACEHOLDERS = {
        "title": "Unknown Title",
        "artist": "Unknown Artist",
        "album": "Unknown Album",
    }


class MetadataTableModel(QAbstractTableModel):
    save_failed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list[TrackMetadata] = []
        self._root_path = ""
        self._data_model: DriveDataModel | None = None
        self._haystacks: list[str] = []

    def update_data(
        self,
        tracks: list[TrackMetadata],
        root_path: str,
        data_model: DriveDataModel | None = None,
    ) -> None:
        self.beginResetModel()
        self._tracks = [t for t in tracks if is_metadata_track(t)]
        self._root_path = root_path
        self._data_model = data_model
        self._tracks.sort(key=lambda t: t.filepath)
        self._rebuild_haystacks()
        self.endResetModel()

    def tracks(self) -> list[TrackMetadata]:
        return self._tracks

    def _haystack_for(self, track: TrackMetadata) -> str:
        status, reason = metadata_status(track)
        return track_search_haystack(
            track,
            self._root_path,
            self._display_tag(track.title),
            self._display_tag(track.artist),
            self._display_tag(track.album),
            self._display_tag(track.album_artist),
            track.track_num,
            track.genre,
            track.year,
            status,
            reason,
        )

    def _rebuild_haystacks(self) -> None:
        self._haystacks = [self._haystack_for(track) for track in self._tracks]

    def search_haystack(self, row: int) -> str:
        if 0 <= row < len(self._haystacks):
            return self._haystacks[row]
        return ""

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return MetaColumn.COUNT

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        if index.column() == MetaColumn.CHECK:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() in MetaColumn.EDITABLE:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False

        track = self._tracks[index.row()]

        if role == Qt.CheckStateRole and index.column() == MetaColumn.CHECK:
            track.meta_is_checked = value in (Qt.Checked, Qt.CheckState.Checked, 2)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True

        if role == Qt.EditRole and index.column() in MetaColumn.EDITABLE:
            return self._save_tag(index.row(), index.column(), str(value or "").strip())

        return False

    def _save_tag(self, row: int, col: int, new_val: str) -> bool:
        track = self._tracks[row]
        field, attr = MetaColumn._EDIT_FIELDS[col]
        current = tag_or_empty(getattr(track, attr, ""))
        if current == new_val:
            return True

        ok, message = save_metadata(track.filepath, {field: new_val or None})
        if not ok:
            self.save_failed.emit(track.filename, message)
            return False

        if self._data_model is not None:
            self._data_model.update_metadata(track.filepath, {field: new_val or None})
        else:
            placeholder = MetaColumn._PLACEHOLDERS.get(field, "")
            setattr(track, attr, new_val or placeholder)

        top_left = self.index(row, 0)
        bottom_right = self.index(row, MetaColumn.COUNT - 1)
        if 0 <= row < len(self._haystacks):
            self._haystacks[row] = self._haystack_for(track)
        self.dataChanged.emit(top_left, bottom_right)
        return True

    def notify_paths_changed(self, filepaths: list[str] | None = None) -> None:
        if not self._tracks:
            return
        if not filepaths:
            self._rebuild_haystacks()
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._tracks) - 1, MetaColumn.COUNT - 1),
            )
            return

        wanted = set(filepaths)
        for row, track in enumerate(self._tracks):
            if track.filepath in wanted:
                self._haystacks[row] = self._haystack_for(track)
                self.dataChanged.emit(
                    self.index(row, 0),
                    self.index(row, MetaColumn.COUNT - 1),
                )

    def _display_tag(self, value: str) -> str:
        return tag_or_empty(value)

    def _status_token_for_cell(self, track: TrackMetadata, col: int) -> str | None:
        if col == MetaColumn.STATUS:
            status, _ = metadata_status(track)
            return status
        if col == MetaColumn.TITLE and is_missing_title(track):
            return "MISSING"
        if col == MetaColumn.ARTIST and is_missing_artist(track):
            return "MISSING"
        if col == MetaColumn.ALBUM and is_missing_album(track):
            return "MISSING"
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._tracks):
            return None

        track = self._tracks[row]
        status, reason = metadata_status(track)

        if role == Qt.CheckStateRole and col == MetaColumn.CHECK:
            return Qt.Checked if track.meta_is_checked else Qt.Unchecked

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == MetaColumn.CHECK:
                return ""
            if col == MetaColumn.TITLE:
                return self._display_tag(track.title)
            if col == MetaColumn.ARTIST:
                return self._display_tag(track.artist)
            if col == MetaColumn.ALBUM:
                return self._display_tag(track.album)
            if col == MetaColumn.ALBUM_ARTIST:
                return self._display_tag(track.album_artist)
            if col == MetaColumn.TRACK:
                return track.track_num
            if col == MetaColumn.GENRE:
                return track.genre
            if col == MetaColumn.YEAR:
                return track.year
            if col == MetaColumn.STATUS:
                return status
            if col == MetaColumn.REASON:
                return reason
            if col == MetaColumn.FILE:
                return relative_track_path(track.filepath, self._root_path)

        if role == Qt.BackgroundRole:
            bg, _ = colours_for_status(self._status_token_for_cell(track, col))
            if bg:
                return QColor(bg)

        if role == Qt.ForegroundRole:
            _, fg = colours_for_status(self._status_token_for_cell(track, col))
            return QColor(fg or Colours.TEXT_PRIMARY)

        if role == Qt.TextAlignmentRole:
            center_cols = (MetaColumn.TRACK, MetaColumn.YEAR, MetaColumn.STATUS)
            if col in center_cols:
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ToolTipRole:
            if col == MetaColumn.STATUS:
                return reason
            if col == MetaColumn.REASON:
                return reason
            if col in MetaColumn.EDITABLE:
                label = MetaColumn.HEADERS[col]
                if self._status_token_for_cell(track, col) == "MISSING":
                    return f"Missing {label.lower()} — double-click to edit"
                return f"Double-click to edit {label.lower()}"
            if col == MetaColumn.FILE:
                return track.filepath

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(MetaColumn.HEADERS):
                return MetaColumn.HEADERS[section]
        return None

    def total_tracks(self) -> int:
        return len(self._tracks)

    def count_by_status(self, status: str) -> int:
        wanted = status.upper()
        return sum(1 for t in self._tracks if metadata_status(t)[0] == wanted)

    def missing_title_count(self) -> int:
        return sum(1 for t in self._tracks if is_missing_title(t))

    def missing_artist_count(self) -> int:
        return sum(1 for t in self._tracks if is_missing_artist(t))

    def missing_album_count(self) -> int:
        return sum(1 for t in self._tracks if is_missing_album(t))


class MetadataFilterProxyModel(FastFilterProxyModel):
    STATUS_LABEL_MAP = {
        "complete": "complete",
        "missing metadata": "missing",
    }

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if source_parent.isValid():
            return True
        model = self.sourceModel()
        tracks = getattr(model, "tracks", lambda: [])()
        track = tracks[source_row] if 0 <= source_row < len(tracks) else None

        if self._choice_is_active() and track is not None:
            if self._choice_filter == "missing title":
                if not is_missing_title(track):
                    return False
            elif self._choice_filter == "missing artist":
                if not is_missing_artist(track):
                    return False
            elif self._choice_filter == "missing album":
                if not is_missing_album(track):
                    return False
            else:
                expected = self.STATUS_LABEL_MAP.get(self._choice_filter, self._choice_filter)
                status, _ = metadata_status(track)
                if status.lower() != expected:
                    return False

        if self._search_query and self._search_query not in self._source_haystack(source_row):
            return False
        return True
