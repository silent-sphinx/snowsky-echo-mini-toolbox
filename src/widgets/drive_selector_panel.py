"""
Drive Selector Panel.

An in-app child widget (not an OS window) that acts as a modal dialog.
It locks perfectly on top of the parent window by living inside it.
"""

import os
from PySide6.QtCore import Qt, Signal, QStorageInfo, QSize
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QStackedWidget,
    QWidget,
)

from ..theme import Colours


class DriveItemWidget(QWidget):
    """Custom widget to display rich drive info in the list."""
    def __init__(self, name: str, path: str, free_bytes: int, total_bytes: int, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)
        
        # Icon (Using a styled label for a minimal, premium look)
        icon_lbl = QLabel("🖴")
        icon_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 24px;")
        icon_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_lbl)
        
        # Text layout
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        title_lbl = QLabel(name or "Unknown Volume")
        title_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 14px; font-weight: 700;")
        text_layout.addWidget(title_lbl)
        
        # Calculate GBs
        free_gb = free_bytes / (1024**3) if free_bytes else 0
        total_gb = total_bytes / (1024**3) if total_bytes else 0
        
        if total_gb > 0:
            sub_text = f"{free_gb:.1f} GB free of {total_gb:.1f} GB  •  {path}"
        else:
            sub_text = f"Capacity Unknown  •  {path}"
            
        sub_lbl = QLabel(sub_text)
        sub_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 11px;")
        text_layout.addWidget(sub_lbl)
        
        layout.addLayout(text_layout)
        layout.addStretch()


class DriveSelectorPanel(QFrame):
    """
    In-app modal panel for selecting the target root directory.
    Emits a signal when a valid selection is confirmed.
    """
    
    location_selected = Signal(str)

    def __init__(self, parent=None, current_path: str = ""):
        super().__init__(parent)
        self.setFixedSize(500, 460)
        
        # Ensure it traps clicks and sits on top
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        self.selected_path = current_path
        self._init_ui()
        self._populate_drives()

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            DriveSelectorPanel {{
                background-color: {Colours.BG_BASE};
                border: 1px solid {Colours.BORDER_SUBTLE};
                border-radius: 4px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Header ──────────────────────────────────────────────
        title = QLabel("Select Target Location")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 22px; font-weight: 800; letter-spacing: -0.5px;")
        layout.addWidget(title)

        subtitle = QLabel("Choose a connected drive or folder to scan for metadata.")
        subtitle.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ── Stacked Widget (List vs Empty State) ────────────────
        self._stack = QStackedWidget()
        
        # Page 0: The Drive List
        self._list_widget = QListWidget()
        self._list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: {Colours.BG_DARK};
                border: 1px solid {Colours.BORDER_SUBTLE};
                border-radius: 0px;
                outline: none;
            }}
            QListWidget::item {{
                border-bottom: 1px solid {Colours.BORDER_SUBTLE};
            }}
            QListWidget::item:hover {{
                background-color: {Colours.BG_HOVER};
            }}
            QListWidget::item:selected {{
                background-color: {Colours.ACCENT_BG};
                border-left: 3px solid {Colours.ACCENT};
            }}
        """)
        self._list_widget.itemSelectionChanged.connect(self._on_list_selection_changed)
        self._list_widget.itemDoubleClicked.connect(self._on_confirm_clicked) # Double click to confirm
        self._stack.addWidget(self._list_widget)
        
        # Page 1: Empty State
        empty_widget = QWidget()
        empty_widget.setStyleSheet(f"background-color: {Colours.BG_DARK}; border: 1px solid {Colours.BORDER_SUBTLE};")
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_icon = QLabel("🔌")
        empty_icon.setStyleSheet("font-size: 48px; background: transparent; border: none;")
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_text = QLabel("No Removable Drives Detected")
        empty_text.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 16px; font-weight: 600; background: transparent; border: none;")
        empty_text.setAlignment(Qt.AlignCenter)
        empty_sub = QLabel("Please connect a USB drive or SD card to begin.")
        empty_sub.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px; background: transparent; border: none;")
        empty_sub.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_layout.addWidget(empty_text)
        empty_layout.addWidget(empty_sub)
        self._stack.addWidget(empty_widget)

        layout.addWidget(self._stack, 1)

        # ── Browse Button ───────────────────────────────────────
        browse_btn = QPushButton("Browse Custom Directory...")
        browse_btn.setMinimumHeight(36)
        browse_btn.clicked.connect(self._on_browse_clicked)
        layout.addWidget(browse_btn)

        # ── Separator ───────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {Colours.BORDER_SUBTLE}; max-height: 1px;")
        layout.addWidget(sep)

        # ── Action Buttons ──────────────────────────────────────
        btn_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("↻ Refresh List")
        refresh_btn.setMinimumHeight(34)
        refresh_btn.clicked.connect(self._populate_drives)
        btn_layout.addWidget(refresh_btn)

        btn_layout.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setMinimumHeight(34)
        self._cancel_btn.setMinimumWidth(100)
        self._cancel_btn.clicked.connect(self.hide)
        btn_layout.addWidget(self._cancel_btn)

        self._confirm_btn = QPushButton("Confirm Selection")
        self._confirm_btn.setObjectName("accentButton")
        self._confirm_btn.setMinimumHeight(34)
        self._confirm_btn.setMinimumWidth(140)
        self._confirm_btn.clicked.connect(self._on_confirm_clicked)
        self._confirm_btn.setEnabled(bool(self.selected_path))
        btn_layout.addWidget(self._confirm_btn)

        layout.addLayout(btn_layout)

    def _populate_drives(self) -> None:
        """Populate the list with actual drives using Qt's cross-platform QStorageInfo."""
        self._list_widget.clear()
        
        # If refreshing manually, keep the current selection if it still exists
        preserved_path = self.selected_path
        self.selected_path = ""
        self._confirm_btn.setEnabled(False)
        
        added_count = 0
        
        # Use Qt's native cross-platform storage info
        for volume in QStorageInfo.mountedVolumes():
            # Skip invalid, read-only system mounts, or tiny boot partitions
            if not volume.isValid() or not volume.isReady():
                continue
                
            path = volume.rootPath()
            name = volume.name()
            
            # Cross-platform heuristic to filter out root/system mounts
            if path == "/" or path == "C:\\" or name in {"Macintosh HD", "Recovery", "Preboot", "Update", "VM"}:
                continue
                
            item = QListWidgetItem()
            # Store the path in the item's UserRole so we can retrieve it cleanly
            item.setData(Qt.UserRole, path) 
            
            # Create our rich widget
            widget = DriveItemWidget(name, path, volume.bytesAvailable(), volume.bytesTotal())
            item.setSizeHint(widget.sizeHint())
            
            self._list_widget.addItem(item)
            self._list_widget.setItemWidget(item, widget)
            added_count += 1
            
            if path == preserved_path:
                item.setSelected(True)
                
        if added_count == 0:
            self._stack.setCurrentIndex(1) # Show empty state
        else:
            self._stack.setCurrentIndex(0) # Show list

    def _on_list_selection_changed(self) -> None:
        selected_items = self._list_widget.selectedItems()
        if selected_items:
            self.selected_path = selected_items[0].data(Qt.UserRole)
            self._confirm_btn.setEnabled(True)

    def _on_browse_clicked(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Target Directory",
            self.selected_path or "/Users/legion",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        
        if directory:
            self.selected_path = directory
            
            # Switch to list view if it was empty
            self._stack.setCurrentIndex(0)
            
            # Check if it already exists in the list
            found = False
            for i in range(self._list_widget.count()):
                item = self._list_widget.item(i)
                if item.data(Qt.UserRole) == directory:
                    item.setSelected(True)
                    found = True
                    break
                    
            if not found:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, directory)
                
                # Fetch basic size info if possible
                info = QStorageInfo(directory)
                name = info.name() or os.path.basename(directory) or directory
                
                widget = DriveItemWidget(name, directory, info.bytesAvailable(), info.bytesTotal())
                item.setSizeHint(widget.sizeHint())
                
                self._list_widget.insertItem(0, item)
                self._list_widget.setItemWidget(item, widget)
                item.setSelected(True)

    def _on_confirm_clicked(self) -> None:
        if self.selected_path:
            self.location_selected.emit(self.selected_path)
