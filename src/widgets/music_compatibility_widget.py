import os
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
)
from PySide6.QtGui import QBrush

from ..theme import Colours
from ..models.drive_data import DriveDataModel, TrackMetadata
from ..models.music_compatibility_model import (
    MusicCompatibilityTableModel,
    MusicCompatibilityFilterProxyModel,
    CompColumn,
)
from ..threads.music_conversion import MusicConversionWorker
from ..utils.music_compatibility import _resolve_ffmpeg_executable, evaluate_music_file
from ..utils.music_conversion_planner import apply_compatibility_result
from .music_conversion_dialog import MusicConversionDialog
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


class MusicCompatibilityWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model = None
        self._last_checked_row = None
        self._conversion_busy = False
        self._conversion_thread: QThread | None = None
        self._conversion_worker: MusicConversionWorker | None = None
        self._conversion_progress: QProgressDialog | None = None
        self._init_models()
        self._init_ui()
        self._connect_signals()

    def _init_models(self) -> None:
        self._source_model = MusicCompatibilityTableModel(self)
        self._proxy_model = MusicCompatibilityFilterProxyModel(self)
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

        title = QLabel("Music Compatibility")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        subtitle = QLabel("Check and convert unsupported media files")
        subtitle.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")

        self._convert_btn = QPushButton("Convert Selected Incompatible Media")
        self._convert_btn.setObjectName("accentButton")
        self._convert_btn.setEnabled(False)
        self._convert_btn.setMinimumHeight(34)

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        title_layout.addSpacing(10)
        title_layout.addWidget(self._convert_btn)
        title_layout.addStretch()

        h_layout.addWidget(title_container)
        h_layout.addStretch(1)

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self._stat_total = StatCard("Total Scanned", Colours.STAT_TOTAL, self)
        self._stat_supported = StatCard("Supported", Colours.STAT_TITLE, self)
        self._stat_limited = StatCard("Limited", Colours.STAT_ARTIST, self)
        self._stat_unsupported = StatCard("Unsupported", Colours.STAT_MISSING, self)
        self._stat_no_eq = StatCard("No EQ Support", Colours.STAT_ALBUM, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_supported)
        stats_layout.addWidget(self._stat_limited)
        stats_layout.addWidget(self._stat_unsupported)
        stats_layout.addWidget(self._stat_no_eq)

        h_layout.addLayout(stats_layout)
        layout.addWidget(header)

        self._stack = QStackedWidget()

        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignCenter)

        load_lbl = QLabel("Hardware scan in progress.")
        load_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 20px; font-weight: 700;")
        load_lbl.setAlignment(Qt.AlignCenter)

        sub_lbl = QLabel("Deep ffprobe analysis running...")
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
        self._search_input.setPlaceholderText("Search by status, artist, song name, file name or codec…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        self._status_combo = QComboBox()
        self._status_combo.addItems(["All Statuses", "Supported", "Limited", "Unsupported", "Unknown", "Skipped"])
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
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)

        header_view = GroupedHeaderView(self._table)
        self._table.setHorizontalHeader(header_view)
        header_view.setStretchLastSection(True)
        header_view.add_group("Track Info", 1, 3)
        header_view.add_group("Status", 4, 5)
        header_view.add_group("Properties", 6, 14)
        header_view.add_group("Validation", 15, 21)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, CompColumn.COUNT):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        self._search_input.textChanged.connect(self._on_search_changed)
        self._status_combo.currentTextChanged.connect(self._on_status_filter_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._convert_btn.clicked.connect(self._open_conversion_dialog)
        self._source_model.dataChanged.connect(self._on_source_data_changed)

    def _checked_tracks(self) -> list[TrackMetadata]:
        return [track for track in self._source_model.tracks() if track.is_checked]

    def _update_convert_button_state(self) -> None:
        has_checked = bool(self._checked_tracks())
        self._convert_btn.setEnabled(
            has_checked
            and not self._conversion_busy
            and self._data_model is not None
            and self._stack.currentIndex() == 1
        )

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() > CompColumn.CHECK or bottom_right.column() < CompColumn.CHECK:
            return
        if roles is None or not roles or Qt.CheckStateRole in roles:
            self._update_convert_button_state()

    def _open_conversion_dialog(self) -> None:
        if self._data_model is None:
            return

        checked = self._checked_tracks()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Tick one or more files in the table first.")
            return

        if _resolve_ffmpeg_executable() is None:
            QMessageBox.warning(
                self,
                "ffmpeg Not Found",
                "ffmpeg is required for conversion but was not found in PATH or bundled assets.",
            )
            return

        dialog = MusicConversionDialog(checked, self._data_model.root_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        candidates = dialog.candidates()
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Convert",
                "No incompatible files matched the selected conversion mode.",
            )
            return

        self._start_conversion(
            candidates=candidates,
            make_eq_compatible=dialog.make_eq_compatible,
            preserve_tags=dialog.preserve_tags,
            dry_run=dialog.dry_run,
            backup_root=dialog.backup_root,
        )

    def _start_conversion(
        self,
        *,
        candidates: list[dict[str, object]],
        make_eq_compatible: bool,
        preserve_tags: bool,
        dry_run: bool,
        backup_root: Path | None,
    ) -> None:
        target_path = Path(self._data_model.root_path).resolve()
        mode_label = "EQ-compatible" if make_eq_compatible else "FLAC"
        title = "Previewing incompatible music conversion..." if dry_run else (
            f"Converting incompatible music to {mode_label}..."
        )

        progress = QProgressDialog(
            title,
            "Cancel",
            0,
            len(candidates),
            self,
        )
        progress.setWindowTitle("Convert Incompatible Music")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._conversion_progress = progress
        self._conversion_busy = True
        self._update_convert_button_state()

        self._conversion_thread = QThread(self)
        self._conversion_worker = MusicConversionWorker(
            target_path=target_path,
            candidates=candidates,
            make_eq_compatible=make_eq_compatible,
            compression_level=8,
            dry_run=dry_run,
            backup_root=backup_root,
            preserve_tags=preserve_tags,
        )
        self._conversion_worker.moveToThread(self._conversion_thread)

        self._conversion_thread.started.connect(self._conversion_worker.run)
        self._conversion_worker.progress.connect(self._on_conversion_progress)
        self._conversion_worker.finished.connect(self._on_conversion_finished)
        self._conversion_worker.cancelled.connect(self._on_conversion_cancelled)
        self._conversion_worker.failed.connect(self._on_conversion_failed)

        self._conversion_worker.finished.connect(self._conversion_thread.quit)
        self._conversion_worker.cancelled.connect(self._conversion_thread.quit)
        self._conversion_worker.failed.connect(self._conversion_thread.quit)
        self._conversion_thread.finished.connect(self._conversion_worker.deleteLater)
        self._conversion_thread.finished.connect(self._conversion_thread.deleteLater)
        self._conversion_thread.finished.connect(self._clear_conversion_refs)

        progress.canceled.connect(self._cancel_conversion)
        self._conversion_thread.start()

    def _cancel_conversion(self) -> None:
        if self._conversion_worker is not None:
            self._conversion_worker.request_cancel()
        if self._conversion_progress is not None:
            self._conversion_progress.setLabelText("Cancelling conversion...")

    def _disconnect_conversion_progress(self) -> None:
        worker = self._conversion_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_conversion_progress)
            except (RuntimeError, TypeError):
                pass

    def _clear_conversion_refs(self) -> None:
        self._conversion_worker = None
        self._conversion_thread = None

    @Slot(int, int, str)
    def _on_conversion_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._conversion_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            # Dialog was closed while a queued progress update was pending.
            self._conversion_progress = None

    def _finish_conversion_ui(self) -> None:
        self._conversion_busy = False
        self._disconnect_conversion_progress()
        progress = self._conversion_progress
        self._conversion_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_convert_button_state()

    def _refresh_processed_tracks(self, processed_paths: list[dict[str, str]]) -> None:
        if self._data_model is None or not processed_paths:
            return

        root_path = Path(self._data_model.root_path)
        changed = False

        for entry in processed_paths:
            old_path = entry.get("old_path", "")
            new_path = entry.get("new_path", "")
            if not old_path or not new_path:
                continue

            old_key = old_path
            track = self._data_model.tracks.get(old_key)
            if track is None and old_path != new_path:
                track = self._data_model.tracks.get(new_path)

            if track is None:
                continue

            if old_path != new_path:
                self._data_model.tracks.pop(old_key, None)
                track.filepath = new_path
                track.filename = os.path.basename(new_path)
                _, ext = os.path.splitext(track.filename)
                track.extension = ext.lower()
                try:
                    track.size_bytes = os.path.getsize(new_path)
                except OSError:
                    pass
                self._data_model.tracks[new_path] = track
                changed = True

            comp_result = evaluate_music_file(Path(new_path), root_path)
            apply_compatibility_result(track, comp_result)
            track.is_checked = False
            changed = True

        if changed:
            tracks = list(self._data_model.tracks.values())
            self._source_model.update_data(tracks, self._data_model.root_path)
            self._update_stats()

    @Slot(object)
    def _on_conversion_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        converted = int(payload.get("converted") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        backup_root = str(payload.get("backup_root") or "")
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        self._finish_conversion_ui()

        if dry_run:
            summary = f"Dry run complete. Files that would be processed: {planned}. Failed prechecks: {failed}."
            if failed:
                preview = "\n".join(str(item) for item in failures[:15])
                QMessageBox.warning(self, "Dry Run Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
            else:
                QMessageBox.information(self, "Dry Run Completed", summary)
            return

        self._refresh_processed_tracks(processed_paths)

        summary = f"Conversion complete. Converted: {converted} | Failed: {failed}"
        if backup_root:
            summary += f"\nBackups saved to: {backup_root}"

        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(self, "Conversion Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Conversion Completed", summary)

    @Slot(object)
    def _on_conversion_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        converted = int(payload.get("converted") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        self._finish_conversion_ui()

        if not dry_run and processed_paths:
            self._refresh_processed_tracks(processed_paths)

        if dry_run:
            message = f"Dry run cancelled. Planned: {planned} | Failed prechecks: {failed}"
        else:
            message = f"Conversion cancelled. Converted: {converted} | Failed: {failed}"

        if failures:
            preview = "\n".join(str(item) for item in failures[:10])
            QMessageBox.warning(self, "Conversion Cancelled", f"{message}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Conversion Cancelled", message)

    @Slot(str)
    def _on_conversion_failed(self, error: str) -> None:
        self._finish_conversion_ui()
        QMessageBox.warning(self, "Conversion Failed", error)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() == CompColumn.CHECK:
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
                        idx = self._proxy_model.index(r, CompColumn.CHECK)
                        self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
            else:
                selection = self._table.selectionModel()
                if selection.isSelected(index):
                    for selected_index in selection.selectedRows(CompColumn.CHECK):
                        if selected_index.row() != current_row:
                            self._proxy_model.setData(selected_index, new_val, Qt.CheckStateRole)

            self._last_checked_row = current_row
            self._update_convert_button_state()

    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.set_search_query(text)

    def _on_status_filter_changed(self, text: str) -> None:
        self._proxy_model.set_status_filter(text)

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        tracks = list(data_model.tracks.values())
        self._source_model.update_data(tracks, data_model.root_path)

        header = self._table.horizontalHeader()
        font_metrics = header.fontMetrics()

        baselines = {
            CompColumn.CHECK: 30,
            CompColumn.TITLE: 200,
            CompColumn.ARTIST: 150,
            CompColumn.ALBUM: 150,
            CompColumn.STATUS: 100,
            CompColumn.REASON: 300,
            CompColumn.EXTENSION: 80,
            CompColumn.CODEC: 100,
            CompColumn.SAMPLE_RATE: 100,
            CompColumn.BIT_DEPTH: 90,
            CompColumn.CHANNELS: 80,
            CompColumn.DSD: 80,
            CompColumn.BLOCK_SIZE: 100,
            CompColumn.STREAMS: 80,
            CompColumn.EQ: 110,
            CompColumn.CHANNEL_COMPAT: 110,
            CompColumn.WAV_CODEC: 110,
            CompColumn.DSD_BITDEPTH: 130,
            CompColumn.TAG_ENCODING: 130,
            CompColumn.TAG_LENGTH: 110,
            CompColumn.FILENAME: 110,
            CompColumn.METADATA: 110,
        }

        for col in range(CompColumn.COUNT):
            if col in baselines:
                text_width = font_metrics.horizontalAdvance(CompColumn.HEADERS[col].upper()) + 45
                header.resizeSection(col, max(baselines[col], text_width))

        self._table.resizeColumnToContents(CompColumn.FILE)
        self._update_convert_button_state()
        QTimer.singleShot(100, self._update_stats)

    def _update_stats(self) -> None:
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_supported.set_value(self._source_model.count_by_status("SUPPORTED"))
        self._stat_limited.set_value(self._source_model.count_by_status("LIMITED"))
        self._stat_unsupported.set_value(
            self._source_model.count_by_status("UNSUPPORTED")
            + self._source_model.count_by_status("UNKNOWN")
        )
        self._stat_no_eq.set_value(self._source_model.count_by_eq("Not EQ Compatible"))

    def set_processing_state(self, is_processing: bool) -> None:
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        self._update_convert_button_state()
