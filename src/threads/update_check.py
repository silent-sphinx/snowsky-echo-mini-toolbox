"""Background thread for the startup update check."""

from PySide6.QtCore import QThread, Signal

from ..utils.update_check import check_for_update


class UpdateCheckThread(QThread):
    """Emit update_available only when a newer release exists."""

    update_available = Signal(str, str)

    def run(self) -> None:
        update = check_for_update()
        if update is not None:
            self.update_available.emit(update.version, update.url)
