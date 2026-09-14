"""Read-only dialog for viewing full lyrics text."""

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..theme import Colours


class LyricsPreviewDialog(QDialog):
    def __init__(self, title: str, lyrics_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("lyricsPreviewDialog")
        self.setWindowTitle(f"Lyrics Preview — {title}")
        self.setMinimumSize(520, 640)
        self.setStyleSheet(
            f"""
            QDialog#lyricsPreviewDialog {{
                background-color: {Colours.BG_ELEVATED};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            QDialog#lyricsPreviewDialog QLabel {{
                background-color: transparent;
                color: {Colours.TEXT_PRIMARY};
            }}
            QDialog#lyricsPreviewDialog QPlainTextEdit {{
                background-color: {Colours.BG_SURFACE};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 15px; font-weight: 700;")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(lyrics_text or "No lyrics available.")
        layout.addWidget(text, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)
