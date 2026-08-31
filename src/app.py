"""
Application entry point for the rewrite.
"""

import sys
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import apply_theme


def main() -> int:
    """Run the application."""
    app = QApplication(sys.argv)
    
    # Apply global theme (palette, styles, fonts)
    apply_theme(app)
    
    # Launch main window
    window = MainWindow()
    window.show()
    
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
