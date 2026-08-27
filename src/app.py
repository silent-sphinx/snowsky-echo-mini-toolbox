import argparse
import sys
import os
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtGui import QIcon

from .window import ToolboxWindow, apply_charcoal_palette


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snowsky Echo Mini Toolbox")
    parser.add_argument(
        "--path",
        "-p",
        help="Optional initial folder or drive path to inspect.",
    )
    return parser.parse_args()


def _asset_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_path = Path(__file__).resolve().parent.parent
    return base_path.joinpath(*parts)


class CenterDialogsFilter(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            if isinstance(obj, (QDialog, QMessageBox)):
                parent = obj.parentWidget() if hasattr(obj, "parentWidget") else None
                main_win = parent.window() if parent else QApplication.activeWindow()
                
                if main_win and main_win != obj:
                    def center_it():
                        try:
                            # Force layout calculation
                            obj.adjustSize()
                            parent_rect = main_win.geometry()
                            obj_rect = obj.frameGeometry()
                            obj_rect.moveCenter(parent_rect.center())
                            obj.move(obj_rect.topLeft())
                        except RuntimeError:
                            pass # Object might have been destroyed if closed instantly
                            
                    # Defer centering to run on the next event loop cycle,
                    # ensuring the OS window manager doesn't override our move() call.
                    QTimer.singleShot(0, center_it)
                    
        return super().eventFilter(obj, event)


def main() -> int:
    args = parse_args()
    
    # Suppress harmless font OpenType warnings that appear for unsupported unicode scripts
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false"

    QApplication.setStyle("Fusion")
    app = QApplication(sys.argv)
    # Identify the application for QSettings storage
    app.setOrganizationName("Snowsky")
    app.setApplicationName("Snowsky Echo Mini Toolbox")
    icon_path = _asset_path("assets", "toolbox-logo.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_charcoal_palette(app)

    # Center all dialogs on their parent windows automatically
    center_filter = CenterDialogsFilter(app)
    app.installEventFilter(center_filter)

    window = ToolboxWindow(initial_path=args.path)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()
    return app.exec()
