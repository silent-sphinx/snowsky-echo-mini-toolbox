import sys
from PySide6.QtWidgets import QHeaderView, QStyleOptionHeader, QStyle
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt, QRect

class GroupedHeaderView(QHeaderView):
    """
    A custom QHeaderView that supports a top row of spanned 'group' headers, 
    and a bottom row of standard column headers.
    """
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._groups = []
        # Moving columns would break the continuous visual span of groups
        self.setSectionsMovable(False)
        
    def add_group(self, title: str, start_col: int, end_col: int) -> None:
        """
        Add a group spanning from start_col to end_col (inclusive).
        """
        self._groups.append((title, start_col, end_col))
        
    def sizeHint(self):
        size = super().sizeHint()
        if self._groups:
            # Double the height to accommodate two rows
            size.setHeight(size.height() * 2)
        return size
        
    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        if not self._groups:
            super().paintSection(painter, rect, logicalIndex)
            return
            
        is_in_group = any(start <= logicalIndex <= end for _, start, end in self._groups)
        half_height = rect.height() // 2
        
        # 1. Paint the main column header (bottom half if in group, full height if not)
        bottom_rect = QRect(rect.left(), rect.top() + half_height, rect.width(), half_height) if is_in_group else rect
        
        painter.save()
        painter.setClipRect(bottom_rect)
        
        if logicalIndex == 0:
            # Explicitly remove the sort indicator for the checkbox column
            opt = QStyleOptionHeader()
            self.initStyleOption(opt)
            opt.rect = bottom_rect
            opt.text = ""
            opt.sortIndicator = QStyleOptionHeader.None_
            self.style().drawControl(QStyle.CE_HeaderSection, opt, painter, self)
        else:
            super().paintSection(painter, bottom_rect, logicalIndex)
            
        painter.restore()

        # 2. Paint the top half (the group header) only if in a group
        if is_in_group:
            for title, start, end in self._groups:
                if start <= logicalIndex <= end:
                    # Calculate the total width of this group by summing visible section sizes
                    span_width = 0
                    for i in range(start, end + 1):
                        span_width += self.sectionSize(i)
                    
                    # Get the visual X coordinate of the group's start column (even if negative/off-screen)
                    group_left = self.sectionViewportPosition(start)
                        
                    top_rect = QRect(group_left, rect.top(), span_width, half_height)
                    
                    painter.save()
                    # Clip drawing precisely to this column's top half
                    clip_rect = QRect(rect.left(), rect.top(), rect.width(), half_height)
                    painter.setClipRect(clip_rect)
                    
                    # Draw the group header using the application's QSS style
                    opt = QStyleOptionHeader()
                    self.initStyleOption(opt)
                    opt.rect = top_rect
                    opt.text = title
                    opt.textAlignment = Qt.AlignCenter
                    
                    # Draw background & borders
                    self.style().drawControl(QStyle.CE_HeaderSection, opt, painter, self)
                    # Draw text
                    self.style().drawControl(QStyle.CE_HeaderLabel, opt, painter, self)
                    
                    painter.restore()
                    break
