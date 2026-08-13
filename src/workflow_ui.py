import uuid
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QLineEdit, QWidget, QSplitter, QMenu, QMessageBox,
    QCheckBox, QComboBox, QFileDialog, QDialogButtonBox, QFormLayout
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

from .workflows import WorkflowManager, Workflow, WorkflowStep

WORKFLOW_ACTIONS = {
    "backup": "Backup Files",
    "file_cleanup": "File Cleanup",
    "file_rename": "File Rename",
    "metadata_manager": "Metadata Manager",
    "lyrics_manager": "Lyrics Manager",
    "fix_album_art": "Fix Album Art",
    "make_music_compatible": "Make Music Compatible (FLAC)",
    "make_music_eq_compatible": "Make Music EQ-Compatible",
}



class WorkflowStepConfigDialog(QDialog):
    def __init__(self, step: WorkflowStep, parent=None):
        super().__init__(parent)
        self.step = step
        self.setWindowTitle(f"Configure {WORKFLOW_ACTIONS.get(step.type, step.type)}")
        self.layout = QVBoxLayout(self)
        
        self.form_layout = QFormLayout()
        self.layout.addLayout(self.form_layout)
        
        self.setup_ui()
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)
        
    def setup_ui(self):
        pass
        
    def save_config(self):
        pass

    def accept(self):
        self.save_config()
        super().accept()

class BackupConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self):
        self.path_edit = QLineEdit(self.step.config.get("backup_path", ""))
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.path_edit)
        row.addWidget(self.browse_btn)
        self.form_layout.addRow("Backup Folder:", row)
        
    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Folder")
        if folder:
            self.path_edit.setText(folder)
            
    def save_config(self):
        self.step.config["backup_path"] = self.path_edit.text()

class FileCleanupConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self):
        self.clean_hidden = QCheckBox("Hidden System Files (.DS_Store, Thumbs.db)")
        self.clean_hidden.setChecked(self.step.config.get("clean_hidden", True))
        
        self.clean_forks = QCheckBox("macOS Resource Forks (._*)")
        self.clean_forks.setChecked(self.step.config.get("clean_forks", True))
        
        self.clean_empty = QCheckBox("Empty Directories")
        self.clean_empty.setChecked(self.step.config.get("clean_empty", True))
        
        self.form_layout.addRow(self.clean_hidden)
        self.form_layout.addRow(self.clean_forks)
        self.form_layout.addRow(self.clean_empty)
        
    def save_config(self):
        self.step.config["clean_hidden"] = self.clean_hidden.isChecked()
        self.step.config["clean_forks"] = self.clean_forks.isChecked()
        self.step.config["clean_empty"] = self.clean_empty.isChecked()

class FileRenameConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self):
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Track No. Title")
        
        current_preset = self.step.config.get("preset", "Track No. Title")
        idx = self.preset_combo.findText(current_preset)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
            
        self.form_layout.addRow("Preset:", self.preset_combo)
        
    def save_config(self):
        self.step.config["preset"] = self.preset_combo.currentText()

class MetadataManagerConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self):
        self.tag_name = QLineEdit(self.step.config.get("tag_name", ""))
        self.tag_value = QLineEdit(self.step.config.get("tag_value", ""))
        
        self.action_combo = QComboBox()
        self.action_combo.addItems(["Set", "Remove"])
        current_action = self.step.config.get("action", "Set")
        self.action_combo.setCurrentText(current_action)
        
        self.form_layout.addRow("Tag Name (e.g. Album Artist):", self.tag_name)
        self.form_layout.addRow("Tag Value:", self.tag_value)
        self.form_layout.addRow("Action:", self.action_combo)
        
    def save_config(self):
        self.step.config["tag_name"] = self.tag_name.text()
        self.step.config["tag_value"] = self.tag_value.text()
        self.step.config["action"] = self.action_combo.currentText()

class LyricsManagerConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self):
        self.auto_apply = QCheckBox("Auto-Apply First Match (Silent)")
        self.auto_apply.setChecked(self.step.config.get("auto_apply", False))
        
        self.form_layout.addRow(self.auto_apply)
        self.form_layout.addRow(QLabel("If unchecked, the workflow will pause to let you review matches."))
        
    def save_config(self):
        self.step.config["auto_apply"] = self.auto_apply.isChecked()

def configure_workflow_step(step: WorkflowStep, parent) -> bool:
    dialog_classes = {
        "backup": BackupConfigDialog,
        "file_cleanup": FileCleanupConfigDialog,
        "file_rename": FileRenameConfigDialog,
        "metadata_manager": MetadataManagerConfigDialog,
        "lyrics_manager": LyricsManagerConfigDialog,
    }
    
    dialog_class = dialog_classes.get(step.type)
    if not dialog_class:
        return True # No configuration needed
        
    dialog = dialog_class(step, parent)
    return dialog.exec() == QDialog.Accepted


class WorkflowManagerDialog(QDialog):
    def __init__(self, manager: WorkflowManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Manage Workflows")
        self.resize(700, 500)
        self.current_workflow: Workflow | None = None

        self._build_ui()
        self._load_workflow_list()

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # Left panel: List of workflows
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.workflow_list = QListWidget()
        self.workflow_list.itemSelectionChanged.connect(self._on_workflow_selected)
        left_layout.addWidget(QLabel("Saved Workflows"))
        left_layout.addWidget(self.workflow_list)
        
        btn_layout = QHBoxLayout()
        self.add_workflow_btn = QPushButton("New Workflow")
        self.add_workflow_btn.clicked.connect(self._add_workflow)
        self.delete_workflow_btn = QPushButton("Delete")
        self.delete_workflow_btn.clicked.connect(self._delete_workflow)
        self.delete_workflow_btn.setEnabled(False)
        
        btn_layout.addWidget(self.add_workflow_btn)
        btn_layout.addWidget(self.delete_workflow_btn)
        left_layout.addLayout(btn_layout)

        # Right panel: Workflow details
        right_panel = QWidget()
        self.right_layout = QVBoxLayout(right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Name:"))
        self.workflow_name_edit = QLineEdit()
        self.workflow_name_edit.textChanged.connect(self._on_name_changed)
        name_layout.addWidget(self.workflow_name_edit)
        self.right_layout.addLayout(name_layout)

        self.right_layout.addWidget(QLabel("Steps:"))
        self.steps_list = QListWidget()
        self.steps_list.setDragDropMode(QListWidget.InternalMove)
        self.steps_list.model().rowsMoved.connect(self._on_steps_reordered)
        self.right_layout.addWidget(self.steps_list)

        step_btn_layout = QHBoxLayout()
        self.add_step_btn = QPushButton("Add Step")
        self.add_step_btn.clicked.connect(self._show_add_step_menu)
        self.remove_step_btn = QPushButton("Remove Step")
        self.remove_step_btn.clicked.connect(self._remove_step)
        self.configure_step_btn = QPushButton("Configure")
        self.configure_step_btn.clicked.connect(self._configure_step)
        
        step_btn_layout.addWidget(self.add_step_btn)
        step_btn_layout.addWidget(self.configure_step_btn)
        step_btn_layout.addWidget(self.remove_step_btn)
        self.right_layout.addLayout(step_btn_layout)
        
        self.right_layout.addStretch()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 450])
        
        layout.addWidget(splitter)
        
        self._set_right_panel_enabled(False)

    def _set_right_panel_enabled(self, enabled: bool):
        self.workflow_name_edit.setEnabled(enabled)
        self.steps_list.setEnabled(enabled)
        self.add_step_btn.setEnabled(enabled)
        self.remove_step_btn.setEnabled(enabled)

    def _load_workflow_list(self):
        self.workflow_list.clear()
        for w in self.manager.workflows:
            item = QListWidgetItem(w.name)
            item.setData(Qt.UserRole, w.id)
            self.workflow_list.addItem(item)

    def _on_workflow_selected(self):
        selected_items = self.workflow_list.selectedItems()
        if not selected_items:
            self.current_workflow = None
            self.delete_workflow_btn.setEnabled(False)
            self._set_right_panel_enabled(False)
            self.workflow_name_edit.clear()
            self.steps_list.clear()
            return

        item = selected_items[0]
        w_id = item.data(Qt.UserRole)
        self.current_workflow = next((w for w in self.manager.workflows if w.id == w_id), None)
        
        if self.current_workflow:
            self.delete_workflow_btn.setEnabled(True)
            self._set_right_panel_enabled(True)
            
            # Block signals to prevent recursive saving
            self.workflow_name_edit.blockSignals(True)
            self.workflow_name_edit.setText(self.current_workflow.name)
            self.workflow_name_edit.blockSignals(False)
            
            self._load_steps_list()

    def _load_steps_list(self):
        self.steps_list.clear()
        if not self.current_workflow:
            return
        for step in self.current_workflow.steps:
            label = WORKFLOW_ACTIONS.get(step.type, step.type)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, step)
            self.steps_list.addItem(item)

    def _add_workflow(self):
        new_workflow = Workflow(id=str(uuid.uuid4()), name="New Workflow", steps=[])
        self.manager.add_workflow(new_workflow)
        self._load_workflow_list()
        
        # Select the new item
        for i in range(self.workflow_list.count()):
            item = self.workflow_list.item(i)
            if item.data(Qt.UserRole) == new_workflow.id:
                self.workflow_list.setCurrentItem(item)
                break

    def _delete_workflow(self):
        if not self.current_workflow:
            return
        
        reply = QMessageBox.question(
            self, "Delete Workflow", 
            f"Are you sure you want to delete '{self.current_workflow.name}'?"
        )
        if reply == QMessageBox.Yes:
            self.manager.remove_workflow(self.current_workflow.id)
            self._load_workflow_list()

    def _on_name_changed(self, text: str):
        if self.current_workflow:
            self.current_workflow.name = text
            self.manager.update_workflow(self.current_workflow)
            
            # Update list widget item text without losing selection
            selected_items = self.workflow_list.selectedItems()
            if selected_items:
                selected_items[0].setText(text)

    def _show_add_step_menu(self):
        menu = QMenu(self)
        for action_id, label in WORKFLOW_ACTIONS.items():
            action = QAction(label, self)
            action.setData(action_id)
            menu.addAction(action)
            
        action = menu.exec(self.add_step_btn.mapToGlobal(self.add_step_btn.rect().bottomLeft()))
        if action and self.current_workflow:
            action_id = action.data()
            new_step = WorkflowStep(type=action_id)
            if configure_workflow_step(new_step, self):
                self.current_workflow.steps.append(new_step)
                self.manager.update_workflow(self.current_workflow)
                self._load_steps_list()

    def _remove_step(self):
        if not self.current_workflow:
            return
        selected = self.steps_list.currentRow()
        if selected >= 0:
            self.current_workflow.steps.pop(selected)
            self.manager.update_workflow(self.current_workflow)
            self._load_steps_list()
            
    def _configure_step(self):
        if not self.current_workflow:
            return
        selected = self.steps_list.currentRow()
        if selected >= 0:
            step = self.current_workflow.steps[selected]
            if configure_workflow_step(step, self):
                self.manager.update_workflow(self.current_workflow)
                self._load_steps_list()

    def _on_steps_reordered(self, parent, start, end, destination, row):
        if not self.current_workflow:
            return
            
        # Rebuild the steps list from the UI order
        new_steps = []
        for i in range(self.steps_list.count()):
            item = self.steps_list.item(i)
            step_obj = item.data(Qt.UserRole)
            new_steps.append(step_obj)
            
        self.current_workflow.steps = new_steps
        self.manager.update_workflow(self.current_workflow)
