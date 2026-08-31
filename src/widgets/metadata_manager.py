"""
Metadata Manager — main composite widget.

Combines the search/filter toolbar, stat cards, and the styled table view
into a complete metadata browsing interface.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..demo_data import generate_demo_tracks
from ..models.metadata_filter_proxy import MetadataFilterProxyModel
from ..models.metadata_table_model import Column, MetadataTableModel
from ..theme import Colours
from ..views.delegates import CodecBadgeDelegate, MissingFieldDelegate
from ..views.metadata_table_view import MetadataTableView
from .stat_card import StatCard


class MetadataManager(QWidget):
    """
    Complete metadata management interface.

    Contains:
    - Header with title
    - Search bar + filter controls toolbar
    - Stat cards row
    - Full-width metadata table
    - Status bar with row counts
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_models()
        self._init_ui()
        self._connect_signals()
        self._load_demo_data()

    # ── Model setup ─────────────────────────────────────────────────

    def _init_models(self) -> None:
        self._source_model = MetadataTableModel(self)
        self._proxy_model = MetadataFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)

    # ── UI construction ─────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(12)

        # ── Header ──────────────────────────────────────────────
        header = self._build_header()
        layout.addWidget(header)

        # ── Separator ───────────────────────────────────────────
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ── Stacked Widget ──────────────────────────────────────
        self._stack = QStackedWidget()
        
        # Page 0: Loading State
        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignCenter)
        
        load_lbl = QLabel("Results will be ready soon.")
        load_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 20px; font-weight: 700;")
        load_lbl.setAlignment(Qt.AlignCenter)
        
        sub_lbl = QLabel("Music is being processed...")
        sub_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 14px;")
        sub_lbl.setAlignment(Qt.AlignCenter)
        
        loading_layout.addWidget(load_lbl)
        loading_layout.addWidget(sub_lbl)
        
        self._stack.addWidget(loading_page)
        
        # Page 1: Data State
        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        # ── Toolbar (search + filters) ──────────────────────────
        toolbar = self._build_toolbar()
        data_layout.addWidget(toolbar)

        # ── Stat cards ──────────────────────────────────────────
        stats = self._build_stat_cards()
        data_layout.addLayout(stats)

        # ── Table ───────────────────────────────────────────────
        self._table_view = MetadataTableView(self)
        self._table_view.setModel(self._proxy_model)

        # Apply delegates
        self._codec_delegate = CodecBadgeDelegate(self)
        self._table_view.setItemDelegateForColumn(Column.CODEC, self._codec_delegate)

        self._missing_title_delegate = MissingFieldDelegate(self)
        self._missing_artist_delegate = MissingFieldDelegate(self)
        self._missing_album_delegate = MissingFieldDelegate(self)
        self._table_view.setItemDelegateForColumn(Column.TITLE, self._missing_title_delegate)
        self._table_view.setItemDelegateForColumn(Column.ARTIST, self._missing_artist_delegate)
        self._table_view.setItemDelegateForColumn(Column.ALBUM, self._missing_album_delegate)

        self._table_view.apply_column_widths()
        data_layout.addWidget(self._table_view, 1)  # stretch factor 1 — fills remaining space

        # ── Status bar ──────────────────────────────────────────
        status = self._build_status_bar()
        data_layout.addLayout(status)
        
        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _build_header(self) -> QWidget:
        """Build the section header with title and subtitle."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title = QLabel("Metadata Manager")
        title.setObjectName("headerTitle")
        layout.addWidget(title)

        subtitle = QLabel("Browse and manage audio file metadata across your library")
        subtitle.setObjectName("headerSubtitle")
        layout.addWidget(subtitle)

        return container

    def _build_toolbar(self) -> QWidget:
        """Build the search + filter toolbar panel."""
        panel = QWidget()
        panel.setObjectName("panelSection")
        
        toolbar = QHBoxLayout(panel)
        toolbar.setContentsMargins(12, 8, 12, 8)
        toolbar.setSpacing(12)

        # Search input
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍  Search by title, artist, album, file path…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumHeight(34)
        toolbar.addWidget(self._search_input, 1)

        # Codec filter
        self._codec_combo = QComboBox()
        self._codec_combo.addItems(["All Codecs", "FLAC", "MP3", "AAC", "OGG", "WAV", "OPUS"])
        self._codec_combo.setMinimumHeight(34)
        toolbar.addWidget(self._codec_combo)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {Colours.BORDER_SUBTLE};")
        sep.setFixedWidth(1)
        toolbar.addWidget(sep)

        # Missing metadata filter checkboxes
        self._chk_missing_title = QCheckBox("Missing Title")
        self._chk_missing_artist = QCheckBox("Missing Artist")
        self._chk_missing_album = QCheckBox("Missing Album")

        toolbar.addWidget(self._chk_missing_title)
        toolbar.addWidget(self._chk_missing_artist)
        toolbar.addWidget(self._chk_missing_album)

        # Spacer + action button
        toolbar.addStretch()

        self._scan_btn = QPushButton("⟳  Refresh")
        self._scan_btn.setObjectName("accentButton")
        self._scan_btn.setMinimumHeight(34)
        toolbar.addWidget(self._scan_btn)

        return panel

    def _build_stat_cards(self) -> QHBoxLayout:
        """Build the row of stat summary cards."""
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self._stat_total = StatCard("Total Tracks", Colours.STAT_TOTAL, self)
        self._stat_missing = StatCard("Missing Metadata", Colours.STAT_MISSING, self)
        self._stat_title = StatCard("Missing Title", Colours.STAT_TITLE, self)
        self._stat_artist = StatCard("Missing Artist", Colours.STAT_ARTIST, self)
        self._stat_album = StatCard("Missing Album", Colours.STAT_ALBUM, self)

        stats_layout.addWidget(self._stat_total)
        stats_layout.addWidget(self._stat_missing)
        stats_layout.addWidget(self._stat_title)
        stats_layout.addWidget(self._stat_artist)
        stats_layout.addWidget(self._stat_album)
        stats_layout.addStretch(1)

        return stats_layout

    def _build_status_bar(self) -> QHBoxLayout:
        """Build the bottom status row with row count info."""
        status_layout = QHBoxLayout()
        status_layout.setSpacing(16)

        self._status_showing = QLabel("Showing 0 tracks")
        self._status_showing.setObjectName("subtitleLabel")
        status_layout.addWidget(self._status_showing)

        self._status_selected = QLabel("")
        self._status_selected.setObjectName("subtitleLabel")
        status_layout.addWidget(self._status_selected)

        status_layout.addStretch()

        self._status_total = QLabel("")
        self._status_total.setObjectName("subtitleLabel")
        status_layout.addWidget(self._status_total)

        return status_layout

    # ── Signal connections ──────────────────────────────────────────

    def _connect_signals(self) -> None:
        # Search
        self._search_input.textChanged.connect(self._on_search_changed)

        # Codec filter
        self._codec_combo.currentTextChanged.connect(self._on_codec_filter_changed)

        # Missing field checkboxes
        self._chk_missing_title.stateChanged.connect(
            lambda state: self._proxy_model.set_show_missing_title(state == Qt.Checked)
        )
        self._chk_missing_artist.stateChanged.connect(
            lambda state: self._proxy_model.set_show_missing_artist(state == Qt.Checked)
        )
        self._chk_missing_album.stateChanged.connect(
            lambda state: self._proxy_model.set_show_missing_album(state == Qt.Checked)
        )

        # All filter changes update the status bar
        self._chk_missing_title.stateChanged.connect(self._update_status_bar)
        self._chk_missing_artist.stateChanged.connect(self._update_status_bar)
        self._chk_missing_album.stateChanged.connect(self._update_status_bar)

        # Scan button (reloads demo data for now)
        self._scan_btn.clicked.connect(self._load_demo_data)

        # Selection changes
        self._table_view.selectionModel()

    # ── Filter handlers ─────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.set_search_query(text)
        self._update_status_bar()

    def _on_codec_filter_changed(self, text: str) -> None:
        codec = "" if text == "All Codecs" else text.lower()
        self._proxy_model.set_codec_filter(codec)
        self._update_status_bar()

    # ── Data loading ────────────────────────────────────────────────

    def _load_demo_data(self) -> None:
        """Load demo tracks into the model."""
        tracks = generate_demo_tracks(200)
        self._source_model.update_data(tracks)

        # Re-apply column widths after data load
        self._table_view.apply_column_widths()

        # Reconnect selection model (reset by model update)
        sel_model = self._table_view.selectionModel()
        if sel_model:
            sel_model.selectionChanged.connect(self._on_selection_changed)

        # Update stats with animation (slight delay for visual effect)
        QTimer.singleShot(100, self._update_stats)
        self._update_status_bar()

    def _update_stats(self) -> None:
        """Update stat cards with current model data."""
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_missing.set_value(self._source_model.missing_any_count())
        self._stat_title.set_value(self._source_model.missing_title_count())
        self._stat_artist.set_value(self._source_model.missing_artist_count())
        self._stat_album.set_value(self._source_model.missing_album_count())

    def _update_status_bar(self) -> None:
        """Update the bottom status bar text."""
        visible = self._proxy_model.visible_row_count()
        total = self._source_model.total_tracks()

        if visible == total:
            self._status_showing.setText(f"Showing all {total:,} tracks")
        else:
            self._status_showing.setText(f"Showing {visible:,} of {total:,} tracks")

        self._status_total.setText(f"Total: {total:,}")

    def _on_selection_changed(self) -> None:
        """Update selection count in status bar."""
        sel = self._table_view.selectionModel()
        if sel:
            count = len(sel.selectedRows())
            if count > 0:
                self._status_selected.setText(f"{count} selected")
            else:
                self._status_selected.setText("")
                
    def set_processing_state(self, is_processing: bool) -> None:
        """Toggle between loading view and data view."""
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
