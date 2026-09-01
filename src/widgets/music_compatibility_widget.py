import os
from PySide6.QtCore import Qt, QTimer, QModelIndex
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
from .grouped_header_view import GroupedHeaderView
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
        self._last_checked_row = None
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
        
        header_view = GroupedHeaderView(self._table)
        self._table.setHorizontalHeader(header_view)
        header_view.setStretchLastSection(True)
        # Add groups based on CompColumn organization
        # Column 0 is the ungrouped Checkbox column
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

    def _on_table_clicked(self, index: QModelIndex) -> None:
        if index.column() == CompColumn.CHECK:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            modifiers = QApplication.keyboardModifiers()
            is_shift = bool(modifiers & Qt.ShiftModifier)
            
            state = self._proxy_model.data(index, Qt.CheckStateRole)
            is_checked = state in (Qt.Checked, Qt.CheckState.Checked, 2)
            new_val = Qt.Checked if is_checked else Qt.Unchecked
            current_row = index.row()
            
            # Handle shift-click range selection
            if is_shift and getattr(self, '_last_checked_row', None) is not None:
                start = min(self._last_checked_row, current_row)
                end = max(self._last_checked_row, current_row)
                for r in range(start, end + 1):
                    if r != current_row:
                        idx = self._proxy_model.index(r, CompColumn.CHECK)
                        self._proxy_model.setData(idx, new_val, Qt.CheckStateRole)
            else:
                # Handle toggling all selected rows
                selection = self._table.selectionModel()
                if selection.isSelected(index):
                    for selected_index in selection.selectedRows(CompColumn.CHECK):
                        if selected_index.row() != current_row:
                            self._proxy_model.setData(selected_index, new_val, Qt.CheckStateRole)
                            
            self._last_checked_row = current_row

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
        
        # Hardcoded baseline widths to ensure content fits
        baselines = {
            CompColumn.CHECK: 30,
            CompColumn.TITLE: 200, CompColumn.ARTIST: 150, CompColumn.ALBUM: 150,
            CompColumn.STATUS: 100, CompColumn.REASON: 300,
            CompColumn.EXTENSION: 80, CompColumn.CODEC: 100, CompColumn.SAMPLE_RATE: 100,
            CompColumn.BIT_DEPTH: 90, CompColumn.CHANNELS: 80, CompColumn.DSD: 80,
            CompColumn.BLOCK_SIZE: 100, CompColumn.STREAMS: 80, CompColumn.EQ: 110,
            CompColumn.CHANNEL_COMPAT: 110, CompColumn.WAV_CODEC: 110, CompColumn.DSD_BITDEPTH: 130,
            CompColumn.TAG_ENCODING: 130, CompColumn.TAG_LENGTH: 110, CompColumn.FILENAME: 110,
            CompColumn.METADATA: 110
        }
        
        for col in range(CompColumn.COUNT):
            if col in baselines:
                # Add 45px padding to account for QSS padding, borders, and the sorting arrow
                text_width = font_metrics.horizontalAdvance(CompColumn.HEADERS[col].upper()) + 45
                header.resizeSection(col, max(baselines[col], text_width))

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
