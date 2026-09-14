"""Preview dialog for converting embedded lyrics into .lrc sidecars."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models.drive_data import TrackMetadata
from ..theme import Colours
from ..utils.lyrics_planner import (
    LyricsConvertPlan,
    actionable_convert_candidates,
    plan_converts_for_tracks,
)


class LyricsConvertDialog(QDialog):
    def __init__(
        self,
        tracks: list[TrackMetadata],
        root_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self._tracks = tracks
        self._root_path = root_path
        self._dry_run = False
        self._backup_root: Path | None = None
        self._plans: list[LyricsConvertPlan] = []

        self.setObjectName("lyricsConvertDialog")
        self.setWindowTitle("Convert Embedded Lyrics")
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(720)
        self.setMinimumHeight(520)
        self.setStyleSheet(
            f"""
            QDialog#lyricsConvertDialog {{
                background-color: {Colours.BG_ELEVATED};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            QDialog#lyricsConvertDialog QLabel {{
                background-color: transparent;
                color: {Colours.TEXT_PRIMARY};
            }}
            QDialog#lyricsConvertDialog QCheckBox {{
                spacing: 8px;
                color: {Colours.TEXT_PRIMARY};
                padding: 2px 0px;
            }}
            QDialog#lyricsConvertDialog QTableWidget {{
                background-color: {Colours.BG_SURFACE};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
                gridline-color: {Colours.BORDER_SUBTLE};
            }}
            QDialog#lyricsConvertDialog QHeaderView::section {{
                background-color: {Colours.BG_DARKEST};
                color: {Colours.TEXT_SECONDARY};
                border: none;
                border-bottom: 1px solid {Colours.BORDER_DEFAULT};
                padding: 6px;
                font-weight: 600;
            }}
            """
        )
        self._init_ui()
        self._refresh_plans()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Convert Embedded Lyrics")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")
        layout.addWidget(self._summary_label)

        self._preview_table = QTableWidget(0, 4)
        self._preview_table.setHorizontalHeaderLabels(["File", "Status", "Issues", "Action"])
        self._preview_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.setMinimumHeight(220)
        self._preview_table.setSortingEnabled(True)
        header = self._preview_table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self._preview_table, 1)

        warning = QLabel(
            "The Snowsky Echo Mini cannot display embedded lyrics. This writes a "
            "matching .lrc sidecar next to each track (song.ext → song.lrc). "
            "Existing sidecars with the same name will be overwritten."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #FF6E6E; font-weight: bold;")
        layout.addWidget(warning)

        self._backup_checkbox = QCheckBox("Back up existing .lrc files before overwriting")
        self._backup_path_input = QLineEdit()
        self._backup_path_input.setPlaceholderText("Choose a backup folder...")
        self._backup_browse_btn = QPushButton("Browse")
        self._backup_browse_btn.clicked.connect(self._choose_backup_folder)

        backup_row = QHBoxLayout()
        backup_row.setContentsMargins(22, 0, 0, 0)
        backup_row.addWidget(self._backup_path_input, 1)
        backup_row.addWidget(self._backup_browse_btn)
        self._backup_path_container = QWidget()
        self._backup_path_container.setLayout(backup_row)
        self._backup_path_container.setVisible(False)
        self._backup_checkbox.toggled.connect(self._update_backup_visibility)
        layout.addWidget(self._backup_checkbox)
        layout.addWidget(self._backup_path_container)

        self._dry_run_checkbox = QCheckBox("Dry run (preview only)")
        self._dry_run_checkbox.toggled.connect(self._update_backup_visibility)
        layout.addWidget(self._dry_run_checkbox)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        self._confirm_btn = QPushButton("Convert")
        self._confirm_btn.setObjectName("accentButton")
        cancel_btn.clicked.connect(self.reject)
        self._confirm_btn.clicked.connect(self._on_confirm)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(self._confirm_btn)
        layout.addLayout(button_row)

    def _choose_backup_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose Backup Folder", str(Path.home()))
        if selected:
            self._backup_path_input.setText(selected)

    def _update_backup_visibility(self) -> None:
        is_dry_run = self._dry_run_checkbox.isChecked()
        self._backup_checkbox.setEnabled(not is_dry_run)
        self._backup_path_container.setVisible(
            self._backup_checkbox.isChecked() and not is_dry_run
        )

    def _refresh_plans(self) -> None:
        self._plans = plan_converts_for_tracks(self._tracks, self._root_path)
        actionable_count = sum(1 for plan in self._plans if plan.has_action)
        overwrite_count = sum(1 for plan in self._plans if plan.has_action and plan.will_overwrite)
        extra = ""
        if overwrite_count:
            extra = f" {overwrite_count} existing sidecar{'s' if overwrite_count != 1 else ''} will be overwritten."
        self._summary_label.setText(
            f"{len(self._plans)} file{'s' if len(self._plans) != 1 else ''} selected; "
            f"{actionable_count} with embedded lyrics to export.{extra}"
        )

        self._preview_table.setSortingEnabled(False)
        self._preview_table.setRowCount(len(self._plans))
        for row, plan in enumerate(self._plans):
            file_item = QTableWidgetItem(plan.title)
            file_item.setToolTip(plan.relative_path)
            status_item = QTableWidgetItem(plan.status)
            issues_item = QTableWidgetItem(plan.issues)
            issues_item.setToolTip(plan.issues)
            action_item = QTableWidgetItem(plan.action)
            action_item.setToolTip(plan.action)

            for item in (file_item, status_item, issues_item, action_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            self._preview_table.setItem(row, 0, file_item)
            self._preview_table.setItem(row, 1, status_item)
            self._preview_table.setItem(row, 2, issues_item)
            self._preview_table.setItem(row, 3, action_item)

        self._preview_table.setSortingEnabled(True)
        self._confirm_btn.setEnabled(actionable_count > 0)

    def _on_confirm(self) -> None:
        self._backup_root = None
        if self._backup_checkbox.isChecked() and not self._dry_run_checkbox.isChecked():
            backup_text = self._backup_path_input.text().strip()
            if not backup_text:
                QMessageBox.warning(
                    self,
                    "Backup Folder Required",
                    "Choose a backup folder or disable backup before starting conversion.",
                )
                return
            backup_candidate = Path(backup_text).expanduser()
            try:
                self._backup_root = backup_candidate.resolve()
            except Exception:
                self._backup_root = backup_candidate

        self._dry_run = self._dry_run_checkbox.isChecked()
        self.accept()

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def backup_root(self) -> Path | None:
        return self._backup_root

    def candidates(self) -> list[dict[str, object]]:
        return actionable_convert_candidates(self._plans)
