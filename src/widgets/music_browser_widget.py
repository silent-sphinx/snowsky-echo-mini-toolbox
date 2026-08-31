import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeView,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QApplication,
    QStyle
)

from ..theme import Colours
from ..models.drive_data import DriveDataModel


class MusicBrowserWidget(QWidget):
    """
    Displays the directory tree of the drive and detailed metadata for the selected file.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model: DriveDataModel = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        
        self._stack = QStackedWidget()
        
        # ── Loading Page ─────────────────────────────────────────────
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
        
        # ── Data Page ────────────────────────────────────────────────
        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        
        # Splitter to separate tree (left) and details (right)
        splitter = QSplitter(Qt.Horizontal)
        
        # ── Left: Directory Tree ─────────────────────────────────────
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        
        self._tree = QTreeView()
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(True)
        self._tree.setSortingEnabled(True)
        self._tree_model = QStandardItemModel()
        self._tree.setModel(self._tree_model)
        self._tree.selectionModel().selectionChanged.connect(self._on_selection_changed)
        
        tree_layout.addWidget(self._tree)
        
        # ── Right: Details Tabs ──────────────────────────────────────
        details_container = QWidget()
        details_layout = QVBoxLayout(details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        
        self._tabs = QTabWidget()
        
        # 1. Properties Tab
        self._props_tab = QWidget()
        props_lyt = QVBoxLayout(self._props_tab)
        props_lyt.setContentsMargins(8, 8, 8, 8)
        
        self._props_table = QTableWidget(0, 2)
        self._props_table.setHorizontalHeaderLabels(["Property", "Value"])
        self._props_table.verticalHeader().setVisible(False)
        self._props_table.setAlternatingRowColors(True)
        self._props_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._props_table.horizontalHeader().setStretchLastSection(True)
        self._props_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        props_lyt.addWidget(self._props_table)
        
        # 2. Music Metadata Tab
        self._meta_tab = QWidget()
        meta_lyt = QVBoxLayout(self._meta_tab)
        meta_lyt.setContentsMargins(8, 8, 8, 8)
        
        self._meta_table = QTableWidget(0, 2)
        self._meta_table.setHorizontalHeaderLabels(["Tag", "Value"])
        self._meta_table.verticalHeader().setVisible(False)
        self._meta_table.setAlternatingRowColors(True)
        self._meta_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._meta_table.horizontalHeader().setStretchLastSection(True)
        self._meta_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        meta_lyt.addWidget(self._meta_table)
        
        # 2. Album Art Tab
        self._art_tab = QWidget()
        art_lyt = QVBoxLayout(self._art_tab)
        art_lyt.setContentsMargins(8, 8, 8, 8)
        self._art_lbl = QLabel("No Album Art")
        self._art_lbl.setAlignment(Qt.AlignCenter)
        self._art_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY};")
        art_lyt.addWidget(self._art_lbl)
        
        # 3. Lyrics Tab
        self._lyrics_tab = QWidget()
        lyrics_lyt = QVBoxLayout(self._lyrics_tab)
        lyrics_lyt.setContentsMargins(8, 8, 8, 8)
        self._lyrics_text = QPlainTextEdit()
        self._lyrics_text.setReadOnly(True)
        lyrics_lyt.addWidget(self._lyrics_text)
        
        self._tabs.addTab(self._props_tab, "Properties")
        self._tabs.addTab(self._meta_tab, "Music Metadata")
        self._tabs.addTab(self._art_tab, "Album Art")
        self._tabs.addTab(self._lyrics_tab, "Lyrics")
        
        details_layout.addWidget(self._tabs)
        
        splitter.addWidget(tree_container)
        splitter.addWidget(details_container)
        
        # Set stretch factors (tree takes 1, details take 2)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        
        data_layout.addWidget(splitter)
        self._stack.addWidget(data_page)
        
        layout.addWidget(self._stack)
        
    def set_processing_state(self, is_processing: bool) -> None:
        """Toggle between loading view and data view."""
        if is_processing:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        
    def populate_data(self, data_model: DriveDataModel) -> None:
        """Populate the UI using the centralized data model."""
        self._data_model = data_model
        self._tree_model.clear()
        
        root_item = self._tree_model.invisibleRootItem()
        self._build_tree(data_model.tree, root_item, data_model.root_path)
        
    def _build_tree(self, tree_dict: dict, parent_item: QStandardItem, current_path: str) -> None:
        # Sort items: folders first (sub_dict is not None), then A-Z
        sorted_items = sorted(
            tree_dict.items(), 
            key=lambda x: (x[1] is None, x[0].lower())
        )
        
        for name, sub_dict in sorted_items:
            full_path = os.path.join(current_path, name)
            item = QStandardItem(name)
            item.setEditable(False)
            # Store the full filepath in UserRole for retrieval on click
            item.setData(full_path, Qt.UserRole)
            
            if sub_dict is not None:
                # It's a directory
                icon = QApplication.style().standardIcon(QStyle.SP_DirIcon)
                item.setIcon(icon)
                self._build_tree(sub_dict, item, full_path)
            else:
                # It's a file
                icon = QApplication.style().standardIcon(QStyle.SP_FileIcon)
                item.setIcon(icon)
                
            parent_item.appendRow(item)

    def _on_selection_changed(self, selected, deselected) -> None:
        if not selected.indexes():
            self._clear_details()
            return
            
        index = selected.indexes()[0]
        item = self._tree_model.itemFromIndex(index)
        filepath = item.data(Qt.UserRole)
        
        if not self._data_model:
            return
            
        meta = self._data_model.get_track(filepath)
        if meta:
            self._populate_details(meta)
        else:
            self._clear_details()
            
    def _populate_details(self, meta) -> None:
        # Properties
        self._props_table.setRowCount(0)
        props = [
            ("File", meta.filename),
            ("Format", meta.format_name),
            ("Size", meta.display_size),
            ("Duration", meta.display_duration),
            ("Bitrate", f"{meta.bitrate_kbps} kbps" if meta.bitrate_kbps else "Unknown"),
            ("Sample Rate", f"{meta.sample_rate_hz} Hz" if meta.sample_rate_hz else "Unknown"),
            ("Channels", str(meta.channels) if meta.channels else "Unknown"),
        ]
        
        for i, (k, v) in enumerate(props):
            self._props_table.insertRow(i)
            self._props_table.setItem(i, 0, QTableWidgetItem(k))
            self._props_table.setItem(i, 1, QTableWidgetItem(str(v)))
            
        # Metadata
        self._meta_table.setRowCount(0)
        tags = [
            ("Title", meta.title),
            ("Artist", meta.artist),
            ("Album", meta.album),
            ("Genre", meta.genre),
            ("Track", str(meta.track_num)),
            ("Year", str(meta.year)),
        ]
        
        for i, (k, v) in enumerate(tags):
            self._meta_table.insertRow(i)
            self._meta_table.setItem(i, 0, QTableWidgetItem(k))
            self._meta_table.setItem(i, 1, QTableWidgetItem(str(v)))
            
        # Album Art
        if meta.has_album_art:
            self._art_lbl.setText("[Album Art Embedded - Image Decoding TODO]")
            self._art_lbl.setStyleSheet(f"color: {Colours.ACCENT}; font-weight: 700;")
        else:
            self._art_lbl.setText("No Album Art")
            self._art_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY};")
            
        # Lyrics
        if meta.has_lyrics and meta.lyrics_text:
            self._lyrics_text.setPlainText(meta.lyrics_text)
        else:
            self._lyrics_text.setPlainText("No embedded lyrics found.")
            
    def _clear_details(self) -> None:
        self._props_table.setRowCount(0)
        self._meta_table.setRowCount(0)
        self._art_lbl.setText("No Album Art")
        self._lyrics_text.setPlainText("")
