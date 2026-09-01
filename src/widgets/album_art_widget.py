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
from ..models.album_art_model import (
    AlbumArtTableModel,
    AlbumArtFilterProxyModel,
    ArtColumn,
)
from ..threads.album_art_fix import AlbumArtFixWorker
from ..utils.album_art_planner import apply_album_art_result
from ..utils.album_art_validation import evaluate_album_art
from .album_art_fix_dialog import AlbumArtFixDialog
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


class AlbumArtWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model = None
        self._last_checked_row = None
        self._fix_busy = False
        self._fix_thread: QThread | None = None
        self._fix_worker: AlbumArtFixWorker | None = None
        self._fix_progress: QProgressDialog | None = None
        self._init_models()
        self._init_ui()
        self._connect_signals()

    def _init_models(self) -> None:
        self._source_model = AlbumArtTableModel(self)
        self._proxy_model = AlbumArtFilterProxyModel(self)
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

        title = QLabel("Album Art")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        subtitle = QLabel("Validate and convert embedded artwork")
        subtitle.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")

        self._convert_btn = QPushButton("Convert Selected Artwork")
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
        self._stat_compatible = StatCard("Compatible", Colours.STAT_TITLE, self)
        self._stat_incompatible = StatCard("Incompatible", Colours.STAT_MISSING, self)
        self._stat_missing = StatCard("Missing Artwork", Colours.STAT_ARTIST, self)
        self._stat_oversized = StatCard("Oversized", Colours.STAT_ALBUM, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_compatible)
        stats_layout.addWidget(self._stat_incompatible)
        stats_layout.addWidget(self._stat_missing)
        stats_layout.addWidget(self._stat_oversized)

        h_layout.addLayout(stats_layout)
        layout.addWidget(header)

        self._stack = QStackedWidget()

        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignCenter)

        load_lbl = QLabel("Hardware scan in progress.")
        load_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 20px; font-weight: 700;")
        load_lbl.setAlignment(Qt.AlignCenter)

        sub_lbl = QLabel("Artwork analysis running...")
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
        self._search_input.setPlaceholderText("Search by status, artist, album, file name or artwork format…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        self._status_combo = QComboBox()
        self._status_combo.addItems(
            ["All Statuses", "Compatible", "Incompatible", "Missing Artwork", "Skipped"]
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
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)

        header_view = GroupedHeaderView(self._table)
        self._table.setHorizontalHeader(header_view)
        header_view.setStretchLastSection(True)
        header_view.add_group("Track Info", 1, 3)
        header_view.add_group("Status", 4, 5)
        header_view.add_group("Artwork", 6, 10)
        header_view.add_group("Validation", 11, 14)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, ArtColumn.FILE):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        self._search_input.textChanged.connect(self._on_search_changed)
        self._status_combo.currentTextChanged.connect(self._on_status_filter_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._convert_btn.clicked.connect(self._open_fix_dialog)
        self._source_model.dataChanged.connect(self._on_source_data_changed)

    def _checked_tracks(self) -> list[TrackMetadata]:
        return [track for track in self._source_model.tracks() if track.art_is_checked]

    def _update_convert_button_state(self) -> None:
        has_checked = bool(self._checked_tracks())
        self._convert_btn.setEnabled(
            has_checked
            and not self._fix_busy
            and self._data_model is not None
            and self._stack.currentIndex() == 1
        )

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() > ArtColumn.CHECK or bottom_right.column() < ArtColumn.CHECK:
            return
        if roles is None or not roles or Qt.CheckStateRole in roles:
            self._update_convert_button_state()

    def _open_fix_dialog(self) -> None:
        if self._data_model is None:
            return

        checked = self._checked_tracks()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Tick one or more files in the table first.")
            return

        dialog = AlbumArtFixDialog(checked, self._data_model.root_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        candidates = dialog.candidates()
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Convert",
                "None of the selected files have artwork that needs converting.",
            )
            return

        self._start_fix(
            candidates=candidates,
            quality=dialog.quality,
            dry_run=dialog.dry_run,
            backup_root=dialog.backup_root,
        )

    def _start_fix(
        self,
        *,
        candidates: list[dict[str, object]],
        quality: int,
        dry_run: bool,
        backup_root: Path | None,
    ) -> None:
        target_path = Path(self._data_model.root_path).resolve()
        title = (
            "Previewing artwork conversion..." if dry_run
            else "Converting artwork to baseline JPEG..."
        )

        progress = QProgressDialog(
            title,
            "Cancel",
            0,
            len(candidates),
            self,
        )
        progress.setWindowTitle("Convert Artwork")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._fix_progress = progress
        self._fix_busy = True
        self._update_convert_button_state()

        self._fix_thread = QThread(self)
        self._fix_worker = AlbumArtFixWorker(
            target_path=target_path,
            candidates=candidates,
            quality=quality,
            dry_run=dry_run,
            backup_root=backup_root,
        )
        self._fix_worker.moveToThread(self._fix_thread)

        self._fix_thread.started.connect(self._fix_worker.run)
        self._fix_worker.progress.connect(self._on_fix_progress)
        self._fix_worker.finished.connect(self._on_fix_finished)
        self._fix_worker.cancelled.connect(self._on_fix_cancelled)
        self._fix_worker.failed.connect(self._on_fix_failed)

        self._fix_worker.finished.connect(self._fix_thread.quit)
        self._fix_worker.cancelled.connect(self._fix_thread.quit)
        self._fix_worker.failed.connect(self._fix_thread.quit)
        self._fix_thread.finished.connect(self._fix_worker.deleteLater)
        self._fix_thread.finished.connect(self._fix_thread.deleteLater)
        self._fix_thread.finished.connect(self._clear_fix_refs)

        progress.canceled.connect(self._cancel_fix)
        self._fix_thread.start()

    def _cancel_fix(self) -> None:
        if self._fix_worker is not None:
            self._fix_worker.request_cancel()
        if self._fix_progress is not None:
            self._fix_progress.setLabelText("Cancelling conversion...")

    def _disconnect_fix_progress(self) -> None:
        worker = self._fix_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_fix_progress)
            except (RuntimeError, TypeError):
                pass

    def _clear_fix_refs(self) -> None:
        self._fix_worker = None
        self._fix_thread = None

    @Slot(int, int, str)
    def _on_fix_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._fix_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            # Dialog was closed while a queued progress update was pending.
            self._fix_progress = None

    def _finish_fix_ui(self) -> None:
        self._fix_busy = False
        self._disconnect_fix_progress()
        progress = self._fix_progress
        self._fix_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_convert_button_state()

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

            apply_album_art_result(track, evaluate_album_art(Path(filepath)))
            track.art_is_checked = False
            changed = True

        if changed:
            tracks = list(self._data_model.tracks.values())
            self._source_model.update_data(tracks, self._data_model.root_path)
            self._update_stats()

    @Slot(object)
    def _on_fix_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        converted = int(payload.get("converted") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        backup_root = str(payload.get("backup_root") or "")
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        self._finish_fix_ui()

        if dry_run:
            summary = f"Dry run complete. Files that would be processed: {planned}. Failed prechecks: {failed}."
            if failed:
                preview = "\n".join(str(item) for item in failures[:15])
                QMessageBox.warning(self, "Dry Run Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
            else:
                QMessageBox.information(self, "Dry Run Completed", summary)
            return

        self._refresh_processed_tracks(processed_paths)

        summary = f"Artwork conversion complete. Converted: {converted} | Failed: {failed}"
        if backup_root:
            summary += f"\nBackups saved to: {backup_root}"

        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(self, "Conversion Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Conversion Completed", summary)

    @Slot(object)
    def _on_fix_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        converted = int(payload.get("converted") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        self._finish_fix_ui()

        if not dry_run and processed_paths:
            self._refresh_processed_tracks(processed_paths)

        if dry_run:
            message = f"Dry run cancelled. Planned: {planned} | Failed prechecks: {failed}"
        else:
            message = f"Artwork conversion cancelled. Converted: {converted} | Failed: {failed}"

        if failures:
            preview = "\n".join(str(item) for item in failures[:10])
            QMessageBox.warning(self, "Conversion Cancelled", f"{message}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Conversion Cancelled", message)

    @Slot(str)
    def _on_fix_failed(self, error: str) -> None:
        self._finish_fix_ui()
        QMessageBox.warning(self, "Conversion Failed", error)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() == ArtColumn.CHECK:
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
                        idx = self._proxy_model.index(r, ArtColumn.CHECK)
                        self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
            else:
                selection = self._table.selectionModel()
                if selection.isSelected(index):
                    for selected_index in selection.selectedRows(ArtColumn.CHECK):
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

        # Pre-select the rows the convert flow can act on.
        for track in tracks:
            track.art_is_checked = track.art_status == "INCOMPATIBLE"

        self._source_model.update_data(tracks, data_model.root_path)

        header = self._table.horizontalHeader()
        font_metrics = header.fontMetrics()

        baselines = {
            ArtColumn.CHECK: 30,
            ArtColumn.TITLE: 200,
            ArtColumn.ARTIST: 150,
            ArtColumn.ALBUM: 150,
            ArtColumn.STATUS: 120,
            ArtColumn.REASON: 300,
            ArtColumn.FORMAT: 90,
            ArtColumn.SCAN_TYPE: 130,
            ArtColumn.RESOLUTION: 120,
            ArtColumn.DATA_SIZE: 100,
            ArtColumn.SOURCE: 120,
            ArtColumn.FORMAT_CHECK: 130,
            ArtColumn.SCAN_CHECK: 130,
            ArtColumn.RESOLUTION_CHECK: 150,
            ArtColumn.METADATA: 150,
        }

        for col in range(ArtColumn.COUNT):
            if col in baselines:
                text_width = font_metrics.horizontalAdvance(ArtColumn.HEADERS[col].upper()) + 45
                header.resizeSection(col, max(baselines[col], text_width))

        self._table.resizeColumnToContents(ArtColumn.FILE)
        self._update_convert_button_state()
        QTimer.singleShot(100, self._update_stats)

    def _update_stats(self) -> None:
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_compatible.set_value(self._source_model.count_by_status("COMPATIBLE"))
        self._stat_incompatible.set_value(
            self._source_model.count_by_status("INCOMPATIBLE")
            + self._source_model.count_by_status("UNKNOWN")
        )
        self._stat_missing.set_value(self._source_model.count_by_status("MISSING"))
        self._stat_oversized.set_value(self._source_model.count_oversized())

    def set_processing_state(self, is_processing: bool) -> None:
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        self._update_convert_button_state()
