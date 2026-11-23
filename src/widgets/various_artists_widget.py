from PySide6.QtCore import Qt, QModelIndex, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.drive_data import DriveDataModel
from ..models.various_artists_model import (
    VariousArtistsColumn,
    VariousArtistsFilterProxyModel,
    VariousArtistsTableModel,
)
from ..theme import Colours
from ..threads.various_artists import VariousArtistsWorker
from ..utils.file_rename import is_rename_track
from ..utils.various_artists import DEFAULT_TARGET, FIELD_CHOICES, FIELDS_BOTH
from .file_rename_widget import _COMBO_STYLE, HighlightDelegate
from .grouped_header_view import GroupedHeaderView
from .page_chrome import (
    PATH_COLUMN_WIDTH,
    bind_search_field,
    filter_toolbar,
    flow_steps,
    freeze_view,
    loading_page,
    page_header,
)
from .stat_card import StatCard
from .table_select import SelectAllTableView, add_select_all_rows_action, wire_select_all_rows


class VariousArtistsWidget(QWidget):
    library_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model: DriveDataModel | None = None
        self._last_checked_row = None
        self._busy = False
        self._applied_target = DEFAULT_TARGET
        self._applied_fields = FIELDS_BOTH
        self._pending_tags: dict[str, dict[str, str]] = {}
        self._thread: QThread | None = None
        self._worker: VariousArtistsWorker | None = None
        self._progress: QProgressDialog | None = None
        self._source_model = VariousArtistsTableModel(self)
        self._proxy_model = VariousArtistsFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)
        self._init_ui()
        self._connect_signals()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        self._apply_btn = QPushButton("Normalise Selected Files")
        self._apply_btn.setObjectName("accentButton")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setMinimumHeight(34)

        layout.addWidget(page_header(
            "Various Artists",
            "Find tracks tagged Various, Various Artists, Multiple Artists or VA, "
            "and rewrite them to one consistent value.",
            [self._apply_btn],
        ))

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page(
            "Hardware scan in progress.",
            "Looking for Various Artists tags...",
        ))

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        data_layout.addWidget(flow_steps([
            ("1", "Choose the value", "Type the artist name every match should be changed to."),
            ("2", "Review the matches", "Highlighted cells are the tags that will change. Untick any you want to leave."),
            ("3", "Normalise", "Tags are written to the files. Back up first, as this overwrites them."),
        ]))

        toolbar_panel, toolbar = filter_toolbar()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search by title, album, artist or file path…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        value_lbl = QLabel("CHANGE TO")
        value_lbl.setObjectName("toolbarLabel")
        toolbar.addWidget(value_lbl)

        self._target_input = QLineEdit(DEFAULT_TARGET)
        self._target_input.setMinimumHeight(34)
        self._target_input.setMinimumWidth(200)
        self._target_input.setToolTip("The value written to every matching tag. Press Enter to refresh the preview.")
        toolbar.addWidget(self._target_input)

        fields_lbl = QLabel("TAGS")
        fields_lbl.setObjectName("toolbarLabel")
        toolbar.addWidget(fields_lbl)

        self._fields_combo = QComboBox()
        for fields, label in FIELD_CHOICES.items():
            self._fields_combo.addItem(label, fields)
        self._fields_combo.setMinimumHeight(34)
        self._fields_combo.setMinimumWidth(200)
        self._fields_combo.setStyleSheet(_COMBO_STYLE)
        self._fields_combo.setToolTip("Which tags to look at and rewrite.")
        toolbar.addWidget(self._fields_combo)

        data_layout.addWidget(toolbar_panel)

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)
        self._stat_total = StatCard("Audio Scanned", Colours.STAT_TOTAL, self)
        self._stat_matches = StatCard("Tracks To Normalise", Colours.STATUS_LIMITED, self)
        self._stat_selected = StatCard("Selected", Colours.STATUS_SUPPORTED, self)
        for card in (self._stat_total, self._stat_matches, self._stat_selected):
            stats_layout.addWidget(card)
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
        header_view.add_group("Track Info", 1, 2)
        header_view.add_group("Artist Tags", 3, 4)

        self._delegate = HighlightDelegate(self._table)
        for col in range(1, VariousArtistsColumn.COUNT):
            self._table.setItemDelegateForColumn(col, self._delegate)

        data_layout.addWidget(self._table, 1)
        wire_select_all_rows(self._table, toolbar, owns_menu=False)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        bind_search_field(self._search_input, self._proxy_model.set_search_query)
        self._target_input.editingFinished.connect(self._on_options_changed)
        self._fields_combo.currentIndexChanged.connect(self._on_options_changed)
        self._table.clicked.connect(self._on_table_clicked)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._apply_btn.clicked.connect(self._normalise_selected)
        self._source_model.dataChanged.connect(self._on_source_data_changed)
        self._update_state()

    def _target(self) -> str:
        return self._target_input.text().strip()

    def _fields(self) -> str:
        return self._fields_combo.currentData() or FIELDS_BOTH

    def _is_ready(self) -> bool:
        return self._data_model is not None and self._stack.currentIndex() == 1 and not self._busy

    def _update_state(self) -> None:
        checked = len(self._source_model.checked_changes())
        self._apply_btn.setEnabled(self._is_ready() and checked > 0 and bool(self._target()))
        if checked:
            noun = "File" if checked == 1 else "Files"
            self._apply_btn.setText(f"Normalise {checked} {noun}")
        else:
            self._apply_btn.setText("Normalise Selected Files")
        self._stat_selected.set_value(checked)
        self._update_guidance(checked)

    def _update_guidance(self, checked: int) -> None:
        if self._data_model is None or self._stack.currentIndex() != 1:
            self._guidance_lbl.setText("Scan a drive or folder first. This page then lists tracks with a Various Artists tag.")
            return
        if not self._target():
            self._guidance_lbl.setText("Enter the value to change matching tags to.")
        elif not self._source_model.total_rows():
            self._guidance_lbl.setText(f"No Various Artists variants found that differ from “{self._target()}”.")
        elif checked:
            self._guidance_lbl.setText(f"Ready to change {checked} track(s) to “{self._target()}”.")
        else:
            self._guidance_lbl.setText("Tick tracks to normalise.")

    def _on_source_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=None) -> None:
        if top_left.column() <= VariousArtistsColumn.CHECK <= bottom_right.column():
            self._update_state()

    def _on_options_changed(self, _index: int = 0) -> None:
        if self._data_model is None or self._busy:
            return
        if self._target() == self._applied_target and self._fields() == self._applied_fields:
            return
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        if self._data_model is None:
            return
        self._applied_target = self._target()
        self._applied_fields = self._fields()
        tracks = list(self._data_model.tracks.values())
        with freeze_view(self._table):
            self._source_model.update_data(
                tracks,
                self._data_model.root_path,
                self._applied_fields,
                self._applied_target,
            )
        self._stat_total.set_value(sum(1 for track in tracks if is_rename_track(track)))
        self._stat_matches.set_value(self._source_model.total_rows())
        self._update_state()

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        index = self._table.indexAt(pos)
        text = self._proxy_model.data(index, Qt.DisplayRole) if index.isValid() else None
        copy_action = None
        if text:
            copy_action = menu.addAction("Copy")
            menu.addSeparator()
        add_select_all_rows_action(menu, self._table)
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if copy_action is not None and action == copy_action:
            QApplication.clipboard().setText(str(text))

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() != VariousArtistsColumn.CHECK:
            return
        is_shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        state = self._proxy_model.data(index, Qt.CheckStateRole)
        new_val = Qt.Checked if state in (Qt.Checked, Qt.CheckState.Checked, 2) else Qt.Unchecked
        current_row = index.row()

        if is_shift and self._last_checked_row is not None:
            start, end = sorted((self._last_checked_row, current_row))
            for row in range(start, end + 1):
                if row != current_row:
                    idx = self._proxy_model.index(row, VariousArtistsColumn.CHECK)
                    self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
        else:
            selection = self._table.selectionModel()
            if selection.isSelected(index):
                for selected in selection.selectedRows(VariousArtistsColumn.CHECK):
                    if selected.row() != current_row:
                        self._proxy_model.setData(selected, new_val, Qt.CheckStateRole)

        self._last_checked_row = current_row
        self._update_state()

    def _normalise_selected(self) -> None:
        if self._data_model is None:
            return
        # The preview was built for the last committed value, so make sure it is the typed one.
        if self._target() != self._applied_target or self._fields() != self._applied_fields:
            self._refresh_rows()
            QMessageBox.information(
                self,
                "Preview Refreshed",
                "The value or tags changed, so the preview was rebuilt. Review it, then apply.",
            )
            return

        checked = self._source_model.checked_changes()
        if not checked:
            QMessageBox.information(self, "Nothing Selected", "Tick one or more tracks in the table first.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Normalise",
            (
                f"Change the artist tags on {len(checked)} file(s) to “{self._applied_target}”?\n\n"
                "This overwrites the tags in the files. Back up first if you are unsure."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self._pending_tags = {track.filepath: changes for track, changes in checked}
        self._start_worker([(track.filepath, track.filename, changes) for track, changes in checked])

    def _start_worker(self, jobs: list[tuple[str, str, dict[str, str]]]) -> None:
        progress = QProgressDialog("Updating artist tags...", "Cancel", 0, len(jobs), self)
        progress.setWindowTitle("Various Artists")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        self._progress = progress
        self._busy = True
        self._update_state()

        self._thread = QThread(self)
        self._worker = VariousArtistsWorker(jobs)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.failed.connect(self._on_failed)

        self._worker.finished.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_refs)

        progress.canceled.connect(self._cancel)
        self._thread.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
        if self._progress is not None:
            self._progress.setLabelText("Finishing current file...")

    def _clear_refs(self) -> None:
        self._worker = None
        self._thread = None

    def cancel_running_job(self) -> None:
        self._cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(8000)

    @Slot(int, int, str)
    def _on_progress(self, processed: int, total: int, detail: str) -> None:
        progress = self._progress
        if progress is None:
            return
        try:
            progress.setRange(0, max(total, 1))
            progress.setValue(min(processed, max(total, 1)))
            progress.setLabelText(detail)
        except RuntimeError:
            self._progress = None

    def _finish_ui(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                worker.progress.disconnect(self._on_progress)
            except (RuntimeError, TypeError):
                pass
        progress = self._progress
        self._progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._busy = False

    def _apply_results(self, payload: dict) -> int:
        updated_paths = payload.get("updated_paths", [])
        if self._data_model is not None:
            for filepath in updated_paths:
                self._data_model.update_metadata(filepath, self._pending_tags[filepath])
        self._pending_tags = {}
        if updated_paths:
            self.library_changed.emit(self._data_model)
        else:
            self._update_state()
        return len(updated_paths)

    @Slot(object)
    def _on_finished(self, payload: dict) -> None:
        self._finish_ui()
        updated = self._apply_results(payload)
        errors = payload.get("errors", [])
        total = payload.get("total", updated)
        if errors:
            preview = "\n".join(errors[:8])
            extra = f"\n…and {len(errors) - 8} more." if len(errors) > 8 else ""
            QMessageBox.warning(
                self,
                "Normalise finished with errors",
                f"Updated {updated} of {total} songs.\n\n{preview}{extra}",
            )
        else:
            QMessageBox.information(
                self,
                "Normalise complete",
                f"Updated artist tags on {updated} song{'s' if updated != 1 else ''}.",
            )

    @Slot(object)
    def _on_cancelled(self, payload: dict) -> None:
        self._finish_ui()
        updated = self._apply_results(payload)
        QMessageBox.information(
            self,
            "Normalise cancelled",
            f"Cancelled after updating {updated} of {payload.get('total', 0)} songs.",
        )

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._finish_ui()
        self._pending_tags = {}
        self._update_state()
        QMessageBox.critical(self, "Normalise failed", message)

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        self._refresh_rows()

        header = self._table.horizontalHeader()
        metrics = header.fontMetrics()
        baselines = {
            VariousArtistsColumn.CHECK: 30,
            VariousArtistsColumn.TITLE: 220,
            VariousArtistsColumn.ALBUM: 180,
            VariousArtistsColumn.ARTIST: 170,
            VariousArtistsColumn.ALBUM_ARTIST: 170,
        }
        for col, baseline in baselines.items():
            text_width = metrics.horizontalAdvance(VariousArtistsColumn.HEADERS[col].upper()) + 45
            header.resizeSection(col, max(baseline, text_width))
        header.resizeSection(VariousArtistsColumn.FILE, PATH_COLUMN_WIDTH)

    def set_processing_state(self, is_processing: bool) -> None:
        self._stack.setCurrentIndex(0 if is_processing else 1)
        self._update_state()
