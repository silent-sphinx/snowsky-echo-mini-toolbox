"""
Application entry point for the rewrite.
"""

from __future__ import annotations

import faulthandler
import sys
import threading
import traceback

from PySide6.QtCore import QCoreApplication, QObject, Qt, QtMsgType, QThread, Signal, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow
from .theme import apply_theme

_STALE_DPR_WARNING = "cached device pixel ratio value was stale"
_CRASH_REPORTER: CrashReporter | None = None
_SHOWING_CRASH_DIALOG = False


class CrashReporter(QObject):
    """Show uncaught errors on the GUI thread as a Qt message box."""

    _show = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._show.connect(self._display, Qt.QueuedConnection)

    def report(self, title: str, details: str) -> None:
        app = QApplication.instance()
        if app is None:
            sys.stderr.write(f"{title}\n{details}\n")
            return
        if QThread.currentThread() is app.thread():
            self._display(title, details)
        else:
            self._show.emit(title, details)

    def _display(self, title: str, details: str) -> None:
        global _SHOWING_CRASH_DIALOG
        if _SHOWING_CRASH_DIALOG:
            sys.stderr.write(f"{title}\n{details}\n")
            return

        _SHOWING_CRASH_DIALOG = True
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Snowsky Echo Mini Toolbox")
            box.setText(title)
            box.setInformativeText(
                "The app hit an unexpected error. Copy the details if you want to "
                "report it. Continue only if the window still looks usable; otherwise quit."
            )
            box.setDetailedText(details)
            box.setStandardButtons(QMessageBox.Abort | QMessageBox.Ignore)
            box.setDefaultButton(QMessageBox.Abort)
            box.setWindowModality(Qt.ApplicationModal)
            if box.exec() == QMessageBox.Abort:
                QApplication.instance().quit()
        except Exception:
            sys.stderr.write(f"{title}\n{details}\n")
        finally:
            _SHOWING_CRASH_DIALOG = False


class CrashAwareApplication(QApplication):
    """Catch exceptions raised while Qt is delivering events and slots."""

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            _report_exception(*sys.exc_info())
            return False


def _format_exception(exc_type, exc, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


def _report_exception(exc_type, exc, tb) -> None:
    if exc_type is None or issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc, tb)
        return

    title = f"{getattr(exc_type, '__name__', 'Error')}: {exc}"
    details = _format_exception(exc_type, exc, tb)
    sys.stderr.write(details)
    if not details.endswith("\n"):
        sys.stderr.write("\n")

    if _CRASH_REPORTER is not None:
        _CRASH_REPORTER.report(title, details)
        return

    _show_startup_crash_dialog(title, details)


def _show_startup_crash_dialog(title: str, details: str) -> None:
    """Last-resort dialog when the crash reporter is not installed yet."""
    try:
        app = QApplication.instance() or CrashAwareApplication(sys.argv)
        reporter = CrashReporter(app)
        reporter.report(title, details)
    except Exception:
        sys.stderr.write(f"{title}\n{details}\n")


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    _report_exception(args.exc_type, args.exc_value, args.exc_traceback)


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

    if mode == QtMsgType.QtFatalMsg:
        details = message
        if getattr(context, "file", None):
            details = f"{context.file}:{context.line}\n{message}"
        if _CRASH_REPORTER is not None:
            _CRASH_REPORTER.report("Qt fatal error", details)
        else:
            _show_startup_crash_dialog("Qt fatal error", details)


def _install_crash_hooks(app: QApplication) -> CrashReporter:
    global _CRASH_REPORTER
    reporter = CrashReporter(app)
    _CRASH_REPORTER = reporter
    sys.excepthook = _report_exception
    threading.excepthook = _thread_excepthook
    return reporter


def main() -> int:
    """Run the application."""
    faulthandler.enable(all_threads=True)

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qInstallMessageHandler(_qt_message_handler)

    QCoreApplication.setOrganizationName("Snowsky Echo Mini Toolbox")
    QCoreApplication.setApplicationName("Snowsky Echo Mini Toolbox")

    try:
        app = CrashAwareApplication(sys.argv)
        _install_crash_hooks(app)

        # Apply global theme (palette, styles, fonts)
        apply_theme(app)

        # Create the native window before the first expose so Cocoa's device
        # pixel ratio is current. Showing children during that first expose is
        # what triggers QTBUG-118794 on Retina macOS.
        window = MainWindow()
        window.winId()
        window.show()

        return app.exec()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        _report_exception(*sys.exc_info())
        return 1


if __name__ == "__main__":
    sys.exit(main())
