"""
Drive Information Widget.

Displays an overview of the selected root drive, including storage capacity,
file system format, and placeholder firmware status.
"""

from PySide6.QtCore import Qt, QStorageInfo
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..theme import Colours
from ..constants import MAX_TRACK_LIMIT


class DriveInfoWidget(QWidget):
    """
    Panel that shows overview stats for a mounted volume.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignTop)

        # ── Header ──────────────────────────────────────────────
        self._title_lbl = QLabel("No Drive Selected")
        self._title_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 28px; font-weight: 800; letter-spacing: -0.5px;")
        layout.addWidget(self._title_lbl)

        # ── Data Cards Container ────────────────────────────────
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(16)
        
        self._capacity_card = self._build_capacity_card()
        cards_layout.addWidget(self._capacity_card, 2)
        
        self._format_card = self._build_info_card("File System", "Unknown")
        cards_layout.addWidget(self._format_card, 1)

        self._track_limit_card = self._build_track_limit_card()
        cards_layout.addWidget(self._track_limit_card, 2)

        layout.addLayout(cards_layout)

    def _build_info_card(self, title: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("infoCard")
        card.setStyleSheet(f"""
            QFrame#infoCard {{
                background-color: {Colours.BG_SURFACE};
                border: 1px solid {Colours.BORDER_SUBTLE};
                border-radius: 0px;
            }}
        """)
        
        lyt = QVBoxLayout(card)
        lyt.setContentsMargins(16, 16, 16, 16)
        
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 11px; font-weight: 600; text-transform: uppercase;")
        lyt.addWidget(title_lbl)
        
        val_lbl = QLabel(value)
        val_lbl.setObjectName("valLbl")
        val_lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 18px; font-weight: 700;")
        lyt.addWidget(val_lbl)
        
        return card

    def _build_capacity_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("capCard")
        card.setStyleSheet(f"""
            QFrame#capCard {{
                background-color: {Colours.BG_SURFACE};
                border: 1px solid {Colours.BORDER_SUBTLE};
                border-radius: 0px;
            }}
        """)
        
        lyt = QVBoxLayout(card)
        lyt.setContentsMargins(16, 16, 16, 16)
        
        # Header row
        hdr_lyt = QHBoxLayout()
        title_lbl = QLabel("Storage Capacity")
        title_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 11px; font-weight: 600; text-transform: uppercase;")
        hdr_lyt.addWidget(title_lbl)
        
        self._cap_text = QLabel("0 GB / 0 GB")
        self._cap_text.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 13px; font-weight: 600;")
        hdr_lyt.addStretch()
        hdr_lyt.addWidget(self._cap_text)
        lyt.addLayout(hdr_lyt)
        
        # Progress bar
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colours.BG_DARKEST};
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {Colours.ACCENT};
            }}
        """)
        lyt.addWidget(self._progress)
        
        return card

    def _build_track_limit_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("trackCard")
        card.setStyleSheet(f"""
            QFrame#trackCard {{
                background-color: {Colours.BG_SURFACE};
                border: 1px solid {Colours.BORDER_SUBTLE};
                border-radius: 0px;
            }}
        """)
        
        lyt = QVBoxLayout(card)
        lyt.setContentsMargins(16, 16, 16, 16)
        
        # Header row
        hdr_lyt = QHBoxLayout()
        title_lbl = QLabel("Track Limit")
        title_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 11px; font-weight: 600; text-transform: uppercase;")
        hdr_lyt.addWidget(title_lbl)
        
        # We use a dummy value 234 for now since we haven't wired up the scanner
        current_tracks = 234
        max_tracks = MAX_TRACK_LIMIT
        
        self._track_text = QLabel(f"{current_tracks} / {max_tracks}")
        self._track_text.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 13px; font-weight: 600;")
        hdr_lyt.addStretch()
        hdr_lyt.addWidget(self._track_text)
        lyt.addLayout(hdr_lyt)
        
        # Progress bar
        self._track_progress = QProgressBar()
        self._track_progress.setTextVisible(False)
        self._track_progress.setFixedHeight(8)
        self._track_progress.setMaximum(max_tracks)
        self._track_progress.setValue(current_tracks)
        self._track_progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colours.BG_DARKEST};
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {Colours.ACCENT};
            }}
        """)
        lyt.addWidget(self._track_progress)
        
        return card

    def set_drive(self, path: str) -> None:
        """Update the UI with storage info for the given path."""
        info = QStorageInfo(path)
        
        if not info.isValid() or not info.isReady():
            self._title_lbl.setText("Drive Unavailable")
            return
            
        name = info.name() or "Local Disk"
        self._title_lbl.setText(f"{name} ({info.rootPath()})")
        
        # Update file system format
        fs_type = bytes(info.fileSystemType()).decode("utf-8", errors="ignore")
        self._format_card.findChild(QLabel, "valLbl").setText(fs_type or "Unknown")
        
        # Update capacity
        total = info.bytesTotal()
        free = info.bytesAvailable()
        used = total - free
        
        t_gb = total / (1024**3)
        u_gb = used / (1024**3)
        
        self._cap_text.setText(f"{u_gb:.1f} GB used of {t_gb:.1f} GB")
        
        if total > 0:
            pct = int((used / total) * 100)
            self._progress.setValue(pct)
        else:
            self._progress.setValue(0)
