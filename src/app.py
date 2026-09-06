"""
Application entry point for the rewrite.
"""

import sys
from PySide6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import apply_theme

_STALE_DPR_WARNING = "cached device pixel ratio value was stale"


def _qt_message_handler(mode, context, message: str) -> None:
    """Drop the known Qt HiDPI expose warning (QTBUG-118794 / QTBUG-120715)."""
    if mode == QtMsgType.QtWarningMsg and _STALE_DPR_WARNING in message:
        return

    prefixes = {
        QtMsgType.QtDebugMsg: "Debug",
        QtMsgType.QtInfoMsg: "Info",
        QtMsgType.QtWarningMsg: "Warning",
        QtMsgType.QtCriticalMsg: "Critical",
        QtMsgType.QtFatalMsg: "Fatal",
    }
    prefix = prefixes.get(mode, "Qt")
    sys.stderr.write(f"{prefix}: {message}\n")


def main() -> int:
    """Run the application."""
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)

    # Apply global theme (palette, styles, fonts)
    apply_theme(app)

    # Create the native window before the first expose so Cocoa's device
    # pixel ratio is current. Showing children during that first expose is
    # what triggers QTBUG-118794 on Retina macOS.
    window = MainWindow()
    window.winId()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
