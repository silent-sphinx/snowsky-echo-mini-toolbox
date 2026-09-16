"""Shared page chrome for the manager-style tabs."""

from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..theme import Colours

PATH_COLUMN_WIDTH = 440


@contextmanager
def freeze_view(view: QAbstractItemView):
    """Pause painting and sorting while a large model is swapped in."""
    sorting = False
    sorter = getattr(view, "isSortingEnabled", None)
    if callable(sorter):
        sorting = bool(sorter())
        view.setSortingEnabled(False)
    view.setUpdatesEnabled(False)
    try:
        yield
    finally:
        view.setUpdatesEnabled(True)
        if callable(getattr(view, "setSortingEnabled", None)):
            view.setSortingEnabled(sorting)


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


def flow_steps(steps: list[tuple[str, str, str]]) -> QWidget:
    """Numbered 1-2-3 guide row in the shared panel treatment."""
    panel = QWidget()
    panel.setObjectName("panelSection")
    row = QHBoxLayout(panel)
    row.setContentsMargins(16, 12, 16, 12)
    row.setSpacing(16)

    for index, (number, title, body) in enumerate(steps):
        if index:
            divider = QFrame()
            divider.setFrameShape(QFrame.NoFrame)
            divider.setFixedWidth(1)
            divider.setStyleSheet(f"background-color: {Colours.BORDER_SUBTLE};")
            row.addWidget(divider)

        step = QWidget()
        step_layout = QHBoxLayout(step)
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.setSpacing(10)

        number_lbl = QLabel(number)
        number_lbl.setObjectName("flowStepNumber")
        number_lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        number_lbl.setFixedWidth(22)
        step_layout.addWidget(number_lbl)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(2)

        title_lbl = QLabel(title.upper())
        title_lbl.setObjectName("flowStepTitle")
        title_lbl.setWordWrap(True)
        copy.addWidget(title_lbl)

        body_lbl = QLabel(body)
        body_lbl.setObjectName("flowStepBody")
        body_lbl.setWordWrap(True)
        copy.addWidget(body_lbl)

        step_layout.addLayout(copy, 1)
        row.addWidget(step, 1)

    return panel
