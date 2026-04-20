import argparse
import sys

from PySide6.QtWidgets import QApplication

from .window import ToolboxWindow, apply_charcoal_palette


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snowsky Echo Mini Toolbox")
    parser.add_argument(
        "--path",
        "-p",
        help="Optional initial folder or drive path to inspect.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    QApplication.setStyle("Fusion")
    app = QApplication(sys.argv)
    app.setApplicationName("Snowsky Echo Mini Toolbox")
    apply_charcoal_palette(app)

    window = ToolboxWindow(initial_path=args.path)
    window.show()
    return app.exec()
