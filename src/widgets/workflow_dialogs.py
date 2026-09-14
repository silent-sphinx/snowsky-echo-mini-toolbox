"""Step configuration and lyrics-review dialogs for the Workflows tab."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..models.workflow import (
    METADATA_TAG_CHOICES,
    WORKFLOW_ACTIONS,
    WorkflowStep,
    normalize_tag_key,
    resolve_rename_preset_id,
    tag_choice_label,
)
from ..theme import Colours
from ..utils.file_rename import RENAME_PRESETS, resolve_preset


def _dialog_stylesheet(object_name: str) -> str:
    return f"""
        QDialog#{object_name} {{
            background-color: {Colours.BG_ELEVATED};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
        }}
        QDialog#{object_name} QLabel {{
            background-color: transparent;
            color: {Colours.TEXT_PRIMARY};
        }}
        QDialog#{object_name} QPlainTextEdit {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
        }}
    """


class WorkflowStepConfigDialog(QDialog):
    def __init__(self, step: WorkflowStep, parent=None):
        super().__init__(parent)
        self.step = step
        self.setObjectName("workflowStepDialog")
        self.setWindowTitle(f"Configure {WORKFLOW_ACTIONS.get(step.type, step.type)}")
        self.setStyleSheet(_dialog_stylesheet("workflowStepDialog"))
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.form_layout = QFormLayout()
        self.form_layout.setSpacing(10)
        layout.addLayout(self.form_layout)
        self.setup_ui()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def setup_ui(self) -> None:
        return

    def save_config(self) -> bool:
        return True

    def accept(self) -> None:
        if self.save_config():
            super().accept()


class BackupConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self) -> None:
        self.path_edit = QLineEdit(str(self.step.config.get("backup_path") or ""))
        self.path_edit.setPlaceholderText("Folder to save zip backups")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_btn)
        self.form_layout.addRow("Backup folder:", row)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Backup Folder",
            self.path_edit.text().strip(),
        )
        if folder:
            self.path_edit.setText(folder)

    def save_config(self) -> bool:
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.information(self, "Backup Folder Required", "Choose a folder to save zip backups.")
            return False
        self.step.config["backup_path"] = path
        return True


class FileCleanupConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self) -> None:
        self.clean_hidden = QCheckBox("Hidden system files (.DS_Store, Thumbs.db)")
        self.clean_hidden.setChecked(bool(self.step.config.get("clean_hidden", True)))
        self.clean_forks = QCheckBox("macOS resource forks (._*)")
        self.clean_forks.setChecked(bool(self.step.config.get("clean_forks", True)))
        self.clean_empty = QCheckBox("Empty folders")
        self.clean_empty.setChecked(bool(self.step.config.get("clean_empty", True)))
        self.form_layout.addRow(self.clean_hidden)
        self.form_layout.addRow(self.clean_forks)
        self.form_layout.addRow(self.clean_empty)

    def save_config(self) -> bool:
        self.step.config["clean_hidden"] = self.clean_hidden.isChecked()
        self.step.config["clean_forks"] = self.clean_forks.isChecked()
        self.step.config["clean_empty"] = self.clean_empty.isChecked()
        if not any(self.step.config[key] for key in ("clean_hidden", "clean_forks", "clean_empty")):
            QMessageBox.information(self, "Nothing Selected", "Choose at least one cleanup option.")
            return False
        return True


class FileRenameConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self) -> None:
        self.preset_combo = QComboBox()
        current_id = resolve_rename_preset_id(str(self.step.config.get("preset") or ""))
        selected_index = 0
        for index, preset in enumerate(RENAME_PRESETS):
            self.preset_combo.addItem(preset.label, preset.id)
            if preset.id == current_id:
                selected_index = index
        self.preset_combo.setCurrentIndex(selected_index)
        self.form_layout.addRow("Name format:", self.preset_combo)

    def save_config(self) -> bool:
        preset_id = str(self.preset_combo.currentData() or resolve_preset(None).id)
        self.step.config["preset"] = preset_id
        return True


class MetadataManagerConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self) -> None:
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        current_key = normalize_tag_key(str(self.step.config.get("tag_name") or "albumartist"))
        selected_index = 0
        known_keys = {key for key, _label in METADATA_TAG_CHOICES}
        for index, (key, label) in enumerate(METADATA_TAG_CHOICES):
            self.tag_combo.addItem(label, key)
            if key == current_key:
                selected_index = index
        if current_key and current_key not in known_keys:
            self.tag_combo.addItem(tag_choice_label(current_key), current_key)
            selected_index = self.tag_combo.count() - 1
        self.tag_combo.setCurrentIndex(selected_index)

        self.value_edit = QLineEdit(str(self.step.config.get("tag_value") or ""))
        self.action_combo = QComboBox()
        self.action_combo.addItems(["Set", "Remove"])
        current_action = str(self.step.config.get("action") or "Set")
        self.action_combo.setCurrentText("Remove" if current_action.lower() == "remove" else "Set")

        self.form_layout.addRow("Tag:", self.tag_combo)
        self.form_layout.addRow("Value:", self.value_edit)
        self.form_layout.addRow("Action:", self.action_combo)

    def save_config(self) -> bool:
        data_key = self.tag_combo.currentData()
        typed = self.tag_combo.currentText().strip()
        tag_name = normalize_tag_key(str(data_key or typed))
        if not tag_name:
            QMessageBox.information(self, "Tag Required", "Choose a tag to set or remove.")
            return False
        self.step.config["tag_name"] = tag_name
        self.step.config["tag_value"] = self.value_edit.text()
        self.step.config["action"] = self.action_combo.currentText()
        return True


class LyricsManagerConfigDialog(WorkflowStepConfigDialog):
    def setup_ui(self) -> None:
        self.auto_apply = QCheckBox("Auto-apply the first LRCLIB match")
        self.auto_apply.setChecked(bool(self.step.config.get("auto_apply", False)))
        hint = QLabel(
            "Embedded lyrics are written to .lrc sidecars automatically. "
            "If this is unchecked, online matches pause so you can review them."
        )
        hint.setObjectName("headerSubtitle")
        hint.setWordWrap(True)
        self.form_layout.addRow(self.auto_apply)
        self.form_layout.addRow(hint)

    def save_config(self) -> bool:
        self.step.config["auto_apply"] = self.auto_apply.isChecked()
        return True


def configure_workflow_step(step: WorkflowStep, parent) -> bool:
    dialog_classes = {
        "backup": BackupConfigDialog,
        "file_cleanup": FileCleanupConfigDialog,
        "file_rename": FileRenameConfigDialog,
        "metadata_manager": MetadataManagerConfigDialog,
        "lyrics_manager": LyricsManagerConfigDialog,
    }
    dialog_class = dialog_classes.get(step.type)
    if dialog_class is None:
        return True
    dialog = dialog_class(step, parent)
    return dialog.exec() == QDialog.Accepted


class WorkflowLyricsReviewDialog(QDialog):
    def __init__(self, title: str, lyrics_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("workflowLyricsReviewDialog")
        self.setWindowTitle(f"Review Lyrics — {title}")
        self.setMinimumSize(520, 640)
        self.setStyleSheet(_dialog_stylesheet("workflowLyricsReviewDialog"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 15px; font-weight: 700;")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        hint = QLabel("Apply writes a matching .lrc sidecar next to the track. Skip leaves this file unchanged.")
        hint.setObjectName("headerSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(lyrics_text or "No lyrics available.")
        layout.addWidget(text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.button(QDialogButtonBox.Cancel).setText("Skip")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
