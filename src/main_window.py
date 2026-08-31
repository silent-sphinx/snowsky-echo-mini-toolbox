"""
Main application window shell.

Provides a minimal tabbed interface that houses the Metadata Manager
and serves as the foundation for the full app rewrite.
"""

from PySide6.QtCore import Qt, QTimer, QStorageInfo
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QProgressBar,
)

from .widgets.metadata_manager import MetadataManager
from .widgets.drive_info_widget import DriveInfoWidget
from .widgets.drive_selector_panel import DriveSelectorPanel
from .widgets.music_browser_widget import MusicBrowserWidget
from .threads.drive_scanner import DriveScannerThread
from .theme import Colours


class MainWindow(QMainWindow):
    """Main application shell."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Snowsky Echo Mini Toolbox")
        self.setMinimumSize(1024, 768)
        self.resize(1280, 800)

        self._current_drive = ""
        self._initial_dialog_shown = False
        self._init_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initial_dialog_shown:
            self._initial_dialog_shown = True
            # Ensure the window is fully visible and positioned before spawning modal
            QTimer.singleShot(100, self._show_drive_selector)

    def _init_ui(self) -> None:
        # Central widget and main layout
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Top Bar ─────────────────────────────────────────────
        top_bar = self._build_top_bar()
        main_layout.addWidget(top_bar)

        # ── Tab Widget ──────────────────────────────────────────
        tab_container = QWidget()
        tab_layout = QVBoxLayout(tab_container)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(0)

        # ── Tabs ────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)  # cleaner look on macOS/Windows
        
        # Index 0: Drive Information
        self._drive_info = DriveInfoWidget()
        self._tabs.addTab(self._drive_info, "Drive Information")
        
        # Index 1: Metadata Browser
        self._metadata_manager = MetadataManager()
        self._tabs.addTab(self._metadata_manager, "Metadata Browser")
        
        # Index 2: Music Browser
        self._music_browser = MusicBrowserWidget()
        self._tabs.addTab(self._music_browser, "Music Browser")
        
        # Add placeholders for future tabs
        self._tabs.addTab(QWidget(), "Album Art")
        self._tabs.addTab(QWidget(), "Music Compatibility")
        self._tabs.addTab(QWidget(), "Lyrics Manager")
        
        tab_layout.addWidget(self._tabs)
        main_layout.addWidget(tab_container)

        # ── Overlay ─────────────────────────────────────────────
        self._overlay = QWidget(central)
        self._overlay.setStyleSheet("background-color: rgba(20, 20, 20, 180);")
        self._overlay.hide()
        
        # ── In-App Modal Panel ──────────────────────────────────
        self._drive_panel = DriveSelectorPanel(central, current_path=self._current_drive)
        self._drive_panel.location_selected.connect(self._on_location_selected)
        self._drive_panel.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, '_overlay'):
            self._overlay.resize(self.centralWidget().size())
        if hasattr(self, '_drive_panel'):
            self._drive_panel.move(
                self.centralWidget().width() // 2 - self._drive_panel.width() // 2,
                self.centralWidget().height() // 2 - self._drive_panel.height() // 2
            )

    def _build_top_bar(self) -> QWidget:
        """Build the top application bar with title and drive selector placeholder."""
        bar = QWidget()
        bar.setStyleSheet(f"background-color: {Colours.BG_DARKEST}; border-bottom: 1px solid {Colours.BORDER_SUBTLE};")
        bar.setFixedHeight(50)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        # Title
        title = QLabel("Snowsky Echo Mini Toolbox")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 18px; font-weight: 800; letter-spacing: -0.5px;")
        layout.addWidget(title)
        
        # Spacer to push progress to center
        layout.addStretch()

        # ── Global Progress Container (Centered) ────────────────
        self._prog_container = QWidget()
        prog_lyt = QVBoxLayout(self._prog_container)
        prog_lyt.setContentsMargins(0, 0, 0, 0)
        prog_lyt.setSpacing(6)
        prog_lyt.setAlignment(Qt.AlignCenter)
        
        self._prog_status_lbl = QLabel("Processing data...")
        self._prog_status_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;")
        self._prog_status_lbl.setAlignment(Qt.AlignCenter)
        prog_lyt.addWidget(self._prog_status_lbl)

        self._global_progress = QProgressBar()
        self._global_progress.setTextVisible(False)
        self._global_progress.setRange(0, 0) # Indeterminate pulsing
        self._global_progress.setFixedWidth(200)
        self._global_progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colours.BG_DARKEST};
                border: none;
                min-height: 2px;
                max-height: 2px;
            }}
            QProgressBar::chunk {{
                background-color: #FFFFFF;
            }}
        """)
        prog_lyt.addWidget(self._global_progress)
        
        self._prog_container.hide()
        layout.addWidget(self._prog_container)

        # Spacer to push button to right
        layout.addStretch()

        # Mock Drive Selector
        drive_lbl = QLabel("Target:")
        drive_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px; font-weight: 600;")
        layout.addWidget(drive_lbl)
        
        self._drive_btn = QPushButton("Select Target Drive...")
        self._drive_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colours.BG_SURFACE};
                border: 1px solid {Colours.BORDER_DEFAULT};
                border-radius: 0px;
                color: {Colours.TEXT_PRIMARY};
                padding: 6px 16px;
                font-weight: 500;
                font-size: 13px;
            }}
            QPushButton:hover {{
                border-color: {Colours.ACCENT};
            }}
        """)
        self._drive_btn.clicked.connect(self._show_drive_selector)
        layout.addWidget(self._drive_btn)

        return bar

    def _show_drive_selector(self) -> None:
        """Show the modal drive selector panel."""
        # Hide the tabs entirely while this menu is active
        self._tabs.hide()
        
        if hasattr(self, '_overlay'):
            self._overlay.resize(self.centralWidget().size())
            self._overlay.show()
            
        # Center the panel before showing
        self._drive_panel.move(
            self.centralWidget().width() // 2 - self._drive_panel.width() // 2,
            self.centralWidget().height() // 2 - self._drive_panel.height() // 2
        )
        
        # Disable and HIDE cancel button if no drive has been selected yet (force choice)
        can_cancel = bool(self._current_drive)
        self._drive_panel._cancel_btn.setEnabled(can_cancel)
        self._drive_panel._cancel_btn.setVisible(can_cancel)
        
        # We also need to hide the overlay and show tabs if they manage to cancel
        self._drive_panel._cancel_btn.clicked.connect(self._overlay.hide, Qt.UniqueConnection)
        self._drive_panel._cancel_btn.clicked.connect(self._tabs.show, Qt.UniqueConnection)
        
        self._drive_panel.show()
        self._drive_panel.raise_()

    def _on_location_selected(self, path: str) -> None:
        """Handle a valid location selection from the in-app panel."""
        self._tabs.show()
        self._current_drive = path
        self._drive_btn.setText(self._current_drive)
        self._drive_panel.hide()
        if hasattr(self, '_overlay'):
            self._overlay.hide()
            
        # Determine if it's a root drive or a directory
        is_drive = False
        for vol in QStorageInfo.mountedVolumes():
            if vol.rootPath() == path:
                is_drive = True
                break
                
        if is_drive:
            self._tabs.setTabVisible(0, True)
            self._drive_info.set_drive(path)
            self._tabs.setCurrentIndex(0)
        else:
            self._tabs.setTabVisible(0, False)
            self._tabs.setCurrentIndex(1)
            
        # Set global processing state
        self._set_processing_state(True, "Initializing scan...")
        
        # Cancel any existing scan
        if hasattr(self, '_scanner_thread') and self._scanner_thread.isRunning():
            self._scanner_thread.cancel()
            self._scanner_thread.wait()
            
        # Start the global data scanner
        self._scanner_thread = DriveScannerThread(path, self)
        self._scanner_thread.progress_updated.connect(self._on_scan_progress)
        self._scanner_thread.scan_finished.connect(self._on_scan_finished)
        self._scanner_thread.start()

    def _on_scan_progress(self, current: int, total: int, filepath: str) -> None:
        """Update global progress bar during deep scan."""
        if total > 0:
            self._global_progress.setMaximum(total)
            self._global_progress.setValue(current)
            self._prog_status_lbl.setText(f"Scanning media: {current} / {total}")
            
    def _on_scan_finished(self, data_model) -> None:
        """Handle completion of the global drive scan."""
        self._set_processing_state(False)
        
        # Pass the unified data model to the child tabs
        self._music_browser.populate_data(data_model)
        
        # Update the DriveInfoWidget track count
        if self._tabs.isTabVisible(0):
            self._drive_info._on_track_count_finished(len(data_model.tracks))

    def _set_processing_state(self, is_processing: bool, status_text: str = "Processing data...") -> None:
        """Toggle the global loading state and UI indicators."""
        if is_processing:
            self._prog_status_lbl.setText(status_text)
            self._prog_container.show()
        else:
            self._prog_container.hide()
            # Reset progress bar for next time
            self._global_progress.setRange(0, 0)
            
        # Notify child tabs that need to show empty/loading states
        self._metadata_manager.set_processing_state(is_processing)
        self._music_browser.set_processing_state(is_processing)

