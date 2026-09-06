from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QModelIndex, Slot
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableView,
    QLabel,
    QStackedWidget,
    QLineEdit,
    QComboBox,
    QPushButton,
    QProgressDialog,
    QMessageBox,
    QApplication,
    QStyledItemDelegate,
    QDialog,
    QMenu,
)
from PySide6.QtGui import QBrush

from ..theme import Colours
from ..models.drive_data import DriveDataModel, TrackMetadata
from ..models.lyrics_model import (
    LyricsTableModel,
    LyricsFilterProxyModel,
    LyricsColumn,
)
from ..threads.lyrics_lookup import LyricsLookupWorker
from ..threads.lyrics_write import LyricsWriteWorker
from ..utils.lyrics import lyrics_text_for_preview
from ..utils.lyrics_lookup import LyricsLookupClient
from ..utils.lyrics_planner import apply_lyrics_evaluation
from .lyrics_convert_dialog import LyricsConvertDialog
from .lyrics_lookup_dialog import LyricsLookupDialog
from .lyrics_preview_dialog import LyricsPreviewDialog
from .stat_card import StatCard
from .grouped_header_view import GroupedHeaderView


class HighlightDelegate(QStyledItemDelegate):
    """Custom delegate to enforce background colors over QSS/Alternating rows."""

    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if bg:
            painter.fillRect(option.rect, bg)
            option.backgroundBrush = QBrush(Qt.NoBrush)
        super().paint(painter, option, index)


class LyricsManagerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model = None
        self._last_checked_row = None
        self._write_busy = False
        self._write_thread: QThread | None = None
        self._write_worker: LyricsWriteWorker | None = None
        self._write_progress: QProgressDialog | None = None
        self._lookup_busy = False
        self._lookup_thread: QThread | None = None
        self._lookup_worker = None
        self._lookup_progress: QProgressDialog | None = None
        self._lookup_client: LyricsLookupClient | None = None
        self._init_models()
        self._init_ui()
        self._connect_signals()

    def _init_models(self) -> None:
        self._source_model = LyricsTableModel(self)
        self._proxy_model = LyricsFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(20)

        title_container = QWidget()
        title_layout = QVBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)

        title = QLabel("Lyrics Manager")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        subtitle = QLabel("Convert embedded lyrics and fetch missing .lrc sidecars")
        subtitle.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")

        self._convert_btn = QPushButton("Convert Embedded Lyrics")
        self._convert_btn.setObjectName("accentButton")
        self._convert_btn.setEnabled(False)
        self._convert_btn.setMinimumHeight(34)

        self._lookup_btn = QPushButton("Find Missing Lyrics")
        self._lookup_btn.setObjectName("accentButton")
        self._lookup_btn.setEnabled(False)
        self._lookup_btn.setMinimumHeight(34)
        self._lookup_btn.setToolTip(
            "Look up lyrics on LRCLIB for the selected tracks that have no matching .lrc file."
        )

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self._convert_btn)
        button_row.addWidget(self._lookup_btn)
        button_row.addStretch(1)

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        title_layout.addSpacing(10)
        title_layout.addLayout(button_row)
        title_layout.addStretch()

        h_layout.addWidget(title_container)
        h_layout.addStretch(1)

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self._stat_total = StatCard("Total Scanned", Colours.STAT_TOTAL, self)
        self._stat_compatible = StatCard("Compatible", Colours.STATUS_COMPATIBLE, self)
        self._stat_embedded = StatCard("Embedded Only", Colours.STATUS_INCOMPATIBLE, self)
        self._stat_missing = StatCard("Missing Lyrics", Colours.STATUS_MISSING, self)
        self._stat_errors = StatCard("Errors", Colours.STATUS_UNKNOWN, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_compatible)
        stats_layout.addWidget(self._stat_embedded)
        stats_layout.addWidget(self._stat_missing)
        stats_layout.addWidget(self._stat_errors)

        h_layout.addLayout(stats_layout)
        layout.addWidget(header)

        self._stack = QStackedWidget()

        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignCenter)

        load_lbl = QLabel("Hardware scan in progress.")
        load_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 20px; font-weight: 700;")
        load_lbl.setAlignment(Qt.AlignCenter)

        sub_lbl = QLabel("Lyrics analysis running...")
        sub_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 14px;")
        sub_lbl.setAlignment(Qt.AlignCenter)

        loading_layout.addWidget(load_lbl)
        loading_layout.addWidget(sub_lbl)
        self._stack.addWidget(loading_page)

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        toolbar_panel = QWidget()
        toolbar = QHBoxLayout(toolbar_panel)
        toolbar.setContentsMargins(0, 4, 0, 4)
        toolbar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            "Search by status, artist, album, file name or lyrics source…"
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        self._status_combo = QComboBox()
        self._status_combo.addItems(
            ["All Statuses", "Compatible", "Embedded Only", "Missing Lyrics", "Errors"]
        )
        self._status_combo.setMinimumHeight(34)
        self._status_combo.setMinimumWidth(150)
        self._status_combo.setStyleSheet(f"""
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
        """)
        toolbar.addWidget(self._status_combo)

        data_layout.addWidget(toolbar_panel)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
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
        header_view.add_group("Lyrics", 6, 9)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, LyricsColumn.COUNT):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        self._search_input.textChanged.connect(self._on_search_changed)
        self._status_combo.currentTextChanged.connect(self._on_status_filter_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._table.doubleClicked.connect(self._on_table_double_clicked)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._convert_btn.clicked.connect(self._open_convert_dialog)
        self._lookup_btn.clicked.connect(self._start_lookup)
        self._source_model.dataChanged.connect(self._on_source_data_changed)

    def _checked_tracks(self) -> list[TrackMetadata]:
        return [track for track in self._source_model.tracks() if track.lyrics_is_checked]

    def _is_ready(self) -> bool:
        return (
            self._data_model is not None
            and self._stack.currentIndex() == 1
            and not self._write_busy
            and not self._lookup_busy
        )

    def _update_action_button_state(self) -> None:
        checked = self._checked_tracks()
        ready = self._is_ready()
        has_checked = bool(checked)
        can_convert = any(track.has_lyrics for track in checked)
        self._convert_btn.setEnabled(has_checked and ready and can_convert)
        self._lookup_btn.setEnabled(has_checked and ready)

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() > LyricsColumn.CHECK or bottom_right.column() < LyricsColumn.CHECK:
            return
        if roles is None or not roles or Qt.CheckStateRole in roles:
            self._update_action_button_state()

    def _track_at_proxy_row(self, proxy_row: int) -> TrackMetadata | None:
        source_index = self._proxy_model.mapToSource(self._proxy_model.index(proxy_row, LyricsColumn.CHECK))
        if not source_index.isValid():
            return None
        tracks = self._source_model.tracks()
        row = source_index.row()
        if 0 <= row < len(tracks):
            return tracks[row]
        return None

    def _show_preview_for_track(self, track: TrackMetadata) -> None:
        lyrics_text = lyrics_text_for_preview(track)
        if not lyrics_text.strip():
            QMessageBox.information(self, "No Lyrics", "No lyrics are available to preview for this track.")
            return
        dialog = LyricsPreviewDialog(track.title or track.filename, lyrics_text, self)
        dialog.exec()

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid() or index.column() == LyricsColumn.CHECK:
            return
        track = self._track_at_proxy_row(index.row())
        if track is not None:
            self._show_preview_for_track(track)

    def _show_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        track = self._track_at_proxy_row(index.row())
        if track is None:
            return
        menu = QMenu(self)
        preview_action = menu.addAction("Preview Full Lyrics")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == preview_action:
            self._show_preview_for_track(track)

    def _open_convert_dialog(self) -> None:
        if self._data_model is None:
            return

        checked = self._checked_tracks()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Tick one or more files in the table first.")
            return

        dialog = LyricsConvertDialog(checked, self._data_model.root_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        candidates = dialog.candidates()
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Convert",
                "None of the selected files have embedded lyrics to export.",
            )
            return

        self._start_write(
            candidates=candidates,
            dry_run=dialog.dry_run,
            backup_root=dialog.backup_root,
            title="Previewing lyrics conversion..." if dialog.dry_run else "Writing .lrc sidecars...",
            window_title="Convert Embedded Lyrics",
        )

    def _start_lookup(self) -> None:
        if self._data_model is None or self._lookup_busy:
            return

        checked = [track for track in self._checked_tracks() if track.lyrics_status != "COMPATIBLE"]
        if not checked:
            QMessageBox.information(
                self,
                "Nothing To Look Up",
                "Tick one or more rows that do not already have a matching .lrc file.",
            )
            return

        self._lookup_client = LyricsLookupClient()

        progress = QProgressDialog(
            f"Searching {len(checked)} track(s) on LRCLIB...",
            "Cancel",
            0,
            len(checked),
            self,
        )
        progress.setWindowTitle("Find Missing Lyrics")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._lookup_progress = progress
        self._lookup_busy = True
        self._update_action_button_state()

        thread = QThread(self)
        worker = LyricsLookupWorker(checked, self._data_model.root_path, client=self._lookup_client)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_lookup_progress)
        worker.finished.connect(self._on_lookup_finished)
        worker.cancelled.connect(self._on_lookup_cancelled)
        worker.failed.connect(self._on_lookup_failed)

        worker.finished.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_lookup_refs)

        progress.canceled.connect(self._cancel_lookup)
        self._lookup_thread = thread
        self._lookup_worker = worker
        thread.start()

    @Slot(object)
    def _on_lookup_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        results = payload.get("results") or []
        found = int(payload.get("found") or 0)

        self._finish_lookup_ui()

        if not results:
            return

        if not found:
            QMessageBox.information(
                self,
                "No Lyrics Found",
                "No lyrics were found for the selected tracks.",
            )

        self._open_lookup_dialog(results)

    @Slot(object)
    def _on_lookup_cancelled(self, payload_obj) -> None:
        self._finish_lookup_ui()
        QMessageBox.information(self, "Search Cancelled", "Lyrics search was cancelled.")

    def _open_lookup_dialog(self, results) -> None:
        if self._data_model is None:
            return

        dialog = LyricsLookupDialog(results, self._data_model.root_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        candidates = dialog.candidates()
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Apply",
                "No lookup results were selected to write.",
            )
            return

        self._start_write(
            candidates=candidates,
            dry_run=dialog.dry_run,
            backup_root=dialog.backup_root,
            title=(
                "Previewing lyrics download..." if dialog.dry_run
                else f"Writing .lrc files for {len(candidates)} track(s)..."
            ),
            window_title="Find Missing Lyrics",
        )

    def _start_write(
        self,
        *,
        candidates: list[dict[str, object]],
        dry_run: bool,
        backup_root: Path | None,
        title: str,
        window_title: str,
    ) -> None:
        target_path = Path(self._data_model.root_path).resolve()

        progress = QProgressDialog(title, "Cancel", 0, len(candidates), self)
        progress.setWindowTitle(window_title)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._write_progress = progress
        self._write_busy = True
        self._update_action_button_state()

        thread = QThread(self)
        worker = LyricsWriteWorker(
            target_path=target_path,
            candidates=candidates,
            dry_run=dry_run,
            backup_root=backup_root,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_write_progress)
        worker.finished.connect(self._on_write_finished)
        worker.cancelled.connect(self._on_write_cancelled)
        worker.failed.connect(self._on_write_failed)

        worker.finished.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_write_refs)

        progress.canceled.connect(self._cancel_write)
        self._write_thread = thread
        self._write_worker = worker
        thread.start()

    def _cancel_write(self) -> None:
        if self._write_worker is not None:
            self._write_worker.request_cancel()
        if self._write_progress is not None:
            self._write_progress.setLabelText("Cancelling...")

    def _cancel_lookup(self) -> None:
        if self._lookup_worker is not None:
            self._lookup_worker.request_cancel()
        if self._lookup_progress is not None:
            self._lookup_progress.setLabelText("Cancelling...")

    def _clear_write_refs(self) -> None:
        self._write_worker = None
        self._write_thread = None

    def _clear_lookup_refs(self) -> None:
        self._lookup_worker = None
        self._lookup_thread = None

    @Slot(int, int, str)
    def _on_write_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._write_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._write_progress = None

    @Slot(int, int, str)
    def _on_lookup_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._lookup_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._lookup_progress = None

    def _finish_write_ui(self) -> None:
        self._write_busy = False
        worker = self._write_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_write_progress)
            except (RuntimeError, TypeError):
                pass
        progress = self._write_progress
        self._write_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_action_button_state()

    def _finish_lookup_ui(self) -> None:
        self._lookup_busy = False
        worker = self._lookup_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_lookup_progress)
            except (RuntimeError, TypeError):
                pass
        progress = self._lookup_progress
        self._lookup_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_action_button_state()

    def _refresh_processed_tracks(self, processed_paths: list[dict[str, str]]) -> None:
        if self._data_model is None or not processed_paths:
            return

        changed = False
        for entry in processed_paths:
            filepath = entry.get("filepath", "")
            if not filepath:
                continue

            track = self._data_model.tracks.get(filepath)
            if track is None:
                continue

            apply_lyrics_evaluation(track)
            track.lyrics_is_checked = False
            changed = True

            lrc_path = entry.get("lrc_path") or str(Path(filepath).with_suffix(".lrc"))
            if lrc_path and lrc_path not in self._data_model.tracks:
                lrc = Path(lrc_path)
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

        if changed:
            tracks = list(self._data_model.tracks.values())
            self._source_model.update_data(tracks, self._data_model.root_path)
            self._update_stats()

    def _summarise_write(self, payload: dict, *, cancelled: bool) -> None:
        written = int(payload.get("written") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        backup_root = str(payload.get("backup_root") or "")
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        if dry_run:
            prefix = "Dry run cancelled" if cancelled else "Dry run complete"
            summary = f"{prefix}. Files that would receive a .lrc sidecar: {planned}."
            if failed:
                preview = "\n".join(str(item) for item in failures[:15])
                QMessageBox.warning(self, "Dry Run Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
            else:
                QMessageBox.information(self, "Dry Run Completed" if not cancelled else "Dry Run Cancelled", summary)
            return

        if processed_paths:
            self._refresh_processed_tracks(processed_paths)

        prefix = "Lyrics write cancelled" if cancelled else "Lyrics write complete"
        summary = f"{prefix}. Files written: {written} | Failed: {failed}"
        if backup_root:
            summary += f"\nBackups saved to: {backup_root}"

        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(self, "Write Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Write Completed" if not cancelled else "Write Cancelled", summary)

    @Slot(object)
    def _on_write_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_write_ui()
        self._summarise_write(payload, cancelled=False)

    @Slot(object)
    def _on_write_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._finish_write_ui()
        self._summarise_write(payload, cancelled=True)

    @Slot(str)
    def _on_write_failed(self, error: str) -> None:
        self._finish_write_ui()
        QMessageBox.warning(self, "Write Failed", error)

    @Slot(str)
    def _on_lookup_failed(self, error: str) -> None:
        self._finish_lookup_ui()
        QMessageBox.warning(self, "Lookup Failed", error)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() == LyricsColumn.CHECK:
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
                        idx = self._proxy_model.index(r, LyricsColumn.CHECK)
                        self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
            else:
                selection = self._table.selectionModel()
                if selection.isSelected(index):
                    for selected_index in selection.selectedRows(LyricsColumn.CHECK):
                        if selected_index.row() != current_row:
                            self._proxy_model.setData(selected_index, new_val, Qt.CheckStateRole)

            self._last_checked_row = current_row
            self._update_action_button_state()

    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.set_search_query(text)

    def _on_status_filter_changed(self, text: str) -> None:
        self._proxy_model.set_status_filter(text)

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        tracks = list(data_model.tracks.values())

        for track in tracks:
            track.lyrics_is_checked = False

        self._source_model.update_data(tracks, data_model.root_path)

        header = self._table.horizontalHeader()
        font_metrics = header.fontMetrics()

        baselines = {
            LyricsColumn.CHECK: 30,
            LyricsColumn.TITLE: 200,
            LyricsColumn.ARTIST: 150,
            LyricsColumn.ALBUM: 150,
            LyricsColumn.STATUS: 120,
            LyricsColumn.REASON: 300,
            LyricsColumn.EMBEDDED: 110,
            LyricsColumn.LRC: 140,
            LyricsColumn.SOURCE: 140,
            LyricsColumn.PREVIEW: 240,
        }

        for col in range(LyricsColumn.COUNT):
            if col in baselines:
                text_width = font_metrics.horizontalAdvance(LyricsColumn.HEADERS[col].upper()) + 45
                header.resizeSection(col, max(baselines[col], text_width))

        self._table.resizeColumnToContents(LyricsColumn.FILE)
        self._update_action_button_state()
        QTimer.singleShot(100, self._update_stats)

    def _update_stats(self) -> None:
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_compatible.set_value(self._source_model.count_by_status("COMPATIBLE"))
        self._stat_embedded.set_value(self._source_model.count_by_status("INCOMPATIBLE"))
        self._stat_missing.set_value(self._source_model.count_by_status("MISSING"))
        self._stat_errors.set_value(self._source_model.count_by_status("UNKNOWN"))

    def set_processing_state(self, is_processing: bool) -> None:
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        self._update_action_button_state()
