"""
Custom item delegates for the metadata table view.

Provides visual enhancements like codec badges, duration formatting,
and missing-field indicators.
"""

from PySide6.QtCore import QRect, Qt, QModelIndex
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..theme import Colours


class CodecBadgeDelegate(QStyledItemDelegate):
    """
    Renders codec names as coloured rounded badges.

    Different codecs get distinct accent colours for quick visual scanning.
    """

    _CODEC_COLOURS = {
        "flac": ("#1B5E20", "#A5D6A7"),
        "mp3":  ("#1565C0", "#90CAF9"),
        "aac":  ("#6A1B9A", "#CE93D8"),
        "ogg":  ("#E65100", "#FFCC80"),
        "wav":  ("#4E342E", "#BCAAA4"),
        "opus": ("#00695C", "#80CBC4"),
        "m4a":  ("#6A1B9A", "#CE93D8"),
        "wma":  ("#B71C1C", "#EF9A9A"),
        "aiff": ("#37474F", "#B0BEC5"),
    }

    _DEFAULT_COLOURS = ("#3A3A52", "#A0A0B8")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Get the codec text
        text = index.data(Qt.DisplayRole)
        if not text:
            painter.restore()
            return

        codec_key = text.strip().lower()
        bg_colour, text_colour = self._CODEC_COLOURS.get(codec_key, self._DEFAULT_COLOURS)

        # Calculate badge rect (centered in cell)
        font = QFont(option.font)
        font.setPointSize(10)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)

        text_width = painter.fontMetrics().horizontalAdvance(text)
        badge_width = text_width + 16
        badge_height = 22
        badge_x = option.rect.center().x() - badge_width // 2
        badge_y = option.rect.center().y() - badge_height // 2
        badge_rect = QRect(badge_x, badge_y, badge_width, badge_height)

        # Draw selected/hover background first if needed
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(Colours.ACCENT_BG))

        # Draw square badge
        path = QPainterPath()
        path.addRect(badge_rect.x(), badge_rect.y(),
                            badge_rect.width(), badge_rect.height())
        painter.fillPath(path, QColor(bg_colour))

        # Draw text perfectly centered
        painter.setPen(QColor(text_colour))
        text_rect = badge_rect.adjusted(0, -1, 0, 0) # adjust for font baseline
        painter.drawText(text_rect, Qt.AlignCenter, text)

        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 34))
        return hint


class MissingFieldDelegate(QStyledItemDelegate):
    """
    Renders fields with a subtle 'missing' indicator when the value is absent.

    Shows a dimmed "—" with a small coloured dot to draw attention to gaps.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        from ..models.metadata_table_model import MISSING_FIELD_ROLE

        is_missing = index.data(MISSING_FIELD_ROLE)

        if is_missing:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)

            # Draw subtle background tint
            bg = QColor(Colours.STATUS_MISSING)
            bg.setAlphaF(0.08)
            painter.fillRect(option.rect, bg)

            # Draw the dot indicator (refined size)
            dot_radius = 2.5
            dot_x = option.rect.x() + 12
            dot_y = option.rect.center().y()
            painter.setBrush(QColor(Colours.STATUS_MISSING))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(dot_x - dot_radius, dot_y - dot_radius,
                                dot_radius * 2, dot_radius * 2)

            # Draw "Missing" text
            font = QFont(option.font)
            font.setItalic(True)
            font.setPointSize(11)
            painter.setFont(font)
            painter.setPen(QColor(Colours.STATUS_MISSING_TEXT))

            text_rect = option.rect.adjusted(22, 0, 0, 0)
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, "Missing")

            painter.restore()
        else:
            # Default rendering for non-missing values
            super().paint(painter, option, index)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 34))
        return hint
