from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QModelIndex, Signal, Slot
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QLineEdit,
    QComboBox,
    QPushButton,
    QProgressDialog,
    QMessageBox,
    QApplication,
    QStyledItemDelegate,
    QMenu,
    QLabel,
)
from PySide6.QtGui import QBrush

from ..theme import Colours
from ..models.drive_data import DriveDataModel, TrackMetadata
from ..models.file_rename_model import (
    FileRenameTableModel,
    FileRenameFilterProxyModel,
    RenameColumn,
)
from ..threads.file_rename import FileRenameWorker
from ..utils.file_rename import (
    PRESET_LABELS,
    PRESET_TRACKNO_TRACKNAME,
    apply_rename_evaluations,
    is_rename_track,
    rename_candidate_dict,
)
from .page_chrome import bind_search_field, filter_toolbar, flow_steps, freeze_view, loading_page, page_header, PATH_COLUMN_WIDTH
from .stat_card import StatCard
from .grouped_header_view import GroupedHeaderView
from .table_select import (
    SelectAllTableView,
    add_select_all_rows_action,
    wire_select_all_rows,
)


class HighlightDelegate(QStyledItemDelegate):
    """Custom delegate to enforce background colors over QSS/Alternating rows."""

    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if bg:
            painter.fillRect(option.rect, bg)
            option.backgroundBrush = QBrush(Qt.NoBrush)
        super().paint(painter, option, index)


_COMBO_STYLE = f"""
    QComboBox {{
        background-color: {Colours.BG_SURFACE};
        border: 1px solid {Colours.BORDER_DEFAULT};
        border-radius: 0px;
        padding: 4px 12px;
        color: {Colours.TEXT_PRIMARY};
        font-weight: 500;
    }}
    QComboBox:hover {{
        border-color: {Colours.ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {Colours.TEXT_SECONDARY};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {Colours.BG_ELEVATED};
        border: 1px solid {Colours.BORDER_DEFAULT};
        selection-background-color: {Colours.ACCENT_MUTED};
        color: {Colours.TEXT_PRIMARY};
        outline: none;
    }}
"""


class FileRenameWidget(QWidget):
    library_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model = None
        self._last_checked_row = None
        self._rename_busy = False
        self._rename_thread: QThread | None = None
        self._rename_worker: FileRenameWorker | None = None
        self._rename_progress: QProgressDialog | None = None
        self._init_models()
        self._init_ui()
        self._connect_signals()

    def _init_models(self) -> None:
        self._source_model = FileRenameTableModel(self)
        self._proxy_model = FileRenameFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        self._rename_btn = QPushButton("Rename Selected Files")
        self._rename_btn.setObjectName("accentButton")
        self._rename_btn.setEnabled(False)
        self._rename_btn.setMinimumHeight(34)
        self._rename_btn.setToolTip("Tick files in the table, then rename them to the selected format.")

        layout.addWidget(page_header(
            "File Rename",
            "Choose a filename format, preview the new names, then rename only the files you tick.",
            [self._rename_btn],
        ))

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page(
            "Hardware scan in progress.",
            "Building rename suggestions...",
        ))

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        data_layout.addWidget(flow_steps([
            (
                "1",
                "Choose a format",
                "Pick how files should be named. Suggestions are built from each track’s tags.",
            ),
            (
                "2",
                "Review the preview",
                "Compare current and suggested names. Conflicts and missing tags stay unticked.",
            ),
            (
                "3",
                "Rename what you tick",
                "Apply when the preview looks right. Matching .lrc lyrics files are renamed too.",
            ),
        ]))

        toolbar_panel, toolbar = filter_toolbar()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            "Search by status, artist, title, current or suggested file name…"
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        format_lbl = QLabel("NAME FORMAT")
        format_lbl.setObjectName("toolbarLabel")
        toolbar.addWidget(format_lbl)

        self._preset_combo = QComboBox()
        for preset_id, label in PRESET_LABELS.items():
            self._preset_combo.addItem(label, preset_id)
        self._preset_combo.setCurrentIndex(
            max(0, self._preset_combo.findData(PRESET_TRACKNO_TRACKNAME))
        )
        self._preset_combo.setMinimumHeight(34)
        self._preset_combo.setMinimumWidth(260)
        self._preset_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._preset_combo.setStyleSheet(_COMBO_STYLE)
        self._preset_combo.setToolTip("The filename pattern applied to selected files.")
        toolbar.addWidget(self._preset_combo)

        show_lbl = QLabel("SHOW")
        show_lbl.setObjectName("toolbarLabel")
        toolbar.addWidget(show_lbl)

        self._status_combo = QComboBox()
        self._status_combo.addItems(
            ["All Statuses", "Needs Rename", "Matching", "Missing Metadata", "Conflicts"]
        )
        self._status_combo.setCurrentText("Needs Rename")
        self._status_combo.setMinimumHeight(34)
        self._status_combo.setMinimumWidth(170)
        self._status_combo.setStyleSheet(_COMBO_STYLE)
        self._status_combo.setToolTip("Focus the table on files you can rename, or on problems to fix.")
        toolbar.addWidget(self._status_combo)

        data_layout.addWidget(toolbar_panel)

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self._stat_total = StatCard("Audio Scanned", Colours.STAT_TOTAL, self)
        self._stat_rename = StatCard("Needs Rename", Colours.STATUS_INCOMPATIBLE, self)
        self._stat_matching = StatCard("Already Matching", Colours.STATUS_COMPATIBLE, self)
        self._stat_missing = StatCard("Missing Metadata", Colours.STATUS_MISSING, self)
        self._stat_conflicts = StatCard("Conflicts", Colours.STATUS_LIMITED, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_rename)
        stats_layout.addWidget(self._stat_matching)
        stats_layout.addWidget(self._stat_missing)
        stats_layout.addWidget(self._stat_conflicts)
        data_layout.addLayout(stats_layout)

        self._guidance_lbl = QLabel()
        self._guidance_lbl.setObjectName("headerSubtitle")
        self._guidance_lbl.setWordWrap(True)
        data_layout.addWidget(self._guidance_lbl)

        self._table = SelectAllTableView()
        self._table.setModel(self._proxy_model)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(SelectAllTableView.SelectRows)
        self._table.setSelectionMode(SelectAllTableView.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)

        header_view = GroupedHeaderView(self._table)
        self._table.setHorizontalHeader(header_view)
        header_view.setStretchLastSection(True)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(-1, Qt.AscendingOrder)
        header_view.add_group("Track Info", 1, 3)
        header_view.add_group("Status", 4, 5)
        header_view.add_group("Rename", 6, 8)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, RenameColumn.COUNT):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)
        wire_select_all_rows(self._table, toolbar, owns_menu=False)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        bind_search_field(self._search_input, self._on_search_changed)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self._status_combo.currentTextChanged.connect(self._on_status_filter_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._rename_btn.clicked.connect(self._rename_selected_files)
        self._source_model.dataChanged.connect(self._on_source_data_changed)
        self._proxy_model.set_status_filter(self._status_combo.currentText())
        self._update_guidance()

    def _current_preset_id(self) -> str:
        return self._preset_combo.currentData() or PRESET_TRACKNO_TRACKNAME

    def _checked_tracks(self) -> list[TrackMetadata]:
        return [
            track for track in self._source_model.tracks()
            if track.rename_is_checked and track.rename_status == "RENAME"
        ]

    def _is_ready(self) -> bool:
        return (
            self._data_model is not None
            and self._stack.currentIndex() == 1
            and not self._rename_busy
        )

    def _update_rename_button_state(self) -> None:
        checked = self._checked_tracks()
        ready = self._is_ready() and bool(checked)
        self._rename_btn.setEnabled(ready)
        if checked:
            count = len(checked)
            noun = "File" if count == 1 else "Files"
            self._rename_btn.setText(f"Rename {count} {noun}")
            self._rename_btn.setToolTip(
                f"Rename {count} selected {noun.lower()} to “{self._preset_combo.currentText()}”."
            )
        else:
            self._rename_btn.setText("Rename Selected Files")
            self._rename_btn.setToolTip("Tick files that need renaming, then apply the selected format.")
        self._update_guidance()

    def _update_guidance(self) -> None:
        if not hasattr(self, "_guidance_lbl"):
            return
        if self._data_model is None or self._stack.currentIndex() != 1:
            self._guidance_lbl.setText(
                "Scan a drive or folder first. This page then previews new names from each track’s tags."
            )
            return

        format_name = self._preset_combo.currentText()
        needs_rename = self._source_model.count_by_status("RENAME")
        matching = self._source_model.count_by_status("MATCHING")
        missing = self._source_model.count_by_status("MISSING")
        conflicts = self._source_model.count_by_status("CONFLICT")
        checked = len(self._checked_tracks())
        showing = self._status_combo.currentText()

        if checked:
            noun = "file" if checked == 1 else "files"
            self._guidance_lbl.setText(
                f"Ready to apply “{format_name}” to {checked} {noun}. "
                "Matching .lrc lyrics files will be renamed with them."
            )
            return

        if needs_rename:
            noun = "file doesn’t" if needs_rename == 1 else "files don’t"
            extra = ""
            if showing != "Needs Rename":
                extra = ' Switch Show to “Needs Rename” to focus on them.'
            self._guidance_lbl.setText(
                f"{needs_rename} {noun} match “{format_name}” yet. "
                f"They are ticked by default — review the suggested names, then rename.{extra}"
            )
            return

        if conflicts:
            noun = "file has" if conflicts == 1 else "files have"
            extra = ""
            if showing != "Conflicts":
                extra = ' Switch Show to “Conflicts” to inspect them.'
            self._guidance_lbl.setText(
                f"{conflicts} {noun} a suggested name that already exists or is used twice. "
                f"Those rows stay unticked until you resolve the clash or pick another format.{extra}"
            )
            return

        if missing:
            extra = ""
            if showing != "Missing Metadata":
                extra = ' Switch Show to “Missing Metadata” to see which tags are required.'
            self._guidance_lbl.setText(
                f"This format needs tags that {missing} "
                f"{'file is' if missing == 1 else 'files are'} missing. "
                f"Fill Title, Artist, or Album in Metadata Browser, then come back.{extra}"
            )
            return

        if matching:
            self._guidance_lbl.setText(
                f"Every file already matches “{format_name}”. "
                "Pick a different name format if you want to rename them another way."
            )
            return

        self._guidance_lbl.setText(
            "No audio files to rename. Point the toolbox at a folder that contains music."
        )

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() > RenameColumn.CHECK or bottom_right.column() < RenameColumn.CHECK:
            return
        if roles is None or not roles or Qt.CheckStateRole in roles:
            self._update_rename_button_state()

    def _track_at_proxy_row(self, proxy_row: int) -> TrackMetadata | None:
        source_index = self._proxy_model.mapToSource(self._proxy_model.index(proxy_row, RenameColumn.CHECK))
        if not source_index.isValid():
            return None
        tracks = self._source_model.tracks()
        row = source_index.row()
        if 0 <= row < len(tracks):
            return tracks[row]
        return None

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        index = self._table.indexAt(pos)
        copy_action = None
        text = self._proxy_model.data(index, Qt.DisplayRole) if index.isValid() else None
        if text:
            copy_action = menu.addAction("Copy")
            menu.addSeparator()
        add_select_all_rows_action(menu, self._table)
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == copy_action:
            QApplication.clipboard().setText(str(text))

    def _rename_selected_files(self) -> None:
        if self._data_model is None:
            return

        checked = self._checked_tracks()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Tick one or more files in the table first.")
            return

        format_name = self._preset_combo.currentText()
        confirm = QMessageBox.question(
            self,
            "Confirm Rename",
            (
                f"Rename {len(checked)} selected file(s) to the “{format_name}” format?\n\n"
                "Matching .lrc lyrics files will be renamed with them. "
                "Review the suggested names first — this overwrites the current filenames."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        candidates = [
            rename_candidate_dict(track, self._data_model.root_path)
            for track in checked
            if track.rename_suggested
        ]
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Rename",
                "None of the selected files have a suggested filename.",
            )
            return

        self._start_rename(candidates)

    def _start_rename(self, candidates: list[dict[str, object]]) -> None:
        target_path = Path(self._data_model.root_path).resolve()

        progress = QProgressDialog(
            "Renaming selected files...",
            "Cancel",
            0,
            len(candidates),
            self,
        )
        progress.setWindowTitle("File Rename")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._rename_progress = progress
        self._rename_busy = True
        self._update_rename_button_state()

        self._rename_thread = QThread(self)
        self._rename_worker = FileRenameWorker(target_path=target_path, candidates=candidates)
        self._rename_worker.moveToThread(self._rename_thread)

        self._rename_thread.started.connect(self._rename_worker.run)
        self._rename_worker.progress.connect(self._on_rename_progress)
        self._rename_worker.finished.connect(self._on_rename_finished)
        self._rename_worker.cancelled.connect(self._on_rename_cancelled)
        self._rename_worker.failed.connect(self._on_rename_failed)

        self._rename_worker.finished.connect(self._rename_thread.quit)
        self._rename_worker.cancelled.connect(self._rename_thread.quit)
        self._rename_worker.failed.connect(self._rename_thread.quit)
        self._rename_thread.finished.connect(self._rename_worker.deleteLater)
        self._rename_thread.finished.connect(self._rename_thread.deleteLater)
        self._rename_thread.finished.connect(self._clear_rename_refs)

        progress.canceled.connect(self._cancel_rename)
        self._rename_thread.start()

    def _cancel_rename(self) -> None:
        if self._rename_worker is not None:
            self._rename_worker.request_cancel()
        if self._rename_progress is not None:
            self._rename_progress.setLabelText("Cancelling rename...")

    def _disconnect_rename_progress(self) -> None:
        worker = self._rename_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_rename_progress)
            except (RuntimeError, TypeError):
                pass

    def _clear_rename_refs(self) -> None:
        self._rename_worker = None
        self._rename_thread = None

    def cancel_running_job(self) -> None:
        self._cancel_rename()
        if self._rename_thread is not None and self._rename_thread.isRunning():
            self._rename_thread.wait(8000)

    @Slot(int, int, str)
    def _on_rename_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._rename_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._rename_progress = None

    def _finish_rename_ui(self) -> None:
        self._rename_busy = False
        self._disconnect_rename_progress()
        progress = self._rename_progress
        self._rename_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_rename_button_state()

    def _apply_processed_paths(self, processed_paths: list[dict[str, str]]) -> None:
        if self._data_model is None or not processed_paths:
            return

        for entry in processed_paths:
            old_path = entry.get("old_path", "")
            new_path = entry.get("new_path", "")
            if old_path and new_path and old_path != new_path:
                self._data_model.move_track(old_path, new_path)

            old_lrc = entry.get("old_lrc", "")
            new_lrc = entry.get("new_lrc", "")
            if old_lrc and new_lrc and old_lrc != new_lrc:
                moved = self._data_model.move_track(old_lrc, new_lrc)
                if moved is None:
                    lrc = Path(new_lrc)
                    if lrc.exists():
                        try:
                            size = lrc.stat().st_size
                        except OSError:
                            size = 0
                        self._data_model.add_track(
                            TrackMetadata(
                                filepath=str(lrc),
                                filename=lrc.name,
                                extension=".lrc",
                                size_bytes=size,
                                format_name="LRC Lyrics File",
                            )
                        )

            track = self._data_model.tracks.get(new_path)
            if track is not None and new_lrc:
                track.lyrics_lrc = Path(new_lrc).name

        self._data_model.rebuild_tree()

    def _refresh_suggestions(self, *, reset_checks: bool) -> None:
        if self._data_model is None:
            return
        tracks = [t for t in self._data_model.tracks.values() if is_rename_track(t)]
        apply_rename_evaluations(
            tracks,
            preset_id=self._current_preset_id(),
            reset_checks=reset_checks,
        )
        self._source_model.update_data(list(self._data_model.tracks.values()), self._data_model.root_path)
        self._update_rename_button_state()

    def _summarise_rename(self, payload: dict, *, cancelled: bool) -> None:
        renamed = int(payload.get("renamed") or 0)
        lrc_renamed = int(payload.get("lrc_renamed") or 0)
        failed = int(payload.get("failed") or 0)
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        if processed_paths:
            self._apply_processed_paths(processed_paths)
            self.library_changed.emit(self._data_model)

        prefix = "Rename cancelled" if cancelled else "Rename complete"
        summary = (
            f"{prefix}. Renamed files: {renamed} | "
            f"Renamed matching .lrc files: {lrc_renamed} | Failed: {failed}"
        )
        if cancelled:
            summary += "\nOperation cancelled before all selected files were processed."

        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(self, "Rename Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(
                self,
                "Rename Cancelled" if cancelled else "Rename Completed",
                summary,
            )

    @Slot(object)
    def _on_rename_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_rename_ui()
        self._summarise_rename(payload, cancelled=False)

    @Slot(object)
    def _on_rename_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_rename_ui()
        self._summarise_rename(payload, cancelled=True)

    @Slot(str)
    def _on_rename_failed(self, error: str) -> None:
        self._finish_rename_ui()
        QMessageBox.warning(self, "Rename Failed", error)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() != RenameColumn.CHECK:
            return
        modifiers = QApplication.keyboardModifiers()
        is_shift = bool(modifiers & Qt.ShiftModifier)

        state = self._proxy_model.data(index, Qt.CheckStateRole)
        is_checked = state in (Qt.Checked, Qt.CheckState.Checked, 2)
        new_val = Qt.Checked if is_checked else Qt.Unchecked
        current_row = index.row()

        if is_shift and getattr(self, "_last_checked_row", None) is not None:
            start = min(self._last_checked_row, current_row)
            end = max(self._last_checked_row, current_row)
            for r in range(start, end + 1):
                if r != current_row:
                    idx = self._proxy_model.index(r, RenameColumn.CHECK)
                    self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
        else:
            selection = self._table.selectionModel()
            if selection.isSelected(index):
                for selected_index in selection.selectedRows(RenameColumn.CHECK):
                    if selected_index.row() != current_row:
                        self._proxy_model.setData(selected_index, new_val, Qt.CheckStateRole)

        self._last_checked_row = current_row
        self._update_rename_button_state()

    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.set_search_query(text)

    def _on_preset_changed(self, _index: int = 0) -> None:
        if self._data_model is None or self._rename_busy:
            return
        self._refresh_suggestions(reset_checks=True)
        self._update_stats()

    def _on_status_filter_changed(self, text: str) -> None:
        self._proxy_model.set_status_filter(text)
        self._update_guidance()

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        with freeze_view(self._table):
            self._refresh_suggestions(reset_checks=True)

        header = self._table.horizontalHeader()
        font_metrics = header.fontMetrics()

        baselines = {
            RenameColumn.CHECK: 30,
            RenameColumn.TITLE: 200,
            RenameColumn.ARTIST: 150,
            RenameColumn.ALBUM: 150,
            RenameColumn.STATUS: 110,
            RenameColumn.REASON: 260,
            RenameColumn.CURRENT: 240,
            RenameColumn.SUGGESTED: 240,
            RenameColumn.TRACK_NO: 90,
        }

        for col in range(RenameColumn.COUNT):
            if col in baselines:
                text_width = font_metrics.horizontalAdvance(RenameColumn.HEADERS[col].upper()) + 45
                header.resizeSection(col, max(baselines[col], text_width))

        header.resizeSection(RenameColumn.FILE, PATH_COLUMN_WIDTH)
        self._update_rename_button_state()
        QTimer.singleShot(100, self._update_stats)

    def _update_stats(self) -> None:
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_rename.set_value(self._source_model.count_by_status("RENAME"))
        self._stat_matching.set_value(self._source_model.count_by_status("MATCHING"))
        self._stat_missing.set_value(self._source_model.count_by_status("MISSING"))
        self._stat_conflicts.set_value(self._source_model.count_by_status("CONFLICT"))
        self._update_guidance()

    def set_processing_state(self, is_processing: bool) -> None:
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        self._update_rename_button_state()
