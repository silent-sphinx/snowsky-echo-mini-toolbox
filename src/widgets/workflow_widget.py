from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..models.drive_data import DriveDataModel
from ..models.workflow import (
    CONFIGURABLE_STEPS,
    WORKFLOW_ACTIONS,
    Workflow,
    WorkflowManager,
    WorkflowStep,
    describe_step,
    new_workflow,
)
from ..threads.workflow_executor import WorkflowExecutionWorker
from .page_chrome import page_header
from .workflow_dialogs import WorkflowLyricsReviewDialog, configure_workflow_step

_LIST_STYLE = """
    QListWidget::item {
        padding: 8px 10px;
    }
"""


def _panel(title: str) -> tuple[QWidget, QVBoxLayout]:
    panel = QWidget()
    panel.setObjectName("panelSection")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    title_lbl = QLabel(title)
    title_lbl.setObjectName("sectionLabel")
    layout.addWidget(title_lbl)
    return panel, layout


class WorkflowWidget(QWidget):
    """Create, edit, and run saved workflows against the current target."""

    needs_rescan = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = WorkflowManager()
        self._data_model: DriveDataModel | None = None
        self._target_path = ""
        self._scanning = False
        self._busy = False
        self._current: Workflow | None = None
        self._thread: QThread | None = None
        self._worker: WorkflowExecutionWorker | None = None
        self._progress: QProgressDialog | None = None
        self._init_ui()
        self._load_workflow_list()
        self._update_editor_state()
        self._update_run_state()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        self._run_btn = QPushButton("Run Workflow")
        self._run_btn.setObjectName("accentButton")
        self._run_btn.setMinimumHeight(34)
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run_current_workflow)

        layout.addWidget(page_header(
            "Workflows",
            "Save a sequence of library jobs and run them against the current target in one pass.",
            [self._run_btn],
        ))

        self._status_lbl = QLabel()
        self._status_lbl.setObjectName("headerSubtitle")
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        splitter = QSplitter(Qt.Horizontal)

        left_panel, left_layout = _panel("Saved Workflows")
        self._workflow_list = QListWidget()
        self._workflow_list.setStyleSheet(_LIST_STYLE)
        self._workflow_list.itemSelectionChanged.connect(self._on_workflow_selected)
        left_layout.addWidget(self._workflow_list, 1)

        list_btns = QHBoxLayout()
        list_btns.setContentsMargins(0, 0, 0, 0)
        list_btns.setSpacing(8)
        self._add_workflow_btn = QPushButton("New")
        self._add_workflow_btn.setMinimumHeight(34)
        self._add_workflow_btn.clicked.connect(self._add_workflow)
        self._delete_workflow_btn = QPushButton("Delete")
        self._delete_workflow_btn.setMinimumHeight(34)
        self._delete_workflow_btn.clicked.connect(self._delete_workflow)
        list_btns.addWidget(self._add_workflow_btn)
        list_btns.addWidget(self._delete_workflow_btn)
        left_layout.addLayout(list_btns)
        splitter.addWidget(left_panel)

        right_panel, right_layout = _panel("Workflow")
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_lbl = QLabel("Name")
        name_lbl.setObjectName("toolbarLabel")
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Workflow name")
        self._name_edit.setMinimumHeight(34)
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(name_lbl)
        name_row.addWidget(self._name_edit, 1)
        right_layout.addLayout(name_row)

        steps_lbl = QLabel("Steps")
        steps_lbl.setObjectName("toolbarLabel")
        right_layout.addWidget(steps_lbl)

        steps_hint = QLabel("Drag to reorder. Conversion, artwork, lyrics, and rename use the latest scan.")
        steps_hint.setObjectName("headerSubtitle")
        steps_hint.setWordWrap(True)
        right_layout.addWidget(steps_hint)

        self._steps_list = QListWidget()
        self._steps_list.setStyleSheet(_LIST_STYLE)
        self._steps_list.setDragDropMode(QListWidget.InternalMove)
        self._steps_list.itemSelectionChanged.connect(self._update_editor_state)
        self._steps_list.itemDoubleClicked.connect(self._configure_step)
        self._steps_list.model().rowsMoved.connect(self._on_steps_reordered)
        right_layout.addWidget(self._steps_list, 1)

        step_btns = QHBoxLayout()
        step_btns.setContentsMargins(0, 0, 0, 0)
        step_btns.setSpacing(8)
        self._add_step_btn = QPushButton("Add Step")
        self._add_step_btn.setMinimumHeight(34)
        self._add_step_btn.clicked.connect(self._show_add_step_menu)
        self._configure_step_btn = QPushButton("Configure")
        self._configure_step_btn.setMinimumHeight(34)
        self._configure_step_btn.clicked.connect(self._configure_step)
        self._remove_step_btn = QPushButton("Remove Step")
        self._remove_step_btn.setMinimumHeight(34)
        self._remove_step_btn.clicked.connect(self._remove_step)
        step_btns.addWidget(self._add_step_btn)
        step_btns.addWidget(self._configure_step_btn)
        step_btns.addWidget(self._remove_step_btn)
        right_layout.addLayout(step_btns)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([280, 640])
        layout.addWidget(splitter, 1)

    def _load_workflow_list(self, select_id: str | None = None) -> None:
        current_id = select_id
        if current_id is None and self._current is not None:
            current_id = self._current.id

        self._workflow_list.blockSignals(True)
        self._workflow_list.clear()
        selected_item = None
        for workflow in self._manager.workflows:
            item = QListWidgetItem(workflow.name)
            item.setData(Qt.UserRole, workflow.id)
            self._workflow_list.addItem(item)
            if workflow.id == current_id:
                selected_item = item
        self._workflow_list.blockSignals(False)

        if selected_item is not None:
            self._workflow_list.setCurrentItem(selected_item)
        elif self._workflow_list.count() > 0:
            self._workflow_list.setCurrentRow(0)
        else:
            self._current = None
            self._on_workflow_selected()

    def _on_workflow_selected(self) -> None:
        selected = self._workflow_list.selectedItems()
        if not selected:
            self._current = None
            self._name_edit.blockSignals(True)
            self._name_edit.clear()
            self._name_edit.blockSignals(False)
            self._steps_list.clear()
            self._update_editor_state()
            self._update_run_state()
            return

        workflow_id = str(selected[0].data(Qt.UserRole) or "")
        self._current = self._manager.get_workflow(workflow_id)
        if self._current is None:
            self._update_editor_state()
            self._update_run_state()
            return

        self._name_edit.blockSignals(True)
        self._name_edit.setText(self._current.name)
        self._name_edit.blockSignals(False)
        self._load_steps_list()
        self._update_editor_state()
        self._update_run_state()

    def _load_steps_list(self) -> None:
        self._steps_list.clear()
        if self._current is None:
            return
        for step in self._current.steps:
            item = QListWidgetItem(describe_step(step))
            item.setData(Qt.UserRole, step)
            self._steps_list.addItem(item)
        if self._steps_list.count() > 0:
            self._steps_list.setCurrentRow(0)

    def _update_editor_state(self) -> None:
        enabled = self._current is not None and not self._busy
        self._delete_workflow_btn.setEnabled(enabled)
        self._name_edit.setEnabled(enabled)
        self._steps_list.setEnabled(enabled)
        self._add_step_btn.setEnabled(enabled)
        has_step = enabled and self._steps_list.currentRow() >= 0
        selected_step = self._selected_step()
        can_configure = bool(
            selected_step is not None
            and selected_step.type in CONFIGURABLE_STEPS
            and enabled
        )
        self._configure_step_btn.setEnabled(can_configure)
        self._add_workflow_btn.setEnabled(not self._busy)
        self._workflow_list.setEnabled(not self._busy)
        self._remove_step_btn.setEnabled(has_step)

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

    def _update_run_state(self) -> None:
        source = self._source_path()
        workflow = self._current
        if self._busy:
            self._status_lbl.setText("Running workflow…")
            self._run_btn.setEnabled(False)
            return
        if workflow is None:
            self._status_lbl.setText("Create a workflow, then add the jobs you want to run in order.")
            self._run_btn.setEnabled(False)
            return
        if not workflow.steps:
            self._status_lbl.setText("Add at least one step before running this workflow.")
            self._run_btn.setEnabled(False)
            return
        if source is None:
            self._status_lbl.setText("Choose a target folder or drive to run this workflow.")
            self._run_btn.setEnabled(False)
            return
        if workflow.needs_library() and (self._scanning or self._data_model is None):
            self._status_lbl.setText("Wait for the scan to finish. This workflow uses the scanned library.")
            self._run_btn.setEnabled(False)
            return
        self._status_lbl.setText(f"Ready to run on {source}")
        self._run_btn.setEnabled(True)

    def _add_workflow(self) -> None:
        workflow = new_workflow()
        self._manager.add_workflow(workflow)
        self._load_workflow_list(select_id=workflow.id)

    def _delete_workflow(self) -> None:
        if self._current is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Workflow",
            f"Delete “{self._current.name}”?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._manager.remove_workflow(self._current.id)
        self._current = None
        self._load_workflow_list()

    def _on_name_changed(self, text: str) -> None:
        if self._current is None:
            return
        self._current.name = text.strip() or "Untitled Workflow"
        self._manager.update_workflow(self._current)
        selected = self._workflow_list.selectedItems()
        if selected:
            selected[0].setText(self._current.name)

    def _show_add_step_menu(self) -> None:
        if self._current is None:
            return
        menu = QMenu(self)
        for action_id, label in WORKFLOW_ACTIONS.items():
            action = QAction(label, self)
            action.setData(action_id)
            menu.addAction(action)
        chosen = menu.exec(self._add_step_btn.mapToGlobal(self._add_step_btn.rect().bottomLeft()))
        if chosen is None:
            return
        step = WorkflowStep(type=str(chosen.data()))
        if configure_workflow_step(step, self):
            self._current.steps.append(step)
            self._manager.update_workflow(self._current)
            self._load_steps_list()
            self._steps_list.setCurrentRow(self._steps_list.count() - 1)
            self._update_editor_state()
            self._update_run_state()

    def _selected_step(self) -> WorkflowStep | None:
        if self._current is None:
            return None
        row = self._steps_list.currentRow()
        if row < 0 or row >= len(self._current.steps):
            return None
        return self._current.steps[row]

    def _configure_step(self, *_args) -> None:
        step = self._selected_step()
        if step is None or self._current is None:
            return
        if step.type not in CONFIGURABLE_STEPS:
            return
        if configure_workflow_step(step, self):
            self._manager.update_workflow(self._current)
            self._load_steps_list()
            self._update_run_state()

    def _remove_step(self) -> None:
        if self._current is None:
            return
        row = self._steps_list.currentRow()
        if row < 0:
            return
        self._current.steps.pop(row)
        self._manager.update_workflow(self._current)
        self._load_steps_list()
        self._update_editor_state()
        self._update_run_state()

    def _on_steps_reordered(self, *_args) -> None:
        if self._current is None:
            return
        steps = []
        for index in range(self._steps_list.count()):
            item = self._steps_list.item(index)
            step = item.data(Qt.UserRole)
            if isinstance(step, WorkflowStep):
                steps.append(step)
        if len(steps) == len(self._current.steps):
            self._current.steps = steps
            self._manager.update_workflow(self._current)

    def _run_current_workflow(self) -> None:
        workflow = self._current
        source = self._source_path()
        if workflow is None or source is None:
            return
        if not workflow.steps:
            return
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "Workflow Running", "A workflow is already running.")
            return

        step_lines = "\n".join(
            f"{index}. {describe_step(step)}"
            for index, step in enumerate(workflow.steps, start=1)
        )
        confirm = QMessageBox.question(
            self,
            "Run Workflow",
            f"Run “{workflow.name}” on:\n{source}\n\n{step_lines}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        worker = WorkflowExecutionWorker(workflow, source, self._data_model)
        progress = QProgressDialog(
            f"Starting {workflow.name}...",
            "Cancel",
            0,
            max(len(workflow.steps), 1),
            self,
        )
        progress.setWindowTitle("Run Workflow")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._progress = progress
        self._busy = True
        self._update_editor_state()
        self._update_run_state()

        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)

        self._thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.step_progress.connect(self._on_step_progress)
        worker.request_lyrics_review.connect(self._on_lyrics_review, Qt.QueuedConnection)
        worker.finished.connect(self._on_finished)
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
            self._progress.setLabelText("Cancelling workflow...")

    def _clear_refs(self) -> None:
        self._worker = None
        self._thread = None

    def _finish_job_ui(self) -> None:
        self._busy = False
        progress = self._progress
        self._progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_editor_state()
        self._update_run_state()

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, detail: str) -> None:
        progress = self._progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(max(current - 1, 0))
            progress.setLabelText(detail)
        except RuntimeError:
            self._progress = None

    @Slot(int, int, str)
    def _on_step_progress(self, current: int, total: int, detail: str) -> None:
        progress = self._progress
        if progress is None:
            return
        try:
            prefix = progress.labelText().split(":", 1)[0]
            progress.setLabelText(f"{prefix}: {detail} ({current}/{total})")
        except RuntimeError:
            self._progress = None

    @Slot(str, str)
    def _on_lyrics_review(self, title: str, lyrics_text: str) -> None:
        dialog = WorkflowLyricsReviewDialog(title, lyrics_text, self)
        accepted = dialog.exec() == QDialog.Accepted
        if self._worker is not None:
            self._worker.submit_review_result(accepted)

    def _emit_rescan_if_needed(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        if payload.get("needs_rescan"):
            self.needs_rescan.emit()

    @Slot(object)
    def _on_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_job_ui()
        QMessageBox.information(
            self,
            "Workflow Completed",
            str(payload.get("message") or "Workflow completed successfully."),
        )
        self._emit_rescan_if_needed(payload)

    @Slot(str)
    def _on_failed(self, error: str) -> None:
        self._finish_job_ui()
        QMessageBox.warning(self, "Workflow Failed", error)
        if self._current is not None and self._current.needs_rescan():
            self.needs_rescan.emit()

    @Slot(object)
    def _on_cancelled(self, payload_obj) -> None:
        self._finish_job_ui()
        QMessageBox.information(
            self,
            "Workflow Cancelled",
            "The workflow was cancelled. Completed steps are kept; later steps were not run.",
        )
        self._emit_rescan_if_needed(payload_obj)

    def cancel_running_job(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(8000)

    def set_target(self, path: str) -> None:
        self._target_path = path or ""
        if not self._target_path:
            self._data_model = None
        self._update_run_state()

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        self._target_path = data_model.root_path
        self._update_run_state()

    def set_processing_state(self, is_processing: bool) -> None:
        self._scanning = is_processing
        if is_processing:
            self._data_model = None
        self._update_run_state()
