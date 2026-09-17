from pathlib import Path

from PySide6.QtCore import Qt, QThread, QModelIndex, Signal, Slot
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
from ..models.drive_data import DriveDataModel
from ..models.file_cleanup_model import (
    CleanupColumn,
    FileCleanupFilterProxyModel,
    FileCleanupTableModel,
)
from ..threads.file_cleanup import FileCleanupDeleteWorker, FileCleanupScanWorker
from ..utils.file_cleanup import CATEGORY_ORDER, format_bytes
from .page_chrome import bind_search_field, filter_toolbar, flow_steps, loading_page, page_header
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


class FileCleanupWidget(QWidget):
    library_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model: DriveDataModel | None = None
        self._scan_target = ""
        self._last_checked_row = None
        self._processing = False
        self._ignore_populate = False
        self._scan_busy = False
        self._delete_busy = False
        self._scan_thread: QThread | None = None
        self._scan_worker: FileCleanupScanWorker | None = None
        self._delete_thread: QThread | None = None
        self._delete_worker: FileCleanupDeleteWorker | None = None
        self._delete_progress: QProgressDialog | None = None
        self._init_models()
        self._init_ui()
        self._connect_signals()

    def _init_models(self) -> None:
        self._source_model = FileCleanupTableModel(self)
        self._proxy_model = FileCleanupFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        self._scan_btn = QPushButton("Scan File Types")
        self._scan_btn.setMinimumHeight(34)
        self._scan_btn.setEnabled(False)
        self._scan_btn.setToolTip("Walk every file on the target and group them by extension.")

        self._remove_btn = QPushButton("Remove Selected Types")
        self._remove_btn.setObjectName("accentButton")
        self._remove_btn.setEnabled(False)
        self._remove_btn.setMinimumHeight(34)
        self._remove_btn.setToolTip("Tick file types in the table, then permanently delete those files.")

        layout.addWidget(page_header(
            "File Cleanup",
            "Group every file by type, then delete the categories you do not want on the player.",
            [self._scan_btn, self._remove_btn],
        ))

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page(
            "Hardware scan in progress.",
            "Waiting to group files by type...",
        ))

        self._status_page = QWidget()
        status_layout = QVBoxLayout(self._status_page)
        status_layout.setAlignment(Qt.AlignCenter)
        status_layout.setSpacing(6)
        self._status_title = QLabel("Grouping files by type.")
        self._status_title.setObjectName("headerTitle")
        self._status_title.setAlignment(Qt.AlignCenter)
        self._status_subtitle = QLabel("Discovering files...")
        self._status_subtitle.setObjectName("headerSubtitle")
        self._status_subtitle.setAlignment(Qt.AlignCenter)
        self._status_subtitle.setWordWrap(True)
        status_layout.addWidget(self._status_title)
        status_layout.addWidget(self._status_subtitle)
        self._stack.addWidget(self._status_page)

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        data_layout.addWidget(flow_steps([
            (
                "1",
                "Review the groups",
                "Every file is listed by extension and category, including hidden macOS junk.",
            ),
            (
                "2",
                "Tick what to remove",
                "Hidden files are ticked by default. Leave Audio alone unless you mean to delete tracks.",
            ),
            (
                "3",
                "Delete the files",
                "Removal is permanent. Other tabs update if music or lyrics files were deleted.",
            ),
        ]))

        toolbar_panel, toolbar = filter_toolbar()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            "Search by extension, category or description…"
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        show_lbl = QLabel("SHOW")
        show_lbl.setObjectName("toolbarLabel")
        toolbar.addWidget(show_lbl)

        self._category_combo = QComboBox()
        self._category_combo.addItem("All Categories")
        for category in CATEGORY_ORDER:
            self._category_combo.addItem(category)
        self._category_combo.setMinimumHeight(34)
        self._category_combo.setMinimumWidth(170)
        self._category_combo.setStyleSheet(_COMBO_STYLE)
        self._category_combo.setToolTip("Focus the table on one category, such as Hidden or Video.")
        toolbar.addWidget(self._category_combo)

        data_layout.addWidget(toolbar_panel)

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self._stat_files = StatCard("Total Files", Colours.STAT_TOTAL, self)
        self._stat_types = StatCard("File Types", Colours.STATUS_UNKNOWN, self)
        self._stat_hidden = StatCard("Hidden Files", Colours.STATUS_INCOMPATIBLE, self)
        self._stat_audio = StatCard("Audio Files", Colours.STATUS_SUPPORTED, self)
        self._stat_size = StatCard("Total Size", Colours.STATUS_LIMITED, self)

        stats_layout.addWidget(self._stat_files)
        stats_layout.addWidget(self._stat_types)
        stats_layout.addWidget(self._stat_hidden)
        stats_layout.addWidget(self._stat_audio)
        stats_layout.addWidget(self._stat_size)
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
        header_view.add_group("Type", 1, 3)
        header_view.add_group("Totals", 4, 5)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, CleanupColumn.COUNT_COLUMNS):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)
        wire_select_all_rows(self._table, toolbar, owns_menu=False)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        bind_search_field(self._search_input, self._on_search_changed)
        self._category_combo.currentTextChanged.connect(self._on_category_filter_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._scan_btn.clicked.connect(self._start_scan)
        self._remove_btn.clicked.connect(self._remove_selected_types)
        self._source_model.dataChanged.connect(self._on_source_data_changed)
        self._proxy_model.set_category_filter(self._category_combo.currentText())
        self._update_guidance()

    def _checked_rows(self):
        return [row for row in self._source_model.rows() if row.is_checked and row.count > 0]

    def _is_ready(self) -> bool:
        return (
            self._data_model is not None
            and self._stack.currentIndex() == 2
            and not self._scan_busy
            and not self._delete_busy
        )

    def _update_action_state(self) -> None:
        has_target = self._data_model is not None and bool(self._data_model.root_path)
        idle = not self._scan_busy and not self._delete_busy
        self._scan_btn.setEnabled(has_target and idle)

        checked = self._checked_rows()
        ready = self._is_ready() and bool(checked)
        self._remove_btn.setEnabled(ready)
        if checked:
            file_count = sum(row.count for row in checked)
            type_count = len(checked)
            type_noun = "type" if type_count == 1 else "types"
            file_noun = "file" if file_count == 1 else "files"
            self._remove_btn.setText(f"Remove {file_count:,} {file_noun}")
            self._remove_btn.setToolTip(
                f"Permanently delete {file_count:,} {file_noun} across {type_count} {type_noun}."
            )
        else:
            self._remove_btn.setText("Remove Selected Types")
            self._remove_btn.setToolTip("Tick file types in the table, then permanently delete those files.")
        self._update_guidance()

    def _update_guidance(self) -> None:
        if not hasattr(self, "_guidance_lbl"):
            return
        if self._data_model is None:
            self._guidance_lbl.setText(
                "Scan a drive or folder first. This page then groups every file by type, not just music."
            )
            return
        if self._stack.currentIndex() != 2:
            self._guidance_lbl.setText("Grouping files by type…")
            return

        hidden = self._source_model.count_by_category("Hidden")
        audio = self._source_model.count_by_category("Audio")
        checked = self._checked_rows()
        showing = self._category_combo.currentText()

        if checked:
            file_count = sum(row.count for row in checked)
            labels = ", ".join(f"{row.category} ({row.file_type})" for row in checked[:6])
            extra = "…" if len(checked) > 6 else ""
            audio_warning = ""
            if any(row.category == "Audio" for row in checked):
                audio_warning = " This includes Audio — those tracks will be deleted from the library."
            self._guidance_lbl.setText(
                f"Ready to remove {file_count:,} "
                f"{'file' if file_count == 1 else 'files'}: {labels}{extra}.{audio_warning}"
            )
            return

        if hidden:
            extra = ""
            if showing != "Hidden":
                extra = ' Switch Show to “Hidden” to focus on them.'
            self._guidance_lbl.setText(
                f"{hidden:,} hidden "
                f"{'file is' if hidden == 1 else 'files are'} ticked by default "
                f"(.DS_Store, macOS sidecars, Thumbs.db). Review, then remove.{extra}"
            )
            return

        if self._source_model.total_files() == 0:
            self._guidance_lbl.setText(
                "No files found on this target. Point the toolbox at a folder that contains files."
            )
            return

        extra = ""
        if audio and showing == "All Categories":
            extra = f" {audio:,} audio files are listed so you can see the library — leave them unticked."
        self._guidance_lbl.setText(
            f"Found {self._source_model.total_files():,} files across "
            f"{self._source_model.type_count()} types. Tick any category you do not want to keep.{extra}"
        )

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() > CleanupColumn.CHECK or bottom_right.column() < CleanupColumn.CHECK:
            return
        if roles is None or not roles or Qt.CheckStateRole in roles:
            self._update_action_state()

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

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() != CleanupColumn.CHECK:
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
            for row in range(start, end + 1):
                if row != current_row:
                    idx = self._proxy_model.index(row, CleanupColumn.CHECK)
                    self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
        else:
            selection = self._table.selectionModel()
            if selection.isSelected(index):
                for selected_index in selection.selectedRows(CleanupColumn.CHECK):
                    if selected_index.row() != current_row:
                        self._proxy_model.setData(selected_index, new_val, Qt.CheckStateRole)

        self._last_checked_row = current_row
        self._update_action_state()

    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.set_search_query(text)

    def _on_category_filter_changed(self, text: str) -> None:
        self._proxy_model.set_category_filter(text)
        self._update_guidance()

    def _current_target(self) -> str:
        if self._data_model is None:
            return ""
        try:
            return str(Path(self._data_model.root_path).expanduser().resolve())
        except OSError:
            return self._data_model.root_path

    def _show_status(self, title: str, subtitle: str) -> None:
        self._status_title.setText(title)
        self._status_subtitle.setText(subtitle)
        self._stack.setCurrentIndex(1)
        self._update_action_state()

    def _start_scan(self) -> None:
        target = self._current_target()
        if not target:
            QMessageBox.information(self, "No Target", "Choose a folder or drive before scanning file types.")
            return
        target_path = Path(target)
        if not target_path.exists() or not target_path.is_dir():
            QMessageBox.warning(self, "Invalid Target", "The selected target path is not a valid folder.")
            return

        self._cancel_scan()
        self._scan_busy = True
        self._update_action_state()
        self._show_status("Grouping files by type.", "Discovering files...")

        self._scan_thread = QThread(self)
        self._scan_worker = FileCleanupScanWorker(target)
        self._scan_worker.moveToThread(self._scan_thread)

        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.cancelled.connect(self._on_scan_cancelled)
        self._scan_worker.failed.connect(self._on_scan_failed)

        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.cancelled.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.finished.connect(self._clear_scan_refs)

        self._scan_thread.start()

    def _cancel_scan(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.request_cancel()

    def _clear_scan_refs(self) -> None:
        if self.sender() is not self._scan_thread:
            return
        self._scan_worker = None
        self._scan_thread = None

    @Slot(int, object, str)
    def _on_scan_progress(self, scanned_files: int, total_bytes, detail: str) -> None:
        if self.sender() is not self._scan_worker or self._processing:
            return
        self._status_subtitle.setText(
            detail or f"Scanning… {scanned_files:,} files · {format_bytes(int(total_bytes or 0))}"
        )

    @Slot()
    def _on_scan_cancelled(self) -> None:
        if self.sender() is not self._scan_worker:
            return
        self._scan_busy = False
        if self._processing:
            return
        self._show_status("Scan cancelled.", "Click Scan File Types to try again.")

    @Slot(str)
    def _on_scan_failed(self, error: str) -> None:
        if self.sender() is not self._scan_worker:
            return
        self._scan_busy = False
        if self._processing:
            return
        self._show_status("Scan failed.", error)

    @Slot(object)
    def _on_scan_finished(self, payload_obj) -> None:
        if self.sender() is not self._scan_worker:
            return
        self._scan_busy = False
        if self._processing:
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        target = str(payload.get("target_path") or "")
        if target and self._current_target() and target != self._current_target():
            self._show_status(
                "Target changed.",
                "Click Scan File Types for updated cleanup data.",
            )
            return

        rows = payload.get("rows") or []
        self._scan_target = target or self._current_target()
        self._source_model.update_data(rows)
        self._resize_columns()
        self._update_stats()
        self._stack.setCurrentIndex(2)
        self._update_action_state()

    def _resize_columns(self) -> None:
        header = self._table.horizontalHeader()
        font_metrics = header.fontMetrics()
        baselines = {
            CleanupColumn.CHECK: 30,
            CleanupColumn.EXTENSION: 180,
            CleanupColumn.CATEGORY: 110,
            CleanupColumn.DESCRIPTION: 240,
            CleanupColumn.COUNT: 90,
            CleanupColumn.SIZE: 120,
        }
        for col, width in baselines.items():
            text_width = font_metrics.horizontalAdvance(CleanupColumn.HEADERS[col].upper()) + 45
            header.resizeSection(col, max(width, text_width))

    def _update_stats(self) -> None:
        self._stat_files.set_value(self._source_model.total_files())
        self._stat_types.set_value(self._source_model.type_count())
        self._stat_hidden.set_value(self._source_model.count_by_category("Hidden"))
        self._stat_audio.set_value(self._source_model.count_by_category("Audio"))
        self._stat_size.set_text(format_bytes(self._source_model.total_bytes()))
        self._update_guidance()

    def _remove_selected_types(self) -> None:
        if self._data_model is None:
            return
        if not self._scan_target:
            QMessageBox.information(self, "No Scan Data", "Scan file types before removing categories.")
            return

        current_target = self._current_target()
        if current_target != self._scan_target:
            QMessageBox.information(
                self,
                "Target Changed",
                "The target path changed since the last cleanup scan. Run Scan File Types again.",
            )
            return

        checked = self._checked_rows()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Select one or more file types to remove.")
            return

        files_to_remove: list[str] = []
        labels: list[str] = []
        for row in checked:
            files_to_remove.extend(row.files)
            labels.append(f"{row.category} ({row.file_type})")

        if not files_to_remove:
            QMessageBox.information(self, "Nothing To Remove", "No files found for selected file types.")
            return

        includes_audio = any(row.category == "Audio" for row in checked)
        message = (
            f"Remove {len(files_to_remove):,} files from selected types:\n"
            f"{', '.join(labels)}?\n\n"
            "This permanently deletes the files from the target."
        )
        if includes_audio:
            message += (
                "\n\nAudio is included. Those tracks will disappear from the library "
                "and from the player."
            )

        confirm = QMessageBox.question(
            self,
            "Confirm File Removal",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self._start_delete(files_to_remove)

    def _start_delete(self, file_paths: list[str]) -> None:
        target_path = Path(self._scan_target)

        progress = QProgressDialog(
            "Removing selected file types...",
            "Cancel",
            0,
            len(file_paths),
            self,
        )
        progress.setWindowTitle("File Cleanup")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._delete_progress = progress
        self._delete_busy = True
        self._update_action_state()

        self._delete_thread = QThread(self)
        self._delete_worker = FileCleanupDeleteWorker(target_path, file_paths)
        self._delete_worker.moveToThread(self._delete_thread)

        self._delete_thread.started.connect(self._delete_worker.run)
        self._delete_worker.progress.connect(self._on_delete_progress)
        self._delete_worker.finished.connect(self._on_delete_finished)
        self._delete_worker.cancelled.connect(self._on_delete_cancelled)
        self._delete_worker.failed.connect(self._on_delete_failed)

        self._delete_worker.finished.connect(self._delete_thread.quit)
        self._delete_worker.cancelled.connect(self._delete_thread.quit)
        self._delete_worker.failed.connect(self._delete_thread.quit)
        self._delete_thread.finished.connect(self._delete_worker.deleteLater)
        self._delete_thread.finished.connect(self._delete_thread.deleteLater)
        self._delete_thread.finished.connect(self._clear_delete_refs)

        progress.canceled.connect(self._cancel_delete)
        self._delete_thread.start()

    def _cancel_delete(self) -> None:
        if self._delete_worker is not None:
            self._delete_worker.request_cancel()
        if self._delete_progress is not None:
            self._delete_progress.setLabelText("Cancelling cleanup...")

    def _disconnect_delete_progress(self) -> None:
        worker = self._delete_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_delete_progress)
            except (RuntimeError, TypeError):
                pass

    def _clear_delete_refs(self) -> None:
        if self.sender() is not self._delete_thread:
            return
        self._delete_worker = None
        self._delete_thread = None

    @Slot(int, int, str)
    def _on_delete_progress(self, processed: int, total: int, detail: str) -> None:
        if self.sender() is not self._delete_worker:
            return
        progress = self._delete_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._delete_progress = None

    def _finish_delete_ui(self) -> None:
        self._delete_busy = False
        self._disconnect_delete_progress()
        progress = self._delete_progress
        self._delete_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_action_state()

    def _apply_removed_paths(self, removed_paths: list[str]) -> int:
        if self._data_model is None or not removed_paths:
            return 0
        dropped = 0
        for path in removed_paths:
            if self._data_model.remove_track(path) is not None:
                dropped += 1
        if dropped:
            self._data_model.rebuild_tree()
        return dropped

    def _summarise_delete(self, payload: dict, *, cancelled: bool) -> None:
        removed = int(payload.get("removed") or 0)
        failed = int(payload.get("failed") or 0)
        failures = payload.get("failures") or []
        removed_paths = payload.get("removed_paths") or []

        dropped = self._apply_removed_paths(list(removed_paths))
        self._ignore_populate = True
        try:
            if dropped:
                self.library_changed.emit(self._data_model)
        finally:
            self._ignore_populate = False

        prefix = "Cleanup cancelled" if cancelled else "Cleanup complete"
        summary = f"{prefix}. Removed files: {removed} | Failed: {failed}"
        if cancelled:
            summary += "\nOperation cancelled before all selected files were processed."
        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(self, "Cleanup Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(
                self,
                "Cleanup Cancelled" if cancelled else "Cleanup Completed",
                summary,
            )

        if self._data_model is not None:
            self._start_scan()

    @Slot(object)
    def _on_delete_finished(self, payload_obj) -> None:
        if self.sender() is not self._delete_worker:
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_delete_ui()
        if self._processing:
            return
        self._summarise_delete(payload, cancelled=False)

    @Slot(object)
    def _on_delete_cancelled(self, payload_obj) -> None:
        if self.sender() is not self._delete_worker:
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_delete_ui()
        if self._processing:
            return
        self._summarise_delete(payload, cancelled=True)

    @Slot(str)
    def _on_delete_failed(self, error: str) -> None:
        if self.sender() is not self._delete_worker:
            return
        self._finish_delete_ui()
        if self._processing:
            return
        QMessageBox.warning(self, "Cleanup Failed", error)

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        if self._delete_busy or self._ignore_populate:
            return
        self._start_scan()

    def set_processing_state(self, is_processing: bool) -> None:
        self._processing = is_processing
        if is_processing:
            self._cancel_scan()
            self._cancel_delete()
            self._stack.setCurrentIndex(0)
        self._update_action_state()

    def cancel_running_job(self) -> None:
        self._cancel_scan()
        self._cancel_delete()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.wait(2000)
        if self._delete_thread is not None and self._delete_thread.isRunning():
            self._delete_thread.wait(2000)
