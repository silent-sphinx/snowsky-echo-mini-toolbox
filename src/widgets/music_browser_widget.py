import os
import subprocess
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel, QStandardItem, QColor, QBrush, QPixmap, QImage
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
    QStyle,
    QStyledItemDelegate,
    QPushButton,
    QMessageBox,
    QLineEdit,
    QMenu,
    QDialog,
    QInputDialog
)
from PySide6.QtGui import QBrush

from ..theme import Colours
from ..models.drive_data import DriveDataModel, TrackMetadata
from ..utils.metadata_writer import save_metadata
from ..utils.album_art import extract_album_art


class HighlightDelegate(QStyledItemDelegate):
    """Custom delegate to enforce background colors over QSS/Alternating rows."""
    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if bg:
            painter.fillRect(option.rect, bg)
            # Prevent base class from trying to draw the background again
            option.backgroundBrush = QBrush(Qt.NoBrush)
        super().paint(painter, option, index)


class FfprobeDialog(QDialog):
    def __init__(self, filepath: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Debug: {os.path.basename(filepath)}")
        self.resize(700, 500)
        
        layout = QVBoxLayout(self)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {Colours.BG_DARKEST};
                color: {Colours.TEXT_PRIMARY};
                font-family: monospace;
                font-size: 12px;
                border: 1px solid {Colours.BORDER_STRONG};
            }}
        """)
        layout.addWidget(self.text_edit)
        
        try:
            result = subprocess.run(
                ["ffprobe", "-hide_banner", "-v", "verbose", "-show_format", "-show_streams", filepath],
                capture_output=True, text=True, check=False
            )
            output = result.stdout + "\n" + result.stderr
            self.text_edit.setPlainText(output.strip())
        except Exception as e:
            self.text_edit.setPlainText(f"Failed to run ffprobe:\n{e}\n\nPlease ensure ffprobe is installed and accessible.")


class StreamsDialog(QDialog):
    def __init__(self, filepath: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Streams: {os.path.basename(filepath)}")
        self.resize(800, 400)
        
        layout = QVBoxLayout(self)
        
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        layout.addWidget(self.table)
        
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", filepath],
                capture_output=True, text=True, check=False
            )
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            if not audio_streams:
                audio_streams = streams
                
            if not audio_streams:
                self.table.setColumnCount(1)
                self.table.setRowCount(1)
                self.table.setItem(0, 0, QTableWidgetItem("No streams found or failed to parse."))
                return
                
            columns = []
            for stream in audio_streams:
                for k in stream.keys():
                    if k not in columns and not isinstance(stream[k], dict):
                        columns.append(k)
                        
            priority_cols = ["index", "codec_type", "codec_name", "profile", "sample_rate", "channels", "bit_rate"]
            ordered_cols = [c for c in priority_cols if c in columns]
            ordered_cols += [c for c in columns if c not in priority_cols]
            
            self.table.setColumnCount(len(ordered_cols))
            self.table.setHorizontalHeaderLabels(ordered_cols)
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(audio_streams))
            
            for row_idx, stream in enumerate(audio_streams):
                for col_idx, col_name in enumerate(ordered_cols):
                    val = str(stream.get(col_name, ""))
                    self.table.setItem(row_idx, col_idx, QTableWidgetItem(val))
                    
            self.table.setSortingEnabled(True)
            self.table.resizeColumnsToContents()
            
        except Exception as e:
            self.table.setColumnCount(1)
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(f"Error loading streams:\n{e}"))


class MusicBrowserWidget(QWidget):
    """
    Displays the directory tree of the drive and detailed metadata for the selected file.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_model: DriveDataModel = None
        self._is_updating_checks = False
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
        self._tree.setSelectionMode(QTreeView.ExtendedSelection)
        
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        
        self._tree_model = QStandardItemModel()
        self._tree.setModel(self._tree_model)
        self._tree.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._tree_model.itemChanged.connect(self._on_item_changed)
        
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
        self._props_table.setSortingEnabled(True)
        self._props_table.horizontalHeader().setStretchLastSection(True)
        self._props_table.horizontalHeader().setSectionsClickable(True)
        self._props_table.horizontalHeader().setSortIndicatorShown(True)
        self._props_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        
        # Prevent squeezing smaller than the title text
        fm = self._props_table.horizontalHeader().fontMetrics()
        min_width = fm.horizontalAdvance("PROPERTY") + 40 # 40px padding buffer
        self._props_table.horizontalHeader().setMinimumSectionSize(min_width)
        
        props_lyt.addWidget(self._props_table)
        
        # 2. Music Metadata Tab
        self._meta_tab = QWidget()
        meta_lyt = QVBoxLayout(self._meta_tab)
        meta_lyt.setContentsMargins(8, 8, 8, 8)
        
        self._meta_table = QTableWidget(0, 2)
        self._meta_table.setHorizontalHeaderLabels(["Tag", "Value"])
        self._meta_table.verticalHeader().setVisible(False)
        self._meta_table.setAlternatingRowColors(True)
        # Edit triggers are enabled by default, we'll restrict column 0 in code
        self._meta_table.horizontalHeader().setStretchLastSection(True)
        self._meta_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        
        fm_meta = self._meta_table.horizontalHeader().fontMetrics()
        min_width_meta = fm_meta.horizontalAdvance("VALUE") + 40
        self._meta_table.horizontalHeader().setMinimumSectionSize(min_width_meta)
        
        self._meta_delegate = HighlightDelegate(self._meta_table)
        self._meta_table.setItemDelegate(self._meta_delegate)
        
        meta_lyt.addWidget(self._meta_table)
        
        meta_btns_lyt = QHBoxLayout()
        self._meta_discard_btn = QPushButton("Discard Changes")
        self._meta_discard_btn.clicked.connect(self._on_discard_metadata_clicked)
        meta_btns_lyt.addWidget(self._meta_discard_btn)
        
        self._meta_save_btn = QPushButton("Save Changes")
        self._meta_save_btn.setObjectName("accentButton")
        self._meta_save_btn.clicked.connect(self._on_save_metadata_clicked)
        meta_btns_lyt.addWidget(self._meta_save_btn)
        
        meta_lyt.addLayout(meta_btns_lyt)
        
        # 3. Album Art Tab
        self._art_tab = QWidget()
        art_lyt = QVBoxLayout(self._art_tab)
        art_lyt.setContentsMargins(16, 16, 16, 16)
        
        self._art_image_lbl = QLabel("No Album Art")
        self._art_image_lbl.setAlignment(Qt.AlignCenter)
        self._art_image_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY};")
        self._art_image_lbl.setMinimumHeight(250)
        
        self._art_table = QTableWidget(0, 2)
        self._art_table.setHorizontalHeaderLabels(["Property", "Value"])
        self._art_table.verticalHeader().setVisible(False)
        self._art_table.setAlternatingRowColors(True)
        self._art_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._art_table.setSortingEnabled(True)
        self._art_table.horizontalHeader().setStretchLastSection(True)
        self._art_table.horizontalHeader().setSectionsClickable(True)
        self._art_table.horizontalHeader().setSortIndicatorShown(True)
        self._art_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        
        art_lyt.addWidget(self._art_image_lbl, stretch=1)
        art_lyt.addWidget(self._art_table, stretch=0)
        
        # 3. Lyrics Tab
        self._lyrics_tab = QWidget()
        lyrics_lyt = QVBoxLayout(self._lyrics_tab)
        lyrics_lyt.setContentsMargins(16, 16, 16, 16)
        lyrics_lyt.setSpacing(16)
        
        # Warning Block
        self._lyrics_warning_block = QWidget()
        self._lyrics_warning_block.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(230, 124, 0, 0.1);
                border: 1px solid {Colours.STATUS_LIMITED};
                border-radius: 0px;
            }}
        """)
        warning_lyt = QVBoxLayout(self._lyrics_warning_block)
        warning_lyt.setContentsMargins(16, 12, 16, 12)
        
        self._lyrics_disclaimer = QLabel("Embedded lyrics are unsupported by the Snowsky Echo Mini.\nAn identically named .lrc file must exist in the same folder to display lyrics on the device. Convert embedded lyrics in the Lyrics Manager tab.")
        self._lyrics_disclaimer.setStyleSheet(f"color: {Colours.STATUS_LIMITED_TEXT}; border: none; background: transparent; font-weight: 500; font-size: 13px;")
        self._lyrics_disclaimer.setWordWrap(True)
        warning_lyt.addWidget(self._lyrics_disclaimer)
        lyrics_lyt.addWidget(self._lyrics_warning_block)
        
        # Lyrics visual area
        self._lyrics_text = QPlainTextEdit()
        self._lyrics_text.setReadOnly(True)
        self._lyrics_text.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {Colours.BG_DARKEST};
                border: 1px solid {Colours.BORDER_STRONG};
                border-radius: 0px;
                padding: 12px;
                font-size: 13px;
                line-height: 1.5;
            }}
        """)
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
        
    def _on_context_menu(self, position) -> None:
        index = self._tree.indexAt(position)
        if not index.isValid():
            return
            
        item = self._tree_model.itemFromIndex(index)
        filepath = item.data(Qt.UserRole)
        
        if not filepath or not os.path.isfile(filepath):
            return
            
        menu = QMenu()
        
        is_music_file = not filepath.lower().endswith(".lrc")
        
        debug_action = None
        streams_action = None
        
        if is_music_file:
            debug_action = menu.addAction("Debug")
            streams_action = menu.addAction("View Streams")
            menu.addSeparator()
            
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        
        action = menu.exec(self._tree.viewport().mapToGlobal(position))
        
        if action is None:
            return
            
        if debug_action and action == debug_action:
            dialog = FfprobeDialog(filepath, self)
            dialog.exec()
        elif streams_action and action == streams_action:
            dialog = StreamsDialog(filepath, self)
            dialog.exec()
        elif action == rename_action:
            old_name = os.path.basename(filepath)
            new_name, ok = QInputDialog.getText(self, "Rename", "New file name:", QLineEdit.Normal, old_name)
            if ok and new_name and new_name != old_name:
                dir_path = os.path.dirname(filepath)
                new_filepath = os.path.join(dir_path, new_name)
                try:
                    os.rename(filepath, new_filepath)
                    item.setText(new_name)
                    item.setData(new_filepath, Qt.UserRole)
                    if self._data_model and filepath in self._data_model.tracks:
                        track = self._data_model.tracks.pop(filepath)
                        track.filepath = new_filepath
                        track.filename = new_name
                        self._data_model.tracks[new_filepath] = track
                    if self._tree.selectionModel().isSelected(index):
                        self._populate_details(self._data_model.get_track(new_filepath))
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to rename file:\n{e}")
        elif action == delete_action:
            reply = QMessageBox.question(
                self, "Delete", 
                f"Are you sure you want to delete '{os.path.basename(filepath)}'?\nThis cannot be undone.", 
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                try:
                    os.remove(filepath)
                    parent = item.parent()
                    if parent:
                        parent.removeRow(item.row())
                    else:
                        self._tree_model.invisibleRootItem().removeRow(item.row())
                        
                    if self._data_model and filepath in self._data_model.tracks:
                        del self._data_model.tracks[filepath]
                    self._clear_details()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to delete file:\n{e}")
        
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
                item.setCheckable(True)
                self._build_tree(sub_dict, item, full_path)
            else:
                # It's a file
                item.setCheckable(True)
            
            # Store the full filepath in UserRole for retrieval on click
            item.setData(full_path, Qt.UserRole)
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
            
    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._is_updating_checks or not item.isCheckable():
            return
            
        self._is_updating_checks = True
        try:
            state = item.checkState()
            self._set_check_state_recursive(item, state)
        finally:
            self._is_updating_checks = False
            
    def _set_check_state_recursive(self, item: QStandardItem, state: Qt.CheckState) -> None:
        for row in range(item.rowCount()):
            child = item.child(row)
            if child and child.isCheckable():
                child.setCheckState(state)
                self._set_check_state_recursive(child, state)
                
    def _populate_details(self, meta: TrackMetadata) -> None:
        self._clear_details()
        
        is_lrc = meta.filepath.lower().endswith(".lrc")
        
        # Toggle tab visibility
        self._tabs.setTabVisible(0, not is_lrc) # Properties
        self._tabs.setTabVisible(1, not is_lrc) # Music Metadata
        self._tabs.setTabVisible(2, not is_lrc) # Album Art
        self._tabs.setTabVisible(3, True)       # Lyrics (either file contents or disclaimer)
        
        if is_lrc:
            self._tabs.setCurrentIndex(3)
            self._lyrics_warning_block.hide()
            try:
                with open(meta.filepath, 'r', encoding='utf-8') as f:
                    self._lyrics_text.setPlainText(f.read())
            except Exception as e:
                self._lyrics_text.setPlainText(f"Failed to read LRC file: {str(e)}")
            return
            
        self._lyrics_warning_block.show()
        
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
        
        if meta.all_tags:
            tags = list(meta.all_tags.items())
            # Sort tags alphabetically by key for cleaner display
            tags.sort(key=lambda x: x[0])
        else:
            # Fallback to standard attributes if mutagen extraction completely failed
            tags = [
                ("Title", meta.title),
                ("Artist", meta.artist),
                ("Album", meta.album),
                ("Genre", meta.genre),
                ("Track", str(meta.track_num)),
                ("Year", str(meta.year)),
            ]
        
        # Tags known to be parsed by the Snowsky Echo Mini firmware
        recognized_tags = {
            "title", "artist", "album", "albumartist", "album artist",
            "tracknumber", "track", "discnumber", "genre", "tit2", "tpe1", "talb", "tpe2", "trck", "tpos", "tcon"
        }
            
        for i, (k, v) in enumerate(tags):
            self._meta_table.insertRow(i)
            
            # Key column: Persistent textbox
            key_edit = QLineEdit(k)
            key_edit.setStyleSheet("QLineEdit { border: none; background: transparent; padding: 4px 8px; }")
            self._meta_table.setCellWidget(i, 0, key_edit)
            
            # Dummy items for background painting
            key_bg_item = QTableWidgetItem()
            val_bg_item = QTableWidgetItem()
            
            # Value column: Persistent textbox
            val_edit = QLineEdit(str(v))
            val_edit.setStyleSheet("QLineEdit { border: none; background: transparent; padding: 4px 8px; }")
            self._meta_table.setCellWidget(i, 1, val_edit)
            
            # Highlight recognized tags
            if k.lower() in recognized_tags:
                green_brush = QBrush(QColor(40, 80, 40)) # Subtle dark green
                key_bg_item.setBackground(green_brush)
                val_bg_item.setBackground(green_brush)
                
                tooltip = "Tag Recognised by Snowsky"
                key_edit.setToolTip(tooltip)
                val_edit.setToolTip(tooltip)
                
            self._meta_table.setItem(i, 0, key_bg_item)
            self._meta_table.setItem(i, 1, val_bg_item)
            
        # Ensure rows are tall enough to display the QLineEdits
        self._meta_table.resizeRowsToContents()
            
        # Album Art
        self._art_table.setRowCount(0)
        if meta.has_album_art:
            art_info = extract_album_art(meta.filepath)
            if art_info:
                # Load Pixmap
                img = QImage.fromData(art_info.image_data)
                pixmap = QPixmap.fromImage(img)
                scaled_pixmap = pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._art_image_lbl.setPixmap(scaled_pixmap)
                
                # Setup table
                scan_type = "Progressive" if art_info.is_progressive else "Baseline (Non-progressive)"
                if "jpeg" not in art_info.mime_type.lower() and "jpg" not in art_info.mime_type.lower():
                    scan_type = "N/A"
                    
                display_size = f"{art_info.size_bytes / 1024:.1f} KB" if art_info.size_bytes < 1024 * 1024 else f"{art_info.size_bytes / (1024 * 1024):.1f} MB"
                    
                art_props = [
                    ("MIME Type", art_info.mime_type),
                    ("Dimensions", f"{art_info.width} x {art_info.height} px"),
                    ("Data Size", display_size),
                    ("JPEG Scan", scan_type),
                ]
                
                for i, (k, v) in enumerate(art_props):
                    self._art_table.insertRow(i)
                    self._art_table.setItem(i, 0, QTableWidgetItem(k))
                    self._art_table.setItem(i, 1, QTableWidgetItem(v))
            else:
                self._art_image_lbl.setText("[Failed to extract album art]")
        else:
            self._art_image_lbl.setPixmap(QPixmap())
            self._art_image_lbl.setText("No Album Art embedded in this file.")
            
        # Lyrics
        if meta.has_lyrics and meta.lyrics_text:
            self._lyrics_text.setPlainText(meta.lyrics_text)
        else:
            self._lyrics_text.setPlainText("No embedded lyrics found.")
            
    def _clear_details(self) -> None:
        self._props_table.setRowCount(0)
        self._meta_table.setRowCount(0)
        self._art_table.setRowCount(0)
        self._art_image_lbl.setPixmap(QPixmap())
        self._art_image_lbl.setText("No Album Art")
        self._lyrics_text.setPlainText("")
        
        # Hide all tabs when a folder or nothing is selected
        for i in range(self._tabs.count()):
            self._tabs.setTabVisible(i, False)
        
    def _on_discard_metadata_clicked(self) -> None:
        # Reload the metadata for the current selection from the cache
        indexes = self._tree.selectionModel().selectedIndexes()
        if indexes:
            # Trigger standard selection changed logic to rebuild the details panel
            self._on_selection_changed(self._tree.selectionModel().selection(), None)
            
    def _on_save_metadata_clicked(self) -> None:
        if not self._data_model:
            return
            
        indexes = self._tree.selectionModel().selectedIndexes()
        if not indexes:
            return
            
        index = indexes[0]
        item = self._tree_model.itemFromIndex(index)
        filepath = item.data(Qt.UserRole)
        
        # Gather tags from table
        new_tags = {}
        for row in range(self._meta_table.rowCount()):
            key_widget = self._meta_table.cellWidget(row, 0)
            val_widget = self._meta_table.cellWidget(row, 1)
            
            if key_widget and val_widget:
                k = key_widget.text().strip()
                if k:
                    new_tags[k] = val_widget.text()
                
        self._meta_save_btn.setText("Saving...")
        QApplication.processEvents()
        
        success, msg = save_metadata(filepath, new_tags)
        self._meta_save_btn.setText("Save Changes")
        
        if success:
            self._data_model.update_metadata(filepath, new_tags)
            QMessageBox.information(self, "Success", "Metadata saved successfully.")
        else:
            QMessageBox.critical(self, "Error", f"Failed to save metadata:\n{msg}")
