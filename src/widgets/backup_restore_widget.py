from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.drive_data import DriveDataModel
from ..threads.backup_restore import FileTransferWorker, ZipBackupWorker, destination_is_within_source
from .page_chrome import loading_page, page_header


def _format_bytes(size: int) -> str:
    if size < 1024 ** 2:
        return f"{size / 1024:.0f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB"
    return f"{size / (1024 ** 3):.1f} GB"


def _operation_panel(title: str, hint: str) -> tuple[QWidget, QVBoxLayout]:
    panel = QWidget()
    panel.setObjectName("panelSection")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)

    title_lbl = QLabel(title)
    title_lbl.setObjectName("sectionLabel")
    hint_lbl = QLabel(hint)
    hint_lbl.setObjectName("headerSubtitle")
    hint_lbl.setWordWrap(True)
    layout.addWidget(title_lbl)
    layout.addWidget(hint_lbl)
    return panel, layout


class BackupRestoreWidget(QWidget):
    """Zip backup and copy/move of the currently selected target."""

    target_relocated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model: DriveDataModel | None = None
        self._target_path = ""
        self._scanning = False
        self._busy = False
        self._job_label = ""
        self._thread: QThread | None = None
        self._worker = None
        self._progress: QProgressDialog | None = None
        self._user_set_zip_path = False
        self._init_ui()
        self._connect_signals()
        self._update_action_state()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        layout.addWidget(page_header(
            "Backup / Restore",
            "Zip this library, or copy or move it to another folder.",
        ))

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page(
            "Select a target first.",
            "Backup uses the folder or drive you choose at launch.",
        ))

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        self._target_lbl = QLabel()
        self._target_lbl.setObjectName("headerSubtitle")
        self._target_lbl.setWordWrap(True)
        self._target_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        data_layout.addWidget(self._target_lbl)

        zip_panel, zip_layout = _operation_panel(
            "Backup to Zip",
            "Saves a snapshot of the target. The original files stay put.",
        )
        zip_row = QHBoxLayout()
        zip_row.setContentsMargins(0, 0, 0, 0)
        zip_row.setSpacing(8)
        self._zip_input = QLineEdit()
        self._zip_input.setPlaceholderText("Where to save the zip")
        self._zip_input.setMinimumHeight(34)
        self._zip_browse_btn = QPushButton("Browse")
        self._zip_browse_btn.setMinimumHeight(34)
        self._zip_run_btn = QPushButton("Backup")
        self._zip_run_btn.setObjectName("accentButton")
        self._zip_run_btn.setMinimumHeight(34)
        self._zip_run_btn.setEnabled(False)
        zip_row.addWidget(self._zip_input, 1)
        zip_row.addWidget(self._zip_browse_btn)
        zip_row.addWidget(self._zip_run_btn)
        zip_layout.addLayout(zip_row)
        data_layout.addWidget(zip_panel)

        transfer_panel, transfer_layout = _operation_panel(
            "Copy or Move",
            "Creates a folder named after the target inside the destination. Move deletes the original afterwards.",
        )
        transfer_row = QHBoxLayout()
        transfer_row.setContentsMargins(0, 0, 0, 0)
        transfer_row.setSpacing(8)
        self._transfer_input = QLineEdit()
        self._transfer_input.setPlaceholderText("Destination folder")
        self._transfer_input.setMinimumHeight(34)
        self._transfer_browse_btn = QPushButton("Browse")
        self._transfer_browse_btn.setMinimumHeight(34)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setMinimumHeight(34)
        self._copy_btn.setEnabled(False)
        self._move_btn = QPushButton("Move")
        self._move_btn.setMinimumHeight(34)
        self._move_btn.setEnabled(False)
        transfer_row.addWidget(self._transfer_input, 1)
        transfer_row.addWidget(self._transfer_browse_btn)
        transfer_row.addWidget(self._copy_btn)
        transfer_row.addWidget(self._move_btn)
        transfer_layout.addLayout(transfer_row)
        data_layout.addWidget(transfer_panel)

        data_layout.addStretch(1)
        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        self._zip_browse_btn.clicked.connect(self._choose_zip_path)
        self._zip_run_btn.clicked.connect(self._start_zip_backup)
        self._zip_input.textChanged.connect(self._on_zip_path_edited)
        self._transfer_browse_btn.clicked.connect(self._choose_transfer_destination)
        self._copy_btn.clicked.connect(lambda: self._start_copy_or_move("copy"))
        self._move_btn.clicked.connect(lambda: self._start_copy_or_move("move"))
        self._transfer_input.textChanged.connect(self._update_action_state)

    def _source_path(self) -> Path | None:
        if not self._target_path:
            return None
        path = Path(self._target_path).expanduser()
        if not path.exists() or not path.is_dir():
            return None
        try:
            return path.resolve()
        except Exception:
            return path

    def _on_zip_path_edited(self, _text: str = "") -> None:
        self._user_set_zip_path = bool(self._zip_input.text().strip())
        self._update_action_state()

    def _suggested_zip_path(self) -> str:
        source = self._source_path()
        name = source.name if source is not None else "target"
        return str(Path.home() / f"{name}_backup.zip")

    def _choose_zip_path(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Choose Zip Backup Destination",
            self._zip_input.text().strip() or self._suggested_zip_path(),
            "Zip archives (*.zip)",
        )
        if selected:
            self._zip_input.setText(selected)
            self._user_set_zip_path = True
            self._update_action_state()

    def _choose_transfer_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Copy/Move Destination",
            self._transfer_input.text().strip() or str(Path.home()),
        )
        if selected:
            self._transfer_input.setText(selected)
            self._update_action_state()

    def _update_action_state(self) -> None:
        has_target = self._source_path() is not None
        idle = has_target and not self._busy
        can_transfer = idle and bool(self._transfer_input.text().strip())
        self._zip_browse_btn.setEnabled(not self._busy)
        self._transfer_browse_btn.setEnabled(not self._busy)
        self._zip_input.setEnabled(not self._busy)
        self._transfer_input.setEnabled(not self._busy)
        self._zip_run_btn.setEnabled(idle and bool(self._zip_input.text().strip()))
        self._copy_btn.setEnabled(can_transfer)
        self._move_btn.setEnabled(can_transfer)
        self._update_status()

    def _update_status(self) -> None:
        if not self._target_path:
            self._target_lbl.setText("No target selected.")
            return
        if self._busy:
            self._target_lbl.setText(self._job_label or "Working…")
            return
        detail = self._target_path
        if self._data_model is not None:
            detail = f"{self._target_path}  ·  {_format_bytes(self._data_model.total_size_bytes)}"
        elif self._scanning:
            detail = f"{self._target_path}  ·  scan in progress"
        self._target_lbl.setText(detail)

    def _start_zip_backup(self) -> None:
        source_path = self._source_path()
        if source_path is None:
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive target before starting a zip backup.",
            )
            return

        zip_text = self._zip_input.text().strip()
        if not zip_text:
            QMessageBox.information(
                self,
                "Zip Path Required",
                "Choose where to save the backup zip before starting.",
            )
            return

        zip_path = Path(zip_text).expanduser()
        if zip_path.suffix.lower() != ".zip":
            zip_path = zip_path.with_suffix(".zip")
            self._zip_input.setText(str(zip_path))

        if destination_is_within_source(source_path, zip_path.parent):
            QMessageBox.warning(
                self,
                "Invalid Zip Destination",
                "Choose a zip destination outside the source folder to avoid recursive backup data.",
            )
            return

        if zip_path.exists():
            overwrite = QMessageBox.question(
                self,
                "Overwrite Existing Zip",
                f"{zip_path.name} already exists. Overwrite it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if overwrite != QMessageBox.Yes:
                return

        confirm = QMessageBox.question(
            self,
            "Confirm Zip Backup",
            f"Create a zip backup of:\n{source_path}\n\nDestination:\n{zip_path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        worker = ZipBackupWorker(source_path, zip_path)
        worker.finished.connect(self._on_zip_finished)
        self._run_worker(worker, "Creating zip backup...", 0)

    def _start_copy_or_move(self, mode: str = "copy") -> None:
        source_path = self._source_path()
        if source_path is None:
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive target before starting copy or move.",
            )
            return

        destination_root_text = self._transfer_input.text().strip()
        if not destination_root_text:
            QMessageBox.information(
                self,
                "Destination Required",
                "Choose a destination folder or drive before starting copy/move.",
            )
            return

        destination_root = Path(destination_root_text).expanduser()
        destination_target = destination_root / source_path.name
        mode = "move" if mode == "move" else "copy"
        mode_label = "Move" if mode == "move" else "Copy"

        if destination_is_within_source(source_path, destination_target):
            QMessageBox.warning(
                self,
                "Invalid Destination",
                "Destination cannot be the source folder, or a folder inside it.",
            )
            return

        if destination_target.exists() and not destination_target.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Destination",
                "Destination target exists but is not a folder.",
            )
            return

        if destination_target.exists():
            merge = QMessageBox.question(
                self,
                "Destination Already Exists",
                f"{destination_target} already exists. Continue and merge into this folder?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if merge != QMessageBox.Yes:
                return

        confirm = QMessageBox.question(
            self,
            f"Confirm {mode_label}",
            (
                f"{mode_label} source folder:\n{source_path}\n\nTo destination:\n{destination_target}"
                + ("\n\nMove deletes the original files after they have been copied." if mode == "move" else "")
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        worker = FileTransferWorker(source_path, destination_target, mode)
        worker.finished.connect(self._on_transfer_finished)
        self._run_worker(worker, f"{mode_label} in progress...", 0)

    def _run_worker(self, worker, label: str, total: int) -> None:
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "Job Running", "A backup/restore job is already running.")
            return

        progress = QProgressDialog(label, "Cancel", 0, max(total, 1), self)
        progress.setWindowTitle("Backup / Restore")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._progress = progress
        self._busy = True
        self._job_label = label
        self._update_action_state()

        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)

        self._thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_refs)

        progress.canceled.connect(self._cancel_job)
        self._thread.start()

    def _cancel_job(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
        if self._progress is not None:
            self._progress.setLabelText("Cancelling backup/restore job...")

    def _disconnect_progress(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_progress)
            except (RuntimeError, TypeError):
                pass

    def _clear_refs(self) -> None:
        self._worker = None
        self._thread = None

    def _finish_job_ui(self) -> None:
        self._busy = False
        self._job_label = ""
        self._disconnect_progress()
        progress = self._progress
        self._progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_action_state()

    @Slot(int, int, str)
    def _on_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            prefix = self._job_label or "Working..."
            progress.setLabelText(f"{prefix} {processed}/{total}: {detail}")
        except RuntimeError:
            self._progress = None

    @Slot(object)
    def _on_zip_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_job_ui()
        zip_path = str(payload.get("zip_path") or "")
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        skipped = int(payload.get("skipped") or 0)
        QMessageBox.information(
            self,
            "Zip Backup Completed",
            f"Zip created at:\n{zip_path}\n\nFiles archived: {processed}/{total}\nSymlink files skipped: {skipped}",
        )

    @Slot(object)
    def _on_transfer_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        destination = str(payload.get("destination") or "")
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        skipped = int(payload.get("skipped") or 0)
        mode = str(payload.get("mode") or "copy").lower()
        mode_label = "Move" if mode == "move" else "Copy"
        self._finish_job_ui()
        QMessageBox.information(
            self,
            f"{mode_label} Completed",
            f"Destination:\n{destination}\n\nFiles processed: {processed}/{total}\nSymlink files skipped: {skipped}",
        )
        if mode == "move" and destination:
            self.target_relocated.emit(destination)

    @Slot(str)
    def _on_failed(self, error: str) -> None:
        self._finish_job_ui()
        QMessageBox.warning(self, "Backup/Restore Failed", error)

    @Slot(object)
    def _on_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        self._finish_job_ui()
        QMessageBox.information(
            self,
            "Backup/Restore Cancelled",
            (
                f"Cancelled after {processed}/{total} files processed.\n\n"
                "A zip job removes a partial archive. Copy/Move may leave a partial destination folder."
            ),
        )

    def set_target(self, path: str) -> None:
        self._target_path = path or ""
        if self._target_path:
            self._stack.setCurrentIndex(1)
            if not self._user_set_zip_path:
                self._zip_input.blockSignals(True)
                self._zip_input.setText(self._suggested_zip_path())
                self._zip_input.blockSignals(False)
        else:
            self._data_model = None
            self._stack.setCurrentIndex(0)
        self._update_action_state()

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        self._target_path = data_model.root_path
        self._stack.setCurrentIndex(1)
        if not self._user_set_zip_path:
            self._zip_input.blockSignals(True)
            self._zip_input.setText(self._suggested_zip_path())
            self._zip_input.blockSignals(False)
        self._update_action_state()

    def set_processing_state(self, is_processing: bool) -> None:
        self._scanning = is_processing
        if self._target_path:
            self._stack.setCurrentIndex(1)
        else:
            self._stack.setCurrentIndex(0)
        self._update_action_state()
