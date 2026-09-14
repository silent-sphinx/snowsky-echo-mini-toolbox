from PySide6.QtCore import Qt, QThread, QTimer, QModelIndex, Slot
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableView,
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
from ..models.metadata_table_model import (
    MetadataTableModel,
    MetadataFilterProxyModel,
    MetaColumn,
)
from ..threads.bulk_metadata import BulkMetadataWorker
from .bulk_metadata_dialog import BulkMetadataDialog
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

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            # Global QLineEdit padding (7px) is taller than the 28px table row
            # and clips the glyphs. Keep the editor flush with the cell.
            editor.setFrame(False)
            editor.setContentsMargins(0, 0, 0, 0)
            editor.setTextMargins(6, 0, 6, 0)
            editor.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {Colours.BG_ELEVATED};
                    color: {Colours.TEXT_PRIMARY};
                    border: 1px solid {Colours.ACCENT};
                    padding: 0px 6px;
                    font-size: 12px;
                    min-height: 0px;
                }}
            """)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class MetadataManager(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model = None
        self._last_checked_row = None
        self._bulk_busy = False
        self._bulk_thread: QThread | None = None
        self._bulk_worker: BulkMetadataWorker | None = None
        self._bulk_progress: QProgressDialog | None = None
        self._bulk_tags: dict[str, str | None] = {}
        self._init_models()
        self._init_ui()
        self._connect_signals()

    def _init_models(self) -> None:
        self._source_model = MetadataTableModel(self)
        self._proxy_model = MetadataFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        self._edit_btn = QPushButton("Bulk Edit Selected")
        self._edit_btn.setObjectName("accentButton")
        self._edit_btn.setEnabled(False)
        self._edit_btn.setMinimumHeight(34)

        layout.addWidget(page_header(
            "Metadata Browser",
            "Browse and edit audio tags, including tracks missing Title, Artist, or Album",
            [self._edit_btn],
        ))

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page(
            "Hardware scan in progress.",
            "Metadata analysis running...",
        ))

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        toolbar_panel, toolbar = filter_toolbar()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            "Search by title, artist, album, genre or file path…"
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        self._status_combo = QComboBox()
        self._status_combo.addItems([
            "All Statuses",
            "Complete",
            "Missing Metadata",
            "Missing Title",
            "Missing Artist",
            "Missing Album",
        ])
        self._status_combo.setMinimumHeight(34)
        self._status_combo.setMinimumWidth(170)
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
        self._stat_missing = StatCard("Missing Metadata", Colours.STATUS_MISSING, self)
        self._stat_title = StatCard("Missing Title", Colours.STAT_TITLE, self)
        self._stat_artist = StatCard("Missing Artist", Colours.STAT_ARTIST, self)
        self._stat_album = StatCard("Missing Album", Colours.STAT_ALBUM, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_missing)
        stats_layout.addWidget(self._stat_title)
        stats_layout.addWidget(self._stat_artist)
        stats_layout.addWidget(self._stat_album)
        data_layout.addLayout(stats_layout)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
        self._table.setEditTriggers(QTableView.DoubleClicked | QTableView.EditKeyPressed)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)

        header_view = GroupedHeaderView(self._table)
        self._table.setHorizontalHeader(header_view)
        header_view.setStretchLastSection(True)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(-1, Qt.AscendingOrder)
        header_view.add_group("Core Tags", 1, 4)
        header_view.add_group("Release", 5, 7)
        header_view.add_group("Status", 8, 9)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, MetaColumn.COUNT):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        self._search_input.textChanged.connect(self._on_search_changed)
        self._status_combo.currentTextChanged.connect(self._on_status_filter_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._edit_btn.clicked.connect(self._open_bulk_edit_dialog)
        self._source_model.dataChanged.connect(self._on_source_data_changed)
        self._source_model.save_failed.connect(self._on_save_failed)

    def _checked_tracks(self) -> list[TrackMetadata]:
        return [track for track in self._source_model.tracks() if track.meta_is_checked]

    def _is_ready(self) -> bool:
        return (
            self._data_model is not None
            and self._stack.currentIndex() == 1
            and not self._bulk_busy
        )

    def _update_action_button_state(self) -> None:
        self._edit_btn.setEnabled(bool(self._checked_tracks()) and self._is_ready())

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() <= MetaColumn.CHECK <= bottom_right.column():
            if roles is None or not roles or Qt.CheckStateRole in roles:
                self._update_action_button_state()
        if bottom_right.column() > MetaColumn.CHECK:
            self._update_stats()

    @Slot(str, str)
    def _on_save_failed(self, filename: str, message: str) -> None:
        QMessageBox.critical(
            self,
            "Save Failed",
            f"Failed to save metadata for {filename}:\n{message}",
        )

    def _open_bulk_edit_dialog(self) -> None:
        if self._data_model is None or self._bulk_thread is not None:
            return

        checked = self._checked_tracks()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Tick one or more files in the table first.")
            return

        dialog = BulkMetadataDialog(checked, self)
        if not dialog.eligible_tracks():
            QMessageBox.information(
                self,
                "Bulk Edit Metadata",
                "None of the selected files can store audio metadata.",
            )
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tags = dialog.tags_to_apply()
        if not tags:
            return

        self._start_bulk_edit(dialog.eligible_tracks(), tags)

    def _start_bulk_edit(
        self, tracks: list[TrackMetadata], tags: dict[str, str | None]
    ) -> None:
        self._bulk_tags = tags

        progress = QProgressDialog(
            f"Updating {len(tracks)} songs...",
            "Cancel",
            0,
            len(tracks),
            self,
        )
        progress.setWindowTitle("Bulk Edit Metadata")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        self._bulk_progress = progress
        self._bulk_busy = True
        self._update_action_button_state()

        self._bulk_thread = QThread(self)
        self._bulk_worker = BulkMetadataWorker(tracks, tags)
        self._bulk_worker.moveToThread(self._bulk_thread)

        self._bulk_thread.started.connect(self._bulk_worker.run)
        self._bulk_worker.progress.connect(self._on_bulk_progress)
        self._bulk_worker.finished.connect(self._on_bulk_finished)
        self._bulk_worker.cancelled.connect(self._on_bulk_cancelled)
        self._bulk_worker.failed.connect(self._on_bulk_failed)

        self._bulk_worker.finished.connect(self._bulk_thread.quit)
        self._bulk_worker.cancelled.connect(self._bulk_thread.quit)
        self._bulk_worker.failed.connect(self._bulk_thread.quit)
        self._bulk_thread.finished.connect(self._bulk_worker.deleteLater)
        self._bulk_thread.finished.connect(self._bulk_thread.deleteLater)
        self._bulk_thread.finished.connect(self._clear_bulk_refs)

        progress.canceled.connect(self._cancel_bulk_edit)
        self._bulk_thread.start()

    def _cancel_bulk_edit(self) -> None:
        if self._bulk_worker is not None:
            self._bulk_worker.request_cancel()
        if self._bulk_progress is not None:
            self._bulk_progress.setLabelText("Finishing current file...")

    def _clear_bulk_refs(self) -> None:
        self._bulk_worker = None
        self._bulk_thread = None

    def cancel_running_job(self) -> None:
        self._cancel_bulk_edit()
        if self._bulk_thread is not None and self._bulk_thread.isRunning():
            self._bulk_thread.wait(8000)

    @Slot(int, int, str)
    def _on_bulk_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._bulk_progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._bulk_progress = None

    def _close_bulk_progress(self) -> None:
        worker = self._bulk_worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_bulk_progress)
            except (RuntimeError, TypeError):
                pass

        progress = self._bulk_progress
        self._bulk_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()

        self._bulk_busy = False
        self._update_action_button_state()

    def _apply_bulk_results(self, payload: dict) -> tuple[int, list[str]]:
        updated_paths = payload.get("updated_paths", [])
        if self._data_model:
            for filepath in updated_paths:
                self._data_model.update_metadata(filepath, self._bulk_tags)
        self._source_model.notify_paths_changed(updated_paths)
        self._update_stats()
        return len(updated_paths), payload.get("errors", [])

    @Slot(object)
    def _on_bulk_finished(self, payload: dict) -> None:
        self._close_bulk_progress()
        updated, errors = self._apply_bulk_results(payload)
        total = payload.get("total", updated)

        if errors:
            preview = "\n".join(errors[:8])
            extra = f"\n…and {len(errors) - 8} more." if len(errors) > 8 else ""
            QMessageBox.warning(
                self,
                "Bulk edit finished with errors",
                f"Updated {updated} of {total} songs.\n\n{preview}{extra}",
            )
        else:
            QMessageBox.information(
                self,
                "Bulk edit complete",
                f"Updated metadata on {updated} song{'s' if updated != 1 else ''}.",
            )

    @Slot(object)
    def _on_bulk_cancelled(self, payload: dict) -> None:
        self._close_bulk_progress()
        updated, _ = self._apply_bulk_results(payload)
        QMessageBox.information(
            self,
            "Bulk edit cancelled",
            f"Cancelled after updating {updated} of {payload.get('total', 0)} songs.",
        )

    @Slot(str)
    def _on_bulk_failed(self, message: str) -> None:
        self._close_bulk_progress()
        QMessageBox.critical(self, "Bulk edit failed", message)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() == MetaColumn.CHECK:
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
                        idx = self._proxy_model.index(r, MetaColumn.CHECK)
                        self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
            else:
                selection = self._table.selectionModel()
                if selection.isSelected(index):
                    for selected_index in selection.selectedRows(MetaColumn.CHECK):
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
            track.meta_is_checked = False

        self._source_model.update_data(tracks, data_model.root_path, data_model)

        header = self._table.horizontalHeader()
        font_metrics = header.fontMetrics()

        baselines = {
            MetaColumn.CHECK: 30,
            MetaColumn.TITLE: 200,
            MetaColumn.ARTIST: 150,
            MetaColumn.ALBUM: 150,
            MetaColumn.ALBUM_ARTIST: 150,
            MetaColumn.TRACK: 70,
            MetaColumn.GENRE: 110,
            MetaColumn.YEAR: 70,
            MetaColumn.STATUS: 110,
            MetaColumn.REASON: 240,
        }

        for col in range(MetaColumn.COUNT):
            if col in baselines:
                text_width = font_metrics.horizontalAdvance(MetaColumn.HEADERS[col].upper()) + 45
                header.resizeSection(col, max(baselines[col], text_width))

        self._table.resizeColumnToContents(MetaColumn.FILE)
        self._update_action_button_state()
        QTimer.singleShot(100, self._update_stats)

    def _update_stats(self) -> None:
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_missing.set_value(self._source_model.count_by_status("MISSING"))
        self._stat_title.set_value(self._source_model.missing_title_count())
        self._stat_artist.set_value(self._source_model.missing_artist_count())
        self._stat_album.set_value(self._source_model.missing_album_count())

    def set_processing_state(self, is_processing: bool) -> None:
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        self._update_action_button_state()
