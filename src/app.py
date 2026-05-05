import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
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


def main() -> int:
    args = parse_args()

    QApplication.setStyle("Fusion")
    app = QApplication(sys.argv)
    # Identify the application for QSettings storage
    app.setOrganizationName("Snowsky")
    app.setApplicationName("Snowsky Echo Mini Toolbox")
    icon_path = _asset_path("assets", "toolbox-logo.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_charcoal_palette(app)

    window = ToolboxWindow(initial_path=args.path)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()
    return app.exec()
