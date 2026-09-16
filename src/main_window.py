"""
Main application window shell.

Provides a minimal tabbed interface that houses the Metadata Manager
and serves as the foundation for the full app rewrite.
"""

from PySide6.QtCore import Qt, QEventLoop, QTimer, QStorageInfo
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QSizePolicy
)
import os

from .widgets.metadata_manager import MetadataManager
from .widgets.album_art_widget import AlbumArtWidget
from .widgets.drive_info_widget import DriveInfoWidget
from .widgets.drive_selector_panel import DriveSelectorPanel
from .widgets.lyrics_manager import LyricsManagerWidget
from .widgets.backup_restore_widget import BackupRestoreWidget
from .widgets.file_rename_widget import FileRenameWidget
from .widgets.file_cleanup_widget import FileCleanupWidget
from .widgets.workflow_widget import WorkflowWidget
from .widgets.music_browser_widget import MusicBrowserWidget
from .widgets.music_compatibility_widget import MusicCompatibilityWidget
from .threads.drive_scanner import DriveScannerThread
from .theme import Colours
from .constants import APP_VERSION
from .utils.volume import eject_volume, removable_volume_for_path


class MainWindow(QMainWindow):
    """Main application shell."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Snowsky Echo Mini Toolbox")
        self.setMinimumSize(1024, 768)
        self.resize(1280, 800)

        self._current_drive = ""
        self._initial_dialog_shown = False
        self._populate_queue: list[tuple[str, QWidget, object]] = []
        self._populate_generation = 0
        self._populate_total = 0
        self._populate_done = 0
        self._populate_reveal = False
        self._populate_status_prefix = "Loading tables"
        self._init_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initial_dialog_shown:
            self._initial_dialog_shown = True
            handle = self.windowHandle()
            if handle is not None:
                # Read the live DPR after the native window exists so Qt's
                # cached value matches the first expose (QTBUG-118794).
                handle.devicePixelRatio()
            # Wait until the first expose has finished before showing the overlay.
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
        
        # Index 0: Drive Information
        self._drive_info = DriveInfoWidget()
        self._tabs.addTab(self._drive_info, "Drive Information")
        
        # Index 1: File Browser
        self._music_browser = MusicBrowserWidget()
        self._music_browser.library_changed.connect(self._on_library_changed)
        self._tabs.addTab(self._music_browser, "File Browser")

        # Index 2: Music Compatibility
        self._music_compatibility = MusicCompatibilityWidget()
        self._tabs.addTab(self._music_compatibility, "Music Compatibility")
        
        # Index 3: Metadata Browser
        self._metadata_manager = MetadataManager()
        self._tabs.addTab(self._metadata_manager, "Metadata Browser")
        
        # Index 4: Album Art Manager
        self._album_art = AlbumArtWidget()
        self._tabs.addTab(self._album_art, "Album Art Manager")
        
        # Index 5: Lyrics Manager
        self._lyrics_manager = LyricsManagerWidget()
        self._tabs.addTab(self._lyrics_manager, "Lyrics Manager")

        # Index 6: File Rename
        self._file_rename = FileRenameWidget()
        self._file_rename.library_changed.connect(self._on_library_changed)
        self._tabs.addTab(self._file_rename, "File Rename")

        # Index 7: File Cleanup
        self._file_cleanup = FileCleanupWidget()
        self._file_cleanup.library_changed.connect(self._on_library_changed)
        self._tabs.addTab(self._file_cleanup, "File Cleanup")

        # Index 8: Backup / Restore
        self._backup_restore = BackupRestoreWidget()
        self._backup_restore.target_relocated.connect(self._on_location_selected)
        self._tabs.addTab(self._backup_restore, "Backup / Restore")

        # Index 9: Workflows
        self._workflows = WorkflowWidget()
        self._workflows.needs_rescan.connect(self._rescan_current_target)
        self._tabs.addTab(self._workflows, "Workflows")
        self._tabs.currentChanged.connect(self._on_library_tab_changed)
        
        tab_layout.addWidget(self._tabs)
        main_layout.addWidget(tab_container)

        # ── Overlay ─────────────────────────────────────────────
        self._overlay = QWidget(central)
        self._overlay.setStyleSheet("background-color: rgba(20, 20, 20, 180);")
        self._overlay.hide()
        
        # ── In-App Modal Panel ──────────────────────────────────
        self._drive_panel = DriveSelectorPanel(central, current_path=self._current_drive)
        self._drive_panel.location_selected.connect(self._on_location_selected)
        self._drive_panel.drive_ejected.connect(self._on_drive_ejected)
        self._drive_panel.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Hidden overlay/panel geometry updates during the first expose race
        # Qt's cached devicePixelRatio on Retina displays (QTBUG-118794).
        if hasattr(self, '_overlay') and self._overlay.isVisible():
            self._overlay.resize(self.centralWidget().size())
        if hasattr(self, '_drive_panel') and self._drive_panel.isVisible():
            self._drive_panel.move(
                self.centralWidget().width() // 2 - self._drive_panel.width() // 2,
                self.centralWidget().height() // 2 - self._drive_panel.height() // 2
            )
            
    def closeEvent(self, event) -> None:
        self._cancel_library_populate()
        for widget in (
            getattr(self, "_workflows", None),
            getattr(self, "_file_cleanup", None),
            getattr(self, "_music_compatibility", None),
            getattr(self, "_music_browser", None),
            getattr(self, "_metadata_manager", None),
            getattr(self, "_album_art", None),
            getattr(self, "_lyrics_manager", None),
            getattr(self, "_file_rename", None),
            getattr(self, "_backup_restore", None),
        ):
            if widget is not None and hasattr(widget, "cancel_running_job"):
                widget.cancel_running_job()
        if hasattr(self, '_scanner_thread') and self._scanner_thread.isRunning():
            self._scanner_thread.cancel()
            self._scanner_thread.wait()

        volume = removable_volume_for_path(self._current_drive)
        if volume:
            root, name, device = volume
            reply = QMessageBox.question(
                self,
                "Eject Drive",
                f'Would you like to eject “{name}” before quitting?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                ok, error = eject_volume(root, device)
                if not ok:
                    QMessageBox.warning(
                        self,
                        "Eject Failed",
                        error or "The drive could not be unmounted.",
                    )

        super().closeEvent(event)

    def _build_top_bar(self) -> QWidget:
        """Build the top application bar with title and drive selector placeholder."""
        bar = QWidget()
        bar.setStyleSheet(f"background-color: {Colours.BG_DARKEST};")
        bar.setFixedHeight(50)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        # Title and Version
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        title_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        title = QLabel("Snowsky Echo Mini Toolbox")
        title.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 18px; font-weight: 800; letter-spacing: -0.5px;")
        
        version_lbl = QLabel(f"{APP_VERSION}")
        version_lbl.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 11px; font-weight: 600;")
        version_lbl.setAlignment(Qt.AlignBottom)
        
        title_layout.addWidget(title)
        title_layout.addWidget(version_lbl)
        
        layout.addWidget(title_container)
        
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
        
        # Fix layout jitter by using fixed container size
        self._global_progress.setFixedWidth(400)
        self._prog_container.setFixedWidth(500)
        
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

        # Spacer to center the progress container and push the drive selector to the right
        layout.addStretch()

        target_container = QWidget()
        target_layout = QHBoxLayout(target_container)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(8)

        drive_lbl = QLabel("Target:")
        drive_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px; font-weight: 600;")
        target_layout.addWidget(drive_lbl)

        top_bar_btn_style = f"""
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
            QPushButton:disabled {{
                color: {Colours.TEXT_DISABLED};
                border-color: {Colours.BORDER_SUBTLE};
            }}
        """

        self._drive_btn = QPushButton("Select Target Drive...")
        self._drive_btn.setStyleSheet(top_bar_btn_style)
        self._drive_btn.clicked.connect(self._show_drive_selector)
        target_layout.addWidget(self._drive_btn)

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setToolTip("Rescan the current target library")
        self._refresh_btn.setStyleSheet(top_bar_btn_style)
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.clicked.connect(self._rescan_current_target)
        target_layout.addWidget(self._refresh_btn)

        layout.addWidget(target_container)

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

    def _path_is_on_volume(self, path: str, volume_root: str) -> bool:
        if not path or not volume_root:
            return False
        current = os.path.normcase(os.path.normpath(path))
        root = os.path.normcase(os.path.normpath(volume_root))
        if current == root:
            return True
        return current.startswith(root.rstrip(os.sep) + os.sep)

    def _on_drive_ejected(self, path: str) -> None:
        """Clear the active target if the ejected volume was in use."""
        if not self._path_is_on_volume(self._current_drive, path):
            return

        if hasattr(self, '_scanner_thread') and self._scanner_thread.isRunning():
            self._scanner_thread.cancel()
            self._scanner_thread.wait()

        self._cancel_library_populate()
        self._current_drive = ""
        self._drive_btn.setText("Select Target Drive...")
        self._set_processing_state(False)
        self._backup_restore.set_target("")
        self._workflows.set_target("")
        self._drive_panel._cancel_btn.setEnabled(False)
        self._drive_panel._cancel_btn.setVisible(False)

    def _on_location_selected(self, path: str) -> None:
        """Handle a valid location selection from the in-app panel."""
        self._tabs.show()
        self._current_drive = path
        self._drive_btn.setText(self._current_drive)
        self._backup_restore.set_target(path)
        self._workflows.set_target(path)
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

        self._start_library_scan(path)

    def _rescan_current_target(self) -> None:
        """Rescan the current target without changing the active tab."""
        if not self._current_drive:
            return
        self._start_library_scan(self._current_drive)

    def _start_library_scan(self, path: str) -> None:
        self._cancel_library_populate()
        self._set_processing_state(True, "Initializing scan...")
        if hasattr(self, "_scanner_thread") and self._scanner_thread.isRunning():
            self._scanner_thread.cancel()
            self._scanner_thread.wait()
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
        elif filepath:
            self._global_progress.setRange(0, 0)
            self._prog_status_lbl.setText(filepath)
            
    def _on_scan_finished(self, data_model) -> None:
        """Handle completion of the global drive scan."""
        self._begin_library_populate(data_model, reveal_tabs_incrementally=True)

    def _on_library_changed(self, data_model) -> None:
        """Refresh tabs after an in-place library mutation such as a rename."""
        self._begin_library_populate(data_model, reveal_tabs_incrementally=False)

    def _library_populate_jobs(self, data_model) -> list[tuple[str, QWidget, object]]:
        jobs: list[tuple[str, QWidget, object]] = []
        if self._tabs.isTabVisible(0):
            jobs.append(
                ("Drive Information", self._drive_info, lambda: self._drive_info.populate_data(data_model))
            )
        jobs.extend(
            [
                ("File Browser", self._music_browser, lambda: self._music_browser.populate_data(data_model)),
                ("Music Compatibility", self._music_compatibility, lambda: self._music_compatibility.populate_data(data_model)),
                ("Metadata Browser", self._metadata_manager, lambda: self._metadata_manager.populate_data(data_model)),
                ("Album Art Manager", self._album_art, lambda: self._album_art.populate_data(data_model)),
                ("Lyrics Manager", self._lyrics_manager, lambda: self._lyrics_manager.populate_data(data_model)),
                ("File Rename", self._file_rename, lambda: self._file_rename.populate_data(data_model)),
                ("File Cleanup", self._file_cleanup, lambda: self._file_cleanup.populate_data(data_model)),
                ("Backup / Restore", self._backup_restore, lambda: self._backup_restore.populate_data(data_model)),
                ("Workflows", self._workflows, lambda: self._workflows.populate_data(data_model)),
            ]
        )
        return jobs

    def _cancel_library_populate(self) -> None:
        self._populate_generation += 1
        self._populate_queue = []

    def _begin_library_populate(self, data_model, *, reveal_tabs_incrementally: bool) -> None:
        """Fill tabs one at a time so the progress bar can paint between them."""
        self._populate_generation += 1
        jobs = self._library_populate_jobs(data_model)
        current = self._tabs.currentWidget()
        jobs.sort(key=lambda job: 0 if job[1] is current else 1)
        self._populate_queue = jobs
        self._populate_total = len(jobs)
        self._populate_done = 0
        self._populate_reveal = reveal_tabs_incrementally
        self._populate_status_prefix = (
            "Loading tables" if reveal_tabs_incrementally else "Updating tables"
        )
        self._prog_status_lbl.setText(f"{self._populate_status_prefix}...")
        self._prog_container.show()
        self._global_progress.setRange(0, max(self._populate_total, 1))
        self._global_progress.setValue(0)
        if hasattr(self, "_refresh_btn"):
            self._refresh_btn.setEnabled(False)
        generation = self._populate_generation
        QTimer.singleShot(0, lambda: self._pump_library_populate(generation))

    def _pump_library_populate(self, generation: int) -> None:
        if generation != self._populate_generation:
            return
        if not self._populate_queue:
            self._set_processing_state(False)
            return

        label, widget, populate = self._populate_queue.pop(0)
        self._populate_done += 1
        self._prog_status_lbl.setText(
            f"{self._populate_status_prefix}: {label} ({self._populate_done}/{self._populate_total})"
        )
        self._global_progress.setValue(self._populate_done - 1)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

        try:
            populate()
        except Exception as exc:
            print(f"Failed to populate {label}: {exc}")

        if self._populate_reveal and hasattr(widget, "set_processing_state"):
            widget.set_processing_state(False)

        self._global_progress.setValue(self._populate_done)
        QTimer.singleShot(0, lambda: self._pump_library_populate(generation))

    def _on_library_tab_changed(self, index: int) -> None:
        """Load the tab the user just opened next, instead of leaving it queued."""
        if not self._populate_queue:
            return
        widget = self._tabs.widget(index)
        for i, job in enumerate(self._populate_queue):
            if job[1] is widget:
                self._populate_queue.insert(0, self._populate_queue.pop(i))
                break

    def _set_processing_state(self, is_processing: bool, status_text: str = "Processing data...") -> None:
        """Toggle the global loading state and UI indicators."""
        if is_processing:
            self._prog_status_lbl.setText(status_text)
            self._prog_container.show()
            self._global_progress.setRange(0, 0)
        else:
            self._prog_container.hide()
            # Reset progress bar for next time
            self._global_progress.setRange(0, 0)

        if hasattr(self, "_refresh_btn"):
            self._refresh_btn.setEnabled(bool(self._current_drive) and not is_processing)

        # Notify child tabs that need to show empty/loading states
        self._metadata_manager.set_processing_state(is_processing)
        self._music_browser.set_processing_state(is_processing)
        self._music_compatibility.set_processing_state(is_processing)
        self._album_art.set_processing_state(is_processing)
        self._lyrics_manager.set_processing_state(is_processing)
        self._file_rename.set_processing_state(is_processing)
        self._file_cleanup.set_processing_state(is_processing)
        self._backup_restore.set_processing_state(is_processing)
        self._workflows.set_processing_state(is_processing)

