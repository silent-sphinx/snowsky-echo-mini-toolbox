"""
Glassmorphism-inspired stat card widget.

A reusable card that displays a large number with a label,
featuring a semi-transparent surface background and a coloured accent indicator.
"""

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import Colours


class StatCard(QWidget):
    """
    A minimal, square stat card showing a numeric value and label.

    Features:
    - Styled entirely via QSS for perfect rendering and layout.
    - Coloured accent indicator line on the left edge.
    - Animated count-up when value changes.
    """

    def __init__(self, label: str, accent_colour: str = Colours.ACCENT, parent=None):
        super().__init__(parent)
        self._accent_colour = accent_colour
        self._display_value = 0
        self._target_value = 0

        self.setFixedHeight(68)
        self.setMinimumWidth(140)

        # Apply QSS styling to the widget itself
        self.setObjectName("statCard")
        self.setStyleSheet(f"""
            QWidget#statCard {{
                background-color: {Colours.BG_SURFACE};
                border: 1px solid {Colours.BORDER_SUBTLE};
                border-left: 4px solid {self._accent_colour};
            }}
            QWidget#statCard:hover {{
                background-color: {Colours.BG_ELEVATED};
                border-color: {Colours.BORDER_DEFAULT};
                border-left: 4px solid {self._accent_colour};
            }}
        """)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        # Value label
        self._value_label = QLabel("0")
        self._value_label.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        font = QFont()
        font.setPointSize(22)
        font.setWeight(QFont.Bold)
        self._value_label.setFont(font)
        self._value_label.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; background: transparent; border: none;")
        layout.addWidget(self._value_label)

        # Description label
        self._label = QLabel(label)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._label.setStyleSheet(
            f"color: {Colours.TEXT_SECONDARY}; font-size: 11px; "
            f"font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(self._label)

    # ── Animated value property ─────────────────────────────────────

    def _get_display_value(self) -> int:
        return self._display_value

    def _set_display_value(self, val: int) -> None:
        self._display_value = val
        self._value_label.setText(f"{val:,}")

    displayValue = Property(int, _get_display_value, _set_display_value)

    def set_value(self, value: int, animate: bool = True) -> None:
        """Set the displayed value, optionally with count-up animation."""
        self._target_value = value
        if animate and value != self._display_value:
            anim = QPropertyAnimation(self, b"displayValue", self)
            anim.setDuration(600)
            anim.setStartValue(self._display_value)
            anim.setEndValue(value)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
        else:
            self._display_value = value
            self._value_label.setText(f"{value:,}")
