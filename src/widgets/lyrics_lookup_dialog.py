"""Review dialog for LRCLIB lyrics lookup results."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..theme import Colours, colours_for_status
from ..utils.lyrics_lookup import LyricsLookupResult
from ..utils.lyrics_planner import lookup_candidates_from_results


class LyricsLookupDialog(QDialog):
    COL_APPLY = 0
    COL_FILE = 1
    COL_STATUS = 2
    COL_SOURCE = 3
    COL_PREVIEW = 4

    def __init__(
        self,
        results: list[LyricsLookupResult],
        root_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self._results = results
        self._root_path = root_path
        self._dry_run = False
        self._backup_root: Path | None = None
        self._updating = False

        self.setObjectName("lyricsLookupDialog")
        self.setWindowTitle("Find Missing Lyrics")
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(860)
        self.setMinimumHeight(580)
        self.setStyleSheet(
            f"""
            QDialog#lyricsLookupDialog {{
                background-color: {Colours.BG_ELEVATED};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            QDialog#lyricsLookupDialog QLabel {{
                background-color: transparent;
                color: {Colours.TEXT_PRIMARY};
            }}
            QDialog#lyricsLookupDialog QCheckBox {{
                spacing: 8px;
                color: {Colours.TEXT_PRIMARY};
                padding: 2px 0px;
            }}
            QDialog#lyricsLookupDialog QTableWidget {{
                background-color: {Colours.BG_SURFACE};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
                gridline-color: {Colours.BORDER_SUBTLE};
            }}
            QDialog#lyricsLookupDialog QHeaderView::section {{
                background-color: {Colours.BG_DARKEST};
                color: {Colours.TEXT_SECONDARY};
                border: none;
                border-bottom: 1px solid {Colours.BORDER_DEFAULT};
                padding: 6px;
                font-weight: 600;
            }}
            QDialog#lyricsLookupDialog QPlainTextEdit {{
                background-color: {Colours.BG_SURFACE};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            """
        )
        self._init_ui()
        self._populate_table()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Review Lookup Results")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")
        layout.addWidget(self._summary_label)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Apply", "File", "Lookup", "Source", "Preview"])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(220)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(self.COL_APPLY, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_FILE, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_SOURCE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PREVIEW, QHeaderView.Stretch)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, 2)

        preview_label = QLabel("Lyrics preview")
        preview_label.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px; font-weight: 600;")
        layout.addWidget(preview_label)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setPlaceholderText("Select a row to preview the full lyrics.")
        self._preview.setMinimumHeight(140)
        layout.addWidget(self._preview, 1)

        warning = QLabel(
            "Selected results are written as matching .lrc sidecars "
            "(song.ext → song.lrc). Existing sidecars with the same name will be overwritten."
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
        self._confirm_btn = QPushButton("Apply Selected")
        self._confirm_btn.setObjectName("accentButton")
        cancel_btn.clicked.connect(self.reject)
        self._confirm_btn.clicked.connect(self._on_confirm)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(self._confirm_btn)
        layout.addLayout(button_row)

    def _status_token(self, status: str) -> str:
        mapping = {
            "Found": "COMPATIBLE",
            "Not found": "SKIPPED",
            "Instrumental": "LIMITED",
            "Error": "INCOMPATIBLE",
            "Missing metadata": "INCOMPATIBLE",
        }
        return mapping.get(status, "")

    def _populate_table(self) -> None:
        found = sum(1 for result in self._results if result.can_apply)
        self._summary_label.setText(
            f"{len(self._results)} track{'s' if len(self._results) != 1 else ''} searched; "
            f"{found} with lyrics ready to write."
        )

        self._updating = True
        self._table.setRowCount(len(self._results))
        for row, result in enumerate(self._results):
            apply_item = QTableWidgetItem()
            apply_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            apply_item.setCheckState(Qt.Checked if result.is_selected else Qt.Unchecked)
            if not result.can_apply:
                apply_item.setFlags(Qt.ItemIsSelectable)
                apply_item.setCheckState(Qt.Unchecked)

            file_item = QTableWidgetItem(result.relative_path)
            status_item = QTableWidgetItem(result.status)
            source_item = QTableWidgetItem(result.source)
            preview_item = QTableWidgetItem(result.preview)

            for item in (file_item, status_item, source_item, preview_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            token = self._status_token(result.status)
            bg, fg = colours_for_status(token)
            if bg:
                status_item.setBackground(QColor(bg))
            if fg:
                status_item.setForeground(QColor(fg))

            self._table.setItem(row, self.COL_APPLY, apply_item)
            self._table.setItem(row, self.COL_FILE, file_item)
            self._table.setItem(row, self.COL_STATUS, status_item)
            self._table.setItem(row, self.COL_SOURCE, source_item)
            self._table.setItem(row, self.COL_PREVIEW, preview_item)

        self._updating = False
        self._update_confirm_state()
        if self._results:
            self._table.selectRow(0)
            self._on_selection_changed()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != self.COL_APPLY:
            return
        row = item.row()
        if row < 0 or row >= len(self._results):
            return
        result = self._results[row]
        if not result.can_apply:
            return
        result.is_selected = item.checkState() == Qt.Checked
        self._update_confirm_state()

    def _on_selection_changed(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._preview.setPlainText("")
            return
        row = rows[0].row()
        if 0 <= row < len(self._results):
            self._preview.setPlainText(self._results[row].lyrics_text)

    def _update_confirm_state(self) -> None:
        selected = sum(1 for result in self._results if result.is_selected and result.can_apply)
        self._confirm_btn.setEnabled(selected > 0)
        self._confirm_btn.setText(f"Apply Selected ({selected})" if selected else "Apply Selected")

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

    def _on_confirm(self) -> None:
        self._backup_root = None
        if self._backup_checkbox.isChecked() and not self._dry_run_checkbox.isChecked():
            backup_text = self._backup_path_input.text().strip()
            if not backup_text:
                QMessageBox.warning(
                    self,
                    "Backup Folder Required",
                    "Choose a backup folder or disable backup before applying lyrics.",
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
        return lookup_candidates_from_results(self._results)
