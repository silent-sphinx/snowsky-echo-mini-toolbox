"""
Custom QTableView subclass for the metadata table.

Provides smooth alternating row colours, hover highlights,
custom header styling, and context menu support.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from ..models.metadata_table_model import Column


class MetadataTableView(QTableView):
    """Styled table view for bulk music metadata display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._configure()

    def _configure(self) -> None:
        """Set up table appearance and behaviour."""
        # General
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSortingEnabled(True)
        self.sortByColumn(-1, Qt.AscendingOrder)
        self.setWordWrap(False)

        # Vertical header (row numbers) — hidden for cleaner look
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(28)

        # Horizontal header
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setMinimumSectionSize(30)

        # Smooth scrolling
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)

        # Focus
        self.setFocusPolicy(Qt.StrongFocus)

    def apply_column_widths(self) -> None:
        """Set default column widths appropriate for metadata columns."""
        header = self.horizontalHeader()

        # Checkbox column — fixed narrow
        header.resizeSection(Column.CHECKBOX, 36)
        header.setSectionResizeMode(Column.CHECKBOX, QHeaderView.Fixed)

        # Track number — narrow
        header.resizeSection(Column.TRACK_NUM, 42)

        # Main metadata columns — stretch proportionally
        header.resizeSection(Column.TITLE, 220)
        header.resizeSection(Column.ARTIST, 160)
        header.resizeSection(Column.ALBUM, 180)
        header.resizeSection(Column.GENRE, 110)
        header.resizeSection(Column.YEAR, 55)
        header.resizeSection(Column.DURATION, 70)
        header.resizeSection(Column.CODEC, 80)
        header.resizeSection(Column.BITRATE, 85)
        header.resizeSection(Column.SAMPLE_RATE, 95)

        # File path — stretches
        header.setSectionResizeMode(Column.FILE_PATH, QHeaderView.Stretch)
