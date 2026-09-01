import os
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableView,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QLineEdit,
    QComboBox,
    QFrame
)

from ..theme import Colours
from ..models.drive_data import DriveDataModel
from ..models.music_compatibility_model import (
    MusicCompatibilityTableModel,
    MusicCompatibilityFilterProxyModel,
    CompColumn
)
from .stat_card import StatCard
from PySide6.QtWidgets import QStyledItemDelegate
from PySide6.QtGui import QBrush

class HighlightDelegate(QStyledItemDelegate):
    """Custom delegate to enforce background colors over QSS/Alternating rows."""
    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if bg:
            painter.fillRect(option.rect, bg)
            # Prevent base class from trying to draw the background again
            option.backgroundBrush = QBrush(Qt.NoBrush)
        super().paint(painter, option, index)

class MusicCompatibilityWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model = None
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

        # Header container
        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(20)

        # Titles (Left)
        title_container = QWidget()
        title_layout = QVBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(2)
        
        title = QLabel("Music Compatibility")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        subtitle = QLabel("Check and convert unsupported media files")
        subtitle.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        title_layout.addStretch() # Push titles to top
        
        h_layout.addWidget(title_container)
        h_layout.addStretch(1) # Space between titles and stats

        # Stat cards (Right)
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


        # Stacked Widget
        self._stack = QStackedWidget()
        
        # Loading Page
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
        
        # Data Page
        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)

        # Toolbar
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

        # Table
        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        header_view = self._table.horizontalHeader()
        header_view.setStretchLastSection(True)
        header_view.setSectionsMovable(True)
        
        self._delegate = HighlightDelegate(self._table)
        self._table.setItemDelegate(self._delegate)
        
        data_layout.addWidget(self._table, 1)

        self._stack.addWidget(data_page)
        layout.addWidget(self._stack, 1)

    def _connect_signals(self) -> None:
        self._search_input.textChanged.connect(self._on_search_changed)
        self._status_combo.currentTextChanged.connect(self._on_status_filter_changed)

    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.set_search_query(text)

    def _on_status_filter_changed(self, text: str) -> None:
        self._proxy_model.set_status_filter(text)

    def populate_data(self, data_model: DriveDataModel) -> None:
        self._data_model = data_model
        tracks = list(data_model.tracks.values())
        self._source_model.update_data(tracks, data_model.root_path)
        
        header = self._table.horizontalHeader()
        header.resizeSection(CompColumn.TITLE, 200)
        header.resizeSection(CompColumn.ARTIST, 150)
        header.resizeSection(CompColumn.ALBUM, 150)
        header.resizeSection(CompColumn.STATUS, 100)
        header.resizeSection(CompColumn.REASON, 300)
        
        QTimer.singleShot(100, self._update_stats)

    def _update_stats(self) -> None:
        self._stat_total.set_value(self._source_model.total_tracks())
        self._stat_supported.set_value(self._source_model.count_by_status("SUPPORTED"))
        self._stat_limited.set_value(self._source_model.count_by_status("LIMITED"))
        self._stat_unsupported.set_value(
            self._source_model.count_by_status("UNSUPPORTED") + 
            self._source_model.count_by_status("UNKNOWN")
        )
        self._stat_no_eq.set_value(self._source_model.count_by_eq("Not EQ Compatible"))

    def set_processing_state(self, is_processing: bool) -> None:
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
