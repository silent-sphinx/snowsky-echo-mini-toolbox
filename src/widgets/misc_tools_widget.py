from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ..models.drive_data import DriveDataModel
from .various_artists_widget import VariousArtistsWidget


class MiscToolsWidget(QWidget):
    """Home for small one-off library tools, each on its own sub-tab."""

    library_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._various_artists = VariousArtistsWidget()
        self._various_artists.library_changed.connect(self.library_changed)

        self._tools = QTabWidget()
        self._tools.addTab(self._various_artists, "Various Artists")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tools)

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._various_artists.populate_data(data_model)

    def set_processing_state(self, is_processing: bool) -> None:
        self._various_artists.set_processing_state(is_processing)

    def cancel_running_job(self) -> None:
        self._various_artists.cancel_running_job()
