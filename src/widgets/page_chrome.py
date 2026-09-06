"""Shared page chrome for the manager-style tabs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


def page_header(
    title: str,
    subtitle: str,
    actions: list[QWidget] | None = None,
) -> QWidget:
    """Title and subtitle on the left, optional actions on the right, then a divider."""
    container = QWidget()
    outer = QVBoxLayout(container)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(12)

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(16)

    titles = QVBoxLayout()
    titles.setContentsMargins(0, 0, 0, 0)
    titles.setSpacing(2)

    title_lbl = QLabel(title)
    title_lbl.setObjectName("headerTitle")
    title_lbl.setWordWrap(True)
    titles.addWidget(title_lbl)

    subtitle_lbl = QLabel(subtitle)
    subtitle_lbl.setObjectName("headerSubtitle")
    subtitle_lbl.setWordWrap(True)
    titles.addWidget(subtitle_lbl)

    row.addLayout(titles, 1)

    if actions:
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.setAlignment(Qt.AlignVCenter)
        for widget in actions:
            action_row.addWidget(widget)
        row.addLayout(action_row)

    outer.addLayout(row)

    sep = QFrame()
    sep.setObjectName("separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    outer.addWidget(sep)

    return container


def loading_page(title: str, subtitle: str) -> QWidget:
    """Centered empty/loading state that uses the same type scale as page headers."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setAlignment(Qt.AlignCenter)
    layout.setSpacing(6)

    title_lbl = QLabel(title)
    title_lbl.setObjectName("headerTitle")
    title_lbl.setAlignment(Qt.AlignCenter)
    layout.addWidget(title_lbl)

    subtitle_lbl = QLabel(subtitle)
    subtitle_lbl.setObjectName("headerSubtitle")
    subtitle_lbl.setAlignment(Qt.AlignCenter)
    layout.addWidget(subtitle_lbl)

    return page


def filter_toolbar() -> tuple[QWidget, QHBoxLayout]:
    """Search/filter bar in the shared panel treatment."""
    panel = QWidget()
    panel.setObjectName("panelSection")
    toolbar = QHBoxLayout(panel)
    toolbar.setContentsMargins(12, 8, 12, 8)
    toolbar.setSpacing(8)
    return panel, toolbar
