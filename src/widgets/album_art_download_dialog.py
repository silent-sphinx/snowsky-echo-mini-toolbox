"""Album-level review dialog for downloaded cover art."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..theme import Colours
from ..utils.album_art_download import AlbumArtLookupClient, LookupCancelled
from ..utils.album_art_download_planner import (
    AlbumGroup,
    SkippedTrack,
    download_candidates,
)

# Outer size of the preview frame; the 1px stylesheet border eats into it.
PREVIEW_FRAME = 222
PREVIEW_SIZE = PREVIEW_FRAME - 4

# Floors that keep every pane usable no matter how the dialog is resized.
MIN_TABLE_HEIGHT = 120
MIN_TRACK_LIST_HEIGHT = 90
MIN_FIELD_WIDTH = 130
MIN_DIALOG_WIDTH = 980


class WrappedLabel(QLabel):
    """Word-wrapped label that always reserves the height its text needs.

    A plain wrapped QLabel reports a one-line minimum, so a vertical layout
    under pressure hands it too little room and silently clips the rest.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def setText(self, text: str) -> None:
        super().setText(text)
        self._sync_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        width = self.width()
        if width <= 0:
            return
        needed = self.heightForWidth(width)
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)


class _PreviewLoader(QObject):
    """Fetches one thumbnail off the UI thread."""

    loaded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, client: AlbumArtLookupClient, url: str):
        super().__init__()
        self._client = client
        self._url = url

    @Slot()
    def run(self) -> None:
        try:
            data = self._client.fetch_image(self._url)
        except LookupCancelled:
            self.failed.emit(self._url, "cancelled")
        except Exception as exc:
            self.failed.emit(self._url, str(exc))
        else:
            self.loaded.emit(self._url, data)


class _RetryLookup(QObject):
    """Re-runs a search for a single album group."""

    done = Signal(object)

    def __init__(self, client: AlbumArtLookupClient, artist: str, album: str, year: str):
        super().__init__()
        self._client = client
        self._artist = artist
        self._album = album
        self._year = year

    @Slot()
    def run(self) -> None:
        try:
            result = self._client.search_album(self._artist, self._album, year=self._year)
        except LookupCancelled:
            self.done.emit(None)
        except Exception as exc:
            self.done.emit({"error": str(exc)})
        else:
            self.done.emit(result)


class AlbumArtDownloadDialog(QDialog):
    """Review one cover choice per album before writing to every track."""

    COL_APPLY = 0
    COL_ARTIST = 1
    COL_ALBUM = 2
    COL_YEAR = 3
    COL_TRACKS = 4
    COL_STATUS = 5
    COL_RELEASE = 6

    def __init__(
        self,
        groups: list[AlbumGroup],
        skipped: list[SkippedTrack],
        root_path: str,
        client: AlbumArtLookupClient,
        parent=None,
    ):
        super().__init__(parent)
        self._groups = groups
        self._skipped = skipped
        self._root_path = root_path
        self._client = client
        self._quality = 90
        self._dry_run = False
        self._backup_root: Path | None = None
        self._preview_cache: dict[str, QPixmap] = {}
        self._preview_failures: set[str] = set()
        self._preview_thread: QThread | None = None
        self._preview_loader: _PreviewLoader | None = None
        self._retry_thread: QThread | None = None
        self._retry_task: _RetryLookup | None = None
        self._pending_preview_url = ""
        self._updating = False

        self.setObjectName("albumArtDownloadDialog")
        self.setWindowTitle("Download Missing Artwork")
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(MIN_DIALOG_WIDTH)
        self.setStyleSheet(
            f"""
            QDialog#albumArtDownloadDialog {{
                background-color: {Colours.BG_ELEVATED};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            QDialog#albumArtDownloadDialog QLabel {{
                background-color: transparent;
                color: {Colours.TEXT_PRIMARY};
            }}
            QDialog#albumArtDownloadDialog QCheckBox {{
                spacing: 8px;
                color: {Colours.TEXT_PRIMARY};
                padding: 2px 0px;
            }}
            QDialog#albumArtDownloadDialog QTableWidget,
            QDialog#albumArtDownloadDialog QListWidget {{
                background-color: {Colours.BG_SURFACE};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
                gridline-color: {Colours.BORDER_SUBTLE};
            }}
            QDialog#albumArtDownloadDialog QHeaderView::section {{
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
        self._refresh_table()
        if self._groups:
            self._table.selectRow(0)
        self._apply_size_bounds()

    def _apply_size_bounds(self) -> None:
        """Pin the dialog so no pane can be resized out of existence."""
        layout = self.layout()
        if layout is not None:
            layout.activate()
            required = layout.minimumSize()
            self.setMinimumSize(
                max(MIN_DIALOG_WIDTH, required.width()), required.height()
            )

        width, height = 1080, 780
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width() - 80)
            height = min(height, available.height() - 80)

        self.resize(
            max(width, self.minimumWidth()), max(height, self.minimumHeight())
        )

    # ── construction ────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Download Missing Artwork")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        self._summary_label = WrappedLabel()
        self._summary_label.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")
        layout.addWidget(self._summary_label)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Apply", "Album Artist", "Album", "Year", "Tracks", "Search Status", "Selected Release"]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(MIN_TABLE_HEIGHT)
        self._table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(self.COL_APPLY, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_ARTIST, QHeaderView.Interactive)
        header.setSectionResizeMode(self.COL_ALBUM, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_YEAR, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_TRACKS, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.Interactive)
        header.setSectionResizeMode(self.COL_RELEASE, QHeaderView.Stretch)
        header.setMinimumSectionSize(64)
        self._table.setColumnWidth(self.COL_ARTIST, 180)
        self._table.setColumnWidth(self.COL_STATUS, 170)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self._table)

        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)

        layout.addWidget(self._build_options_panel())

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        self._confirm_btn = QPushButton("Download and Apply")
        self._confirm_btn.setObjectName("accentButton")
        for button in (cancel_btn, self._confirm_btn):
            button.setMinimumWidth(button.sizeHint().width())
            button.setMinimumHeight(30)
        cancel_btn.clicked.connect(self.reject)
        self._confirm_btn.clicked.connect(self._on_confirm)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(self._confirm_btn)
        layout.addLayout(button_row)

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget()
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._artist_input = QLineEdit()
        self._artist_input.setPlaceholderText("Album artist")
        self._album_input = QLineEdit()
        self._album_input.setPlaceholderText("Album title")
        for field in (self._artist_input, self._album_input):
            field.setMinimumWidth(MIN_FIELD_WIDTH)
        self._retry_btn = QPushButton("Retry Search")
        self._retry_btn.clicked.connect(self._on_retry_search)
        search_label = QLabel("Search")
        search_label.setMinimumWidth(search_label.sizeHint().width())
        search_row.addWidget(search_label)
        search_row.addWidget(self._artist_input, 1)
        search_row.addWidget(self._album_input, 1)
        search_row.addWidget(self._retry_btn)
        left.addLayout(search_row)

        candidate_row = QHBoxLayout()
        candidate_row.setSpacing(6)
        self._candidate_combo = QComboBox()
        self._candidate_combo.setMinimumHeight(30)
        self._candidate_combo.setMinimumWidth(MIN_FIELD_WIDTH)
        # Long release titles must not force the whole dialog wider.
        self._candidate_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._candidate_combo.setMinimumContentsLength(24)
        self._candidate_combo.currentIndexChanged.connect(self._on_candidate_changed)
        self._prev_btn = QPushButton("Previous")
        self._next_btn = QPushButton("Next")
        self._prev_btn.clicked.connect(lambda: self._step_candidate(-1))
        self._next_btn.clicked.connect(lambda: self._step_candidate(1))
        cover_label = QLabel("Cover")
        cover_label.setMinimumWidth(search_label.sizeHint().width())
        candidate_row.addWidget(cover_label)
        candidate_row.addWidget(self._candidate_combo, 1)
        candidate_row.addWidget(self._prev_btn)
        candidate_row.addWidget(self._next_btn)
        left.addLayout(candidate_row)

        self._release_detail = WrappedLabel()
        self._release_detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._release_detail.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px;")
        left.addWidget(self._release_detail)

        left.addWidget(QLabel("Tracks that will receive this cover:"))
        self._tracks_list = QListWidget()
        self._tracks_list.setMinimumHeight(MIN_TRACK_LIST_HEIGHT)
        self._tracks_list.setTextElideMode(Qt.ElideMiddle)
        self._tracks_list.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self._tracks_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left.addWidget(self._tracks_list, 1)

        left_holder = QWidget()
        left_holder.setLayout(left)
        left_holder.setMinimumWidth(360)
        outer.addWidget(left_holder, 1)

        preview_box = QVBoxLayout()
        preview_box.setSpacing(6)
        preview_box.addWidget(QLabel("Preview"))
        self._preview_label = QLabel("No cover selected")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setFixedSize(PREVIEW_FRAME, PREVIEW_FRAME)
        self._preview_label.setStyleSheet(
            f"background-color: {Colours.BG_SURFACE};"
            f"border: 1px solid {Colours.BORDER_DEFAULT};"
            f"color: {Colours.TEXT_SECONDARY};"
        )
        preview_box.addWidget(self._preview_label)
        preview_box.addStretch(1)

        preview_holder = QWidget()
        preview_holder.setLayout(preview_box)
        preview_holder.setFixedWidth(PREVIEW_FRAME)
        outer.addWidget(preview_holder, 0)

        # Floor the pane at what its children actually need, not at their
        # preferred size, so the splitter stays usable on short screens.
        outer.activate()
        panel.setMinimumHeight(outer.minimumSize().height())
        return panel

    def _build_options_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        quality_row = QHBoxLayout()
        quality_row.setSpacing(8)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(50, 100)
        self._quality_spin.setValue(self._quality)
        self._quality_spin.setToolTip(
            "Higher values keep more detail but produce larger embedded images."
        )
        quality_row.addWidget(QLabel("JPEG quality"))
        quality_row.addWidget(self._quality_spin)
        quality_row.addStretch(1)
        layout.addLayout(quality_row)

        self._backup_checkbox = QCheckBox("Back up originals before writing")
        self._backup_path_input = QLineEdit()
        self._backup_path_input.setPlaceholderText("Choose a backup folder...")
        self._backup_path_input.setMinimumWidth(MIN_FIELD_WIDTH)
        backup_browse = QPushButton("Browse")
        backup_browse.clicked.connect(self._choose_backup_folder)

        backup_row = QHBoxLayout()
        backup_row.setContentsMargins(22, 0, 0, 0)
        backup_row.addWidget(self._backup_path_input, 1)
        backup_row.addWidget(backup_browse)
        self._backup_path_container = QWidget()
        self._backup_path_container.setLayout(backup_row)
        self._backup_path_container.setVisible(False)
        self._backup_checkbox.toggled.connect(self._update_backup_visibility)
        layout.addWidget(self._backup_checkbox)
        layout.addWidget(self._backup_path_container)

        self._dry_run_checkbox = QCheckBox("Dry run (preview only)")
        self._dry_run_checkbox.toggled.connect(self._update_backup_visibility)
        layout.addWidget(self._dry_run_checkbox)

        if self._skipped:
            skipped_label = WrappedLabel(self._skipped_summary())
            skipped_label.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px;")
            layout.addWidget(skipped_label)

        panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        return panel

    def _skipped_summary(self) -> str:
        reasons: dict[str, int] = {}
        for item in self._skipped:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
        parts = [f"{count} × {reason}" for reason, count in sorted(reasons.items())]
        return f"Excluded {len(self._skipped)} selected file(s): " + "; ".join(parts)

    # ── table ───────────────────────────────────────────────────

    def _refresh_table(self) -> None:
        self._updating = True
        self._table.setRowCount(len(self._groups))

        for row, group in enumerate(self._groups):
            apply_item = QTableWidgetItem()
            apply_item.setFlags(
                (apply_item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable
            )
            can_apply = group.has_candidates
            apply_item.setCheckState(
                Qt.Checked if (can_apply and group.is_selected) else Qt.Unchecked
            )
            if not can_apply:
                apply_item.setFlags(apply_item.flags() & ~Qt.ItemIsEnabled)

            cover = group.selected_candidate
            status = "Cover found" if can_apply else (group.error or "No cover found")

            cells = [
                apply_item,
                QTableWidgetItem(group.display_artist),
                QTableWidgetItem(group.album),
                QTableWidgetItem(group.year),
                QTableWidgetItem(str(group.track_count)),
                QTableWidgetItem(status),
                QTableWidgetItem(cover.display_label if cover else ""),
            ]
            for column, item in enumerate(cells):
                if column != self.COL_APPLY:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setToolTip(item.text())
                self._table.setItem(row, column, item)

        self._updating = False
        self._update_summary()

    def _update_summary(self) -> None:
        selected = [g for g in self._groups if g.is_actionable]
        files = sum(g.track_count for g in selected)
        without = sum(1 for g in self._groups if not g.has_candidates)

        text = (
            f"{len(self._groups)} album(s) grouped from your selection; "
            f"{len(selected)} selected, covering {files} file(s). "
            f"One cover is downloaded per album and written to every track in it."
        )
        if without:
            text += f" {without} album(s) have no cover available."
        self._summary_label.setText(text)
        self._confirm_btn.setEnabled(bool(selected))

    def _current_group(self) -> AlbumGroup | None:
        row = self._table.currentRow()
        if 0 <= row < len(self._groups):
            return self._groups[row]
        return None

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != self.COL_APPLY:
            return
        row = item.row()
        if 0 <= row < len(self._groups):
            self._groups[row].is_selected = item.checkState() == Qt.Checked
            self._update_summary()

    def _on_row_selected(self) -> None:
        self._load_detail(self._current_group())

    # ── detail panel ────────────────────────────────────────────

    def _load_detail(self, group: AlbumGroup | None) -> None:
        self._updating = True
        try:
            self._candidate_combo.clear()
            self._tracks_list.clear()

            if group is None:
                self._artist_input.setText("")
                self._album_input.setText("")
                self._release_detail.setText("")
                self._set_preview(None, "No album selected")
                self._set_detail_enabled(False)
                return

            self._set_detail_enabled(True)
            self._artist_input.setText(group.artist)
            self._album_input.setText(group.album)

            for path in group.relative_paths(self._root_path):
                self._tracks_list.addItem(path)
                self._tracks_list.item(self._tracks_list.count() - 1).setToolTip(path)

            for candidate in group.candidates:
                self._candidate_combo.addItem(candidate.display_label)
            if group.candidates:
                self._candidate_combo.setCurrentIndex(
                    max(0, min(group.selected_index, len(group.candidates) - 1))
                )
        finally:
            self._updating = False

        self._sync_candidate_view(group)

    def _set_detail_enabled(self, enabled: bool) -> None:
        for widget in (
            self._artist_input,
            self._album_input,
            self._retry_btn,
            self._candidate_combo,
            self._prev_btn,
            self._next_btn,
        ):
            widget.setEnabled(enabled)

    def _sync_candidate_view(self, group: AlbumGroup | None) -> None:
        if group is None:
            return

        cover = group.selected_candidate
        count = len(group.candidates)
        self._prev_btn.setEnabled(count > 1 and group.selected_index > 0)
        self._next_btn.setEnabled(count > 1 and group.selected_index < count - 1)
        self._candidate_combo.setEnabled(count > 0)

        if cover is None:
            self._release_detail.setText(group.error or "No cover art found for this album.")
            self._set_preview(None, "No cover available")
            return

        details = [
            f"Release: {cover.release_title or 'Untitled'}",
            f"Artist: {cover.artist_credit or 'Unknown'}",
            f"Date: {cover.date or 'Unknown'}",
            f"Country: {cover.country or 'Unknown'}",
            f"Status: {cover.status or 'Unknown'}",
            f"Type: {cover.release_group_type or 'Unknown'}",
            f"MBID: {cover.release_id}",
            f"Cover {group.selected_index + 1} of {count}",
        ]
        self._release_detail.setText("  |  ".join(details))
        self._request_preview(cover.thumbnail_url)

    def _on_candidate_changed(self, index: int) -> None:
        if self._updating:
            return
        group = self._current_group()
        if group is None or index < 0:
            return
        group.selected_index = index
        self._sync_candidate_view(group)
        self._refresh_row(self._table.currentRow(), group)

    def _step_candidate(self, delta: int) -> None:
        group = self._current_group()
        if group is None or not group.candidates:
            return
        new_index = max(0, min(group.selected_index + delta, len(group.candidates) - 1))
        self._candidate_combo.setCurrentIndex(new_index)

    def _refresh_row(self, row: int, group: AlbumGroup) -> None:
        if row < 0:
            return
        self._updating = True
        cover = group.selected_candidate
        status_item = self._table.item(row, self.COL_STATUS)
        release_item = self._table.item(row, self.COL_RELEASE)
        apply_item = self._table.item(row, self.COL_APPLY)

        if status_item is not None:
            status_item.setText("Cover found" if cover else (group.error or "No cover found"))
        if release_item is not None:
            release_item.setText(cover.display_label if cover else "")
            release_item.setToolTip(release_item.text())
        if apply_item is not None:
            if cover:
                apply_item.setFlags(apply_item.flags() | Qt.ItemIsEnabled)
                apply_item.setCheckState(Qt.Checked if group.is_selected else Qt.Unchecked)
            else:
                apply_item.setCheckState(Qt.Unchecked)
                apply_item.setFlags(apply_item.flags() & ~Qt.ItemIsEnabled)

        self._table.item(row, self.COL_ARTIST).setText(group.display_artist)
        self._table.item(row, self.COL_ALBUM).setText(group.album)
        self._updating = False
        self._update_summary()

    # ── preview loading ─────────────────────────────────────────

    def _set_preview(self, pixmap: QPixmap | None, message: str = "") -> None:
        if pixmap is None:
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText(message)
        else:
            self._preview_label.setText("")
            self._preview_label.setPixmap(pixmap)

    def _request_preview(self, url: str) -> None:
        if not url:
            self._set_preview(None, "No preview")
            return

        cached = self._preview_cache.get(url)
        if cached is not None:
            self._set_preview(cached)
            return

        if url in self._preview_failures:
            self._set_preview(None, "Preview unavailable")
            return

        self._set_preview(None, "Loading preview...")
        self._pending_preview_url = url

        # A request is already in flight; it will pick this url up when it ends.
        if self._preview_thread is not None and self._preview_thread.isRunning():
            return

        self._start_preview_fetch(url)

    def _start_preview_fetch(self, url: str) -> None:
        thread = QThread(self)
        loader = _PreviewLoader(self._client, url)
        loader.moveToThread(thread)
        thread.started.connect(loader.run)
        loader.loaded.connect(self._on_preview_loaded)
        loader.failed.connect(self._on_preview_failed)
        loader.loaded.connect(thread.quit)
        loader.failed.connect(thread.quit)
        thread.finished.connect(self._on_preview_thread_finished)

        # Qt does not own the loader, so the dialog must keep it alive until the
        # thread has delivered started() and finished running it.
        self._preview_thread = thread
        self._preview_loader = loader
        thread.start()

    @Slot(str, object)
    def _on_preview_loaded(self, url: str, data: object) -> None:
        pixmap = QPixmap()
        if isinstance(data, (bytes, bytearray)) and pixmap.loadFromData(bytes(data)):
            scaled = pixmap.scaled(
                PREVIEW_SIZE,
                PREVIEW_SIZE,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._preview_cache[url] = scaled
            if url == self._pending_preview_url:
                self._set_preview(scaled)
        elif url == self._pending_preview_url:
            self._set_preview(None, "Preview unavailable")

    @Slot(str, str)
    def _on_preview_failed(self, url: str, error: str) -> None:
        # Remember the failure so the thread-finished handler does not retry it
        # forever, which would leave the label stuck on "Loading preview...".
        if error != "cancelled":
            self._preview_failures.add(url)
        if url == self._pending_preview_url:
            self._set_preview(None, "Preview unavailable")

    def _on_preview_thread_finished(self) -> None:
        thread = self._preview_thread
        loader = self._preview_loader
        self._preview_thread = None
        self._preview_loader = None
        if loader is not None:
            loader.deleteLater()
        if thread is not None:
            thread.deleteLater()

        # A newer request may have arrived while this one was in flight.
        pending = self._pending_preview_url
        if (
            pending
            and pending not in self._preview_cache
            and pending not in self._preview_failures
        ):
            self._start_preview_fetch(pending)

    # ── retry search ────────────────────────────────────────────

    def _on_retry_search(self) -> None:
        group = self._current_group()
        if group is None:
            return
        if self._retry_thread is not None and self._retry_thread.isRunning():
            return

        group.artist = self._artist_input.text().strip()
        group.album = self._album_input.text().strip()
        if not group.album and not group.artist:
            QMessageBox.information(
                self,
                "Nothing To Search",
                "Enter an album title or artist name before retrying the search.",
            )
            return

        self._retry_btn.setEnabled(False)
        self._retry_btn.setText("Searching...")
        row = self._table.currentRow()

        thread = QThread(self)
        task = _RetryLookup(self._client, group.artist, group.album, group.year)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.done.connect(lambda result: self._on_retry_done(row, result))
        task.done.connect(thread.quit)
        thread.finished.connect(self._clear_retry_thread)
        self._retry_thread = thread
        self._retry_task = task
        thread.start()

    def _on_retry_done(self, row: int, result: object) -> None:
        self._retry_btn.setEnabled(True)
        self._retry_btn.setText("Retry Search")

        if not (0 <= row < len(self._groups)) or result is None:
            return

        group = self._groups[row]
        if isinstance(result, dict):
            group.candidates = []
            group.error = result.get("error", "Lookup failed")
            group.is_selected = False
        else:
            group.candidates = result.candidates
            group.error = result.error
            group.query = result.query
            group.selected_index = 0
            group.is_selected = result.has_candidates

        if row == self._table.currentRow():
            self._load_detail(group)
        self._refresh_row(row, group)

    def _clear_retry_thread(self) -> None:
        thread = self._retry_thread
        task = self._retry_task
        self._retry_thread = None
        self._retry_task = None
        if task is not None:
            task.deleteLater()
        if thread is not None:
            thread.deleteLater()

    # ── options and confirm ─────────────────────────────────────

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
                    "Choose a backup folder or disable backup before downloading.",
                )
                return
            backup_candidate = Path(backup_text).expanduser()
            try:
                self._backup_root = backup_candidate.resolve()
            except Exception:
                self._backup_root = backup_candidate

        self._dry_run = self._dry_run_checkbox.isChecked()
        self._quality = self._quality_spin.value()
        self.accept()

    def closeEvent(self, event) -> None:
        for thread in (self._preview_thread, self._retry_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2000)
        super().closeEvent(event)

    @property
    def quality(self) -> int:
        return self._quality

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def backup_root(self) -> Path | None:
        return self._backup_root

    def candidates(self) -> list[dict[str, object]]:
        return download_candidates(self._groups, self._root_path)
