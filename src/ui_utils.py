from PySide6.QtWidgets import QProgressDialog
from PySide6.QtCore import Qt

def create_progress_dialog(
    title: str,
    label: str,
    maximum: int,
    parent=None,
    cancel_text: str = "Cancel",
    modality: Qt.WindowModality = Qt.ApplicationModal
) -> QProgressDialog:
    """
    Universally standardizes the creation of progress dialogs across the project.
    """
    progress = QProgressDialog(label, cancel_text, 0, maximum, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(modality)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setValue(0)
    return progress
