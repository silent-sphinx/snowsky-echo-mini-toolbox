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
from ..threads.album_art_download import AlbumArtApplyWorker, AlbumArtLookupWorker
from ..utils.album_art_download import AlbumArtLookupClient
from ..utils.album_art_download_planner import build_album_groups
from ..utils.album_art_planner import apply_album_art_result
from ..utils.album_art_validation import evaluate_album_art
from .album_art_download_dialog import AlbumArtDownloadDialog
from .album_art_fix_dialog import AlbumArtFixDialog
from .page_chrome import filter_toolbar, loading_page, page_header
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
        self._download_busy = False
        self._download_thread: QThread | None = None
        self._download_worker = None
        self._download_progress: QProgressDialog | None = None
        self._lookup_client: AlbumArtLookupClient | None = None
        self._pending_skipped = []
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

        self._convert_btn = QPushButton("Convert Selected Artwork")
        self._convert_btn.setObjectName("accentButton")
        self._convert_btn.setEnabled(False)
        self._convert_btn.setMinimumHeight(34)

        self._download_btn = QPushButton("Download Missing Artwork")
        self._download_btn.setEnabled(False)
        self._download_btn.setVisible(False)
        self._download_btn.setMinimumHeight(34)
        self._download_btn.setToolTip(
            "Look up covers on MusicBrainz for the selected tracks that have no artwork."
        )

        layout.addWidget(page_header(
            "Album Art Manager",
            "Validate and convert embedded artwork",
            [self._convert_btn, self._download_btn],
        ))

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page(
            "Hardware scan in progress.",
            "Artwork analysis running...",
        ))

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        toolbar_panel, toolbar = filter_toolbar()

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

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self._stat_total = StatCard("Total Scanned", Colours.STAT_TOTAL, self)
        self._stat_compatible = StatCard("Compatible", Colours.STATUS_COMPATIBLE, self)
        self._stat_incompatible = StatCard("Incompatible", Colours.STATUS_INCOMPATIBLE, self)
        self._stat_missing = StatCard("Missing Artwork", Colours.STATUS_MISSING, self)
        self._stat_oversized = StatCard("Oversized", Colours.STATUS_LIMITED, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_compatible)
        stats_layout.addWidget(self._stat_incompatible)
        stats_layout.addWidget(self._stat_missing)
        stats_layout.addWidget(self._stat_oversized)
        data_layout.addLayout(stats_layout)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)

        header_view = GroupedHeaderView(self._table)
        self._table.setHorizontalHeader(header_view)
        header_view.setStretchLastSection(True)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(-1, Qt.AscendingOrder)
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
        self._download_btn.clicked.connect(self._start_lookup)
        self._source_model.dataChanged.connect(self._on_source_data_changed)

    def _checked_tracks(self) -> list[TrackMetadata]:
        return [track for track in self._source_model.tracks() if track.art_is_checked]

    def _update_convert_button_state(self) -> None:
        checked = self._checked_tracks()
        ready = (
            self._data_model is not None
            and self._stack.currentIndex() == 1
            and not self._fix_busy
            and not self._download_busy
        )
        has_checked = bool(checked)
        has_missing_checked = any(track.art_status == "MISSING" for track in checked)

        self._convert_btn.setEnabled(has_checked and ready)
        self._download_btn.setVisible(has_checked)
        self._download_btn.setEnabled(ready and has_missing_checked)

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

    # ── download flow ───────────────────────────────────────────

    def _start_lookup(self) -> None:
        if self._data_model is None or self._download_busy:
            return

        checked = self._checked_tracks()
        missing = [track for track in checked if track.art_status == "MISSING"]
        if not missing:
            QMessageBox.information(
                self,
                "Nothing To Download",
                "Tick one or more rows with missing artwork first.",
            )
            return

        groups, skipped = build_album_groups(missing, self._data_model.root_path)
        if not groups:
            detail = ""
            if skipped:
                reasons = sorted({item.reason for item in skipped})
                detail = "\n\n" + "\n".join(f"- {reason}" for reason in reasons)
            QMessageBox.information(
                self,
                "Nothing To Download",
                "None of the selected tracks can be looked up." + detail,
            )
            return

        self._pending_skipped = skipped
        self._lookup_client = AlbumArtLookupClient()

        progress = QProgressDialog(
            f"Searching {len(groups)} album(s) on MusicBrainz...",
            "Cancel",
            0,
            len(groups),
            self,
        )
        progress.setWindowTitle("Download Missing Artwork")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._download_progress = progress
        self._download_busy = True
        self._update_convert_button_state()

        thread = QThread(self)
        worker = AlbumArtLookupWorker(groups, client=self._lookup_client)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_download_progress)
        worker.finished.connect(self._on_lookup_finished)
        worker.cancelled.connect(self._on_lookup_cancelled)
        worker.failed.connect(self._on_download_failed)

        worker.finished.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_download_refs)

        progress.canceled.connect(self._cancel_download)
        self._download_thread = thread
        self._download_worker = worker
        thread.start()

    @Slot(object)
    def _on_lookup_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        groups = payload.get("groups") or []
        found = int(payload.get("found") or 0)

        self._finish_download_ui()

        if not groups:
            return

        if not found:
            QMessageBox.information(
                self,
                "No Covers Found",
                "No cover art was found for the selected albums. "
                "You can edit the search terms and retry from the review window.",
            )

        self._open_download_dialog(groups)

    @Slot(object)
    def _on_lookup_cancelled(self, payload_obj) -> None:
        self._finish_download_ui()
        QMessageBox.information(self, "Search Cancelled", "Album cover search was cancelled.")

    def _open_download_dialog(self, groups) -> None:
        if self._data_model is None or self._lookup_client is None:
            return

        # The lookup worker is being torn down; stop the client consulting its
        # cancellation flag while the dialog reuses the client for previews.
        self._lookup_client.set_cancel_check(None)

        dialog = AlbumArtDownloadDialog(
            groups,
            self._pending_skipped,
            self._data_model.root_path,
            self._lookup_client,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        candidates = dialog.candidates()
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Apply",
                "No albums with a selected cover were confirmed.",
            )
            return

        self._start_download_apply(
            candidates=candidates,
            quality=dialog.quality,
            dry_run=dialog.dry_run,
            backup_root=dialog.backup_root,
        )

    def _start_download_apply(
        self,
        *,
        candidates: list[dict[str, object]],
        quality: int,
        dry_run: bool,
        backup_root: Path | None,
    ) -> None:
        target_path = Path(self._data_model.root_path).resolve()
        total_files = sum(len(c.get("filepaths") or []) for c in candidates)
        title = (
            f"Previewing artwork for {len(candidates)} album(s)..." if dry_run
            else f"Applying artwork to {total_files} file(s) across {len(candidates)} album(s)..."
        )

        progress = QProgressDialog(title, "Cancel", 0, len(candidates), self)
        progress.setWindowTitle("Download Missing Artwork")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._download_progress = progress
        self._download_busy = True
        self._update_convert_button_state()

        thread = QThread(self)
        worker = AlbumArtApplyWorker(
            target_path=target_path,
            candidates=candidates,
            quality=quality,
            dry_run=dry_run,
            backup_root=backup_root,
            client=self._lookup_client,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_download_progress)
        worker.finished.connect(self._on_download_finished)
        worker.cancelled.connect(self._on_download_cancelled)
        worker.failed.connect(self._on_download_failed)

        worker.finished.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_download_refs)

        progress.canceled.connect(self._cancel_download)
        self._download_thread = thread
        self._download_worker = worker
        thread.start()

    def _cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.request_cancel()
        if self._download_progress is not None:
            self._download_progress.setLabelText("Cancelling...")

    def _clear_download_refs(self) -> None:
        self._download_worker = None
        self._download_thread = None

    @Slot(int, int, str)
    def _on_download_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._download_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._download_progress = None

    def _finish_download_ui(self) -> None:
        self._download_busy = False
        worker = self._download_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_download_progress)
            except (RuntimeError, TypeError):
                pass
        progress = self._download_progress
        self._download_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._update_convert_button_state()

    @Slot(object)
    def _on_download_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        written = int(payload.get("written") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        albums = int(payload.get("total_albums") or 0)
        dry_run = bool(payload.get("dry_run"))
        backup_root = str(payload.get("backup_root") or "")
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        self._finish_download_ui()

        if dry_run:
            summary = (
                f"Dry run complete. Files that would receive artwork: {planned} "
                f"across {albums} album(s)."
            )
            if failed:
                preview = "\n".join(str(item) for item in failures[:15])
                QMessageBox.warning(self, "Dry Run Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
            else:
                QMessageBox.information(self, "Dry Run Completed", summary)
            return

        self._refresh_processed_tracks(processed_paths)

        summary = (
            f"Artwork download complete. Files updated: {written} | Failed: {failed} "
            f"(across {albums} album(s))"
        )
        if backup_root:
            summary += f"\nBackups saved to: {backup_root}"

        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(self, "Download Completed With Errors", f"{summary}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Download Completed", summary)

    @Slot(object)
    def _on_download_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        written = int(payload.get("written") or 0)
        failed = int(payload.get("failed") or 0)
        failures = payload.get("failures") or []
        processed_paths = payload.get("processed_paths") or []

        self._finish_download_ui()
        self._refresh_processed_tracks(processed_paths)

        message = f"Artwork download cancelled. Files updated: {written} | Failed: {failed}"
        if failures:
            preview = "\n".join(str(item) for item in failures[:10])
            QMessageBox.warning(self, "Download Cancelled", f"{message}\n\nFailures:\n{preview}")
        else:
            QMessageBox.information(self, "Download Cancelled", message)

    @Slot(str)
    def _on_download_failed(self, error: str) -> None:
        self._finish_download_ui()
        QMessageBox.warning(self, "Download Failed", error)

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
