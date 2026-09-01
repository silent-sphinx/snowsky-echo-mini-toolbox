import math
import os
from collections import Counter
from PySide6.QtCore import Qt, QStorageInfo, QRectF
from PySide6.QtGui import (
    QFont, QPainter, QColor, QPen, QConicalGradient, QBrush
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QGridLayout,
    QScrollArea,
    QSizePolicy
)

from ..theme import Colours
from ..constants import MAX_TRACK_LIMIT, SUPPORTED_MEDIA_EXTENSIONS


# ── Donut Chart ─────────────────────────────────────────────────────────────

class DonutChartWidget(QWidget):
    """Hollow donut chart with gaps between segments and optional center text."""

    PALETTE = [
        "#5C6BC0", "#42A5F5", "#26C6DA", "#66BB6A", "#FFA726",
        "#EF5350", "#AB47BC", "#8D6E63", "#78909C",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict[str, int] = {}
        self._colors = [QColor(c) for c in self.PALETTE]
        self._center_label = ""
        self._center_sub = ""
        self.setMinimumSize(160, 160)

    def set_data(self, data: dict, center_label: str = "", center_sub: str = ""):
        self._data = data
        self._center_label = center_label
        self._center_sub = center_sub
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        size = min(w, h) - 16
        ring_width = max(20, size * 0.15)
        rect = QRectF((w - size) / 2, (h - size) / 2, size, size)

        draw_rect = rect.adjusted(ring_width / 2, ring_width / 2,
                                  -ring_width / 2, -ring_width / 2)

        if not self._data or sum(self._data.values()) == 0:
            pen = QPen(QColor(Colours.BG_DARKEST))
            pen.setWidthF(ring_width)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            painter.drawArc(draw_rect, 0, 360 * 16)
            self._draw_center_text(painter, rect)
            return

        total = sum(self._data.values())
        items = list(self._data.items())
        num = len(items)
        gap = 2.5 if num > 1 else 0
        available = 360 - gap * num
        start = 90.0

        for i, (key, value) in enumerate(items):
            span = (value / total) * available
            color = self._colors[i % len(self._colors)]
            pen = QPen(color)
            pen.setWidthF(ring_width)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            painter.drawArc(draw_rect, int(start * 16), int(-span * 16))
            start -= span + gap

        self._draw_center_text(painter, rect)

    def _draw_center_text(self, painter: QPainter, rect: QRectF):
        if self._center_label:
            painter.setPen(QColor(Colours.TEXT_PRIMARY))
            f = painter.font()
            f.setPixelSize(max(16, int(rect.height() * 0.13)))
            f.setBold(True)
            painter.setFont(f)
            r = QRectF(rect); r.translate(0, -8)
            painter.drawText(r, Qt.AlignCenter, self._center_label)
        if self._center_sub:
            painter.setPen(QColor(Colours.TEXT_SECONDARY))
            f = painter.font()
            f.setPixelSize(max(10, int(rect.height() * 0.065)))
            f.setBold(False)
            painter.setFont(f)
            r = QRectF(rect); r.translate(0, 14)
            painter.drawText(r, Qt.AlignCenter, self._center_sub)


# ── Storage Ring ────────────────────────────────────────────────────────────

class StorageRingWidget(QWidget):
    """Arc-based storage usage ring with gradient colour and center stats."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self._percent = 0.0
        self._used_text = "0 GB"
        self._total_text = "of 0 GB"

    def set_data(self, used_gb: float, total_gb: float):
        self._percent = used_gb / total_gb if total_gb > 0 else 0.0
        self._used_text = f"{used_gb:.1f} GB"
        self._total_text = f"of {total_gb:.1f} GB"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        size = min(w, h) - 28
        rw = 20
        rect = QRectF((w - size) / 2, (h - size) / 2, size, size)
        dr = rect.adjusted(rw / 2, rw / 2, -rw / 2, -rw / 2)

        # Background ring
        pen_bg = QPen(QColor(Colours.BG_DARKEST)); pen_bg.setWidthF(rw); pen_bg.setCapStyle(Qt.FlatCap)
        painter.setPen(pen_bg)
        painter.drawArc(dr, 0, 360 * 16)

        # Foreground
        if self._percent > 0:
            if self._percent < 0.7:
                c_s, c_e = QColor("#4A4A4A"), QColor("#8C8C8C")
            elif self._percent < 0.9:
                c_s, c_e = QColor("#8B6914"), QColor(Colours.STATUS_LIMITED)
            else:
                c_s, c_e = QColor("#8B1A1A"), QColor(Colours.STATUS_UNSUPPORTED)

            grad = QConicalGradient(dr.center(), 90)
            grad.setColorAt(0.0, c_s)
            grad.setColorAt(min(self._percent, 1.0), c_e)
            grad.setColorAt(1.0, c_s)
            pen_fg = QPen(QBrush(grad), rw); pen_fg.setCapStyle(Qt.RoundCap)
            painter.setPen(pen_fg)
            painter.drawArc(dr, 90 * 16, -int(self._percent * 360 * 16))

        # Center text
        painter.setPen(QColor(Colours.TEXT_PRIMARY))
        f = painter.font(); f.setPixelSize(max(28, int(size * 0.16))); f.setBold(True); painter.setFont(f)
        painter.drawText(QRectF(rect).translated(0, -10), Qt.AlignCenter, self._used_text)

        painter.setPen(QColor(Colours.TEXT_SECONDARY))
        f.setPixelSize(max(12, int(size * 0.065))); f.setBold(False); painter.setFont(f)
        painter.drawText(QRectF(rect).translated(0, 16), Qt.AlignCenter, self._total_text)

        painter.setPen(QColor(Colours.TEXT_TERTIARY))
        f.setPixelSize(max(10, int(size * 0.055))); painter.setFont(f)
        painter.drawText(QRectF(rect).translated(0, 34), Qt.AlignCenter, f"{self._percent * 100:.0f}% used")


# ── Inline Stat ─────────────────────────────────────────────────────────────

def _make_stat_label(value: str, label: str, accent: str, parent) -> QFrame:
    """Build a compact stat block with accent bar, value and caption."""
    card = QFrame(parent)
    card.setFixedHeight(62)
    card.setMinimumWidth(120)
    card.setStyleSheet(
        f"QFrame {{ background-color: {Colours.BG_SURFACE}; "
        f"border: 1px solid {Colours.BORDER_SUBTLE}; border-left: 3px solid {accent}; }} "
        f"QLabel {{ border: none; background: transparent; }}"
    )
    lyt = QVBoxLayout(card)
    lyt.setContentsMargins(10, 6, 10, 6)
    lyt.setSpacing(1)
    v = QLabel(value)
    v.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 17px; font-weight: 800;")
    cap = QLabel(label)
    cap.setStyleSheet(
        f"color: {Colours.TEXT_SECONDARY}; font-size: 10px; font-weight: 700; "
        f"text-transform: uppercase; letter-spacing: 0.5px;"
    )
    lyt.addWidget(v)
    lyt.addWidget(cap)
    card._val = v
    return card


# ── Health Badge Row ────────────────────────────────────────────────────────

def _make_badge(text: str, bg: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedHeight(20)
    lbl.setStyleSheet(
        f"background-color: {bg}; color: white; "
        f"padding: 1px 8px; font-size: 10px; font-weight: bold; letter-spacing: 0.5px;"
    )
    return lbl


# ── Helpers ─────────────────────────────────────────────────────────────────

def _format_duration(total_seconds: float) -> str:
    if total_seconds <= 0:
        return "0 min"
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m} min"


def _format_bytes(b: int) -> str:
    if b < 1024 ** 2:
        return f"{b / 1024:.0f} KB"
    if b < 1024 ** 3:
        return f"{b / (1024 ** 2):.1f} MB"
    return f"{b / (1024 ** 3):.1f} GB"


# ── Main Widget ─────────────────────────────────────────────────────────────

class DriveInfoWidget(QWidget):
    """
    Panel that shows overview stats for a mounted volume in a unified dashboard.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    # ------------------------------------------------------------------ #
    #  UI Setup                                                            #
    # ------------------------------------------------------------------ #

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignTop)

        # ── Header ──────────────────────────────────────────────
        self._title_lbl = QLabel("No Drive Selected")
        self._title_lbl.setStyleSheet(
            f"color: {Colours.TEXT_PRIMARY}; font-size: 26px; font-weight: 800; "
            f"letter-spacing: -0.5px; background: transparent;"
        )
        layout.addWidget(self._title_lbl)

        # Subtitle: filesystem badge + quick note
        sub = QHBoxLayout(); sub.setSpacing(8); sub.setContentsMargins(0, 0, 0, 0)
        self._fs_badge = _make_badge("—", Colours.BG_ELEVATED)
        sub.addWidget(self._fs_badge)
        self._header_note = QLabel("")
        self._header_note.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 12px; background: transparent;")
        sub.addWidget(self._header_note)
        sub.addStretch()
        layout.addLayout(sub)

        # ── Separator ──────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {Colours.BORDER_SUBTLE}; max-height: 1px; border: none;")
        layout.addWidget(sep)

        # ── Stat Card Row ──────────────────────────────────────
        stat_row = QHBoxLayout(); stat_row.setSpacing(10)
        self._sc_capacity = _make_stat_label("—", "Capacity", Colours.ACCENT, self)
        self._sc_tracks   = _make_stat_label("—", "Tracks", "#757575", self)
        self._sc_duration = _make_stat_label("—", "Total Duration", "#5C6BC0", self)
        self._sc_mediasize = _make_stat_label("—", "Media Size", "#42A5F5", self)
        self._sc_artists  = _make_stat_label("—", "Artists", "#AB47BC", self)
        self._sc_albums   = _make_stat_label("—", "Albums", "#26C6DA", self)
        for w in (self._sc_capacity, self._sc_tracks, self._sc_duration,
                  self._sc_mediasize, self._sc_artists, self._sc_albums):
            stat_row.addWidget(w)
        layout.addLayout(stat_row)

        # ── Three-Column Detail Area ───────────────────────────
        detail = QHBoxLayout(); detail.setSpacing(14)

        # ─ Col 1: Storage Usage ─
        c1 = self._card()
        c1_lyt = QVBoxLayout(c1); c1_lyt.setContentsMargins(20, 20, 20, 20); c1_lyt.setSpacing(12)
        c1_lyt.addWidget(self._section_title("Storage Usage"))

        self._storage_ring = StorageRingWidget()
        c1_lyt.addWidget(self._storage_ring, 0, Qt.AlignCenter)

        # Health checks
        health = QVBoxLayout(); health.setSpacing(8)
        health.addWidget(self._section_title("Health Checks"))
        # FS row
        fs_row = QHBoxLayout(); fs_row.setSpacing(8)
        fs_row.addWidget(self._dim_label("File System"))
        fs_row.addStretch()
        self._fs_val = QLabel("—")
        self._fs_val.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 12px; font-weight: bold; border: none; background: transparent;")
        fs_row.addWidget(self._fs_val)
        self._fs_status_badge = _make_badge("—", Colours.BG_ELEVATED)
        fs_row.addWidget(self._fs_status_badge)
        health.addLayout(fs_row)
        # Track limit row
        tk_row = QHBoxLayout(); tk_row.setSpacing(8)
        tk_row.addWidget(self._dim_label("Track Limit"))
        tk_row.addStretch()
        self._track_val = QLabel(f"— / {MAX_TRACK_LIMIT:,}")
        self._track_val.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 12px; font-weight: bold; border: none; background: transparent;")
        tk_row.addWidget(self._track_val)
        self._track_badge = _make_badge("—", Colours.BG_ELEVATED)
        tk_row.addWidget(self._track_badge)
        health.addLayout(tk_row)
        c1_lyt.addLayout(health)
        c1_lyt.addStretch()
        detail.addWidget(c1, 35)

        # ─ Col 2: Media Breakdown (donut) ─
        c2 = self._card()
        c2_lyt = QVBoxLayout(c2); c2_lyt.setContentsMargins(20, 20, 20, 20); c2_lyt.setSpacing(12)
        c2_lyt.addWidget(self._section_title("Format Breakdown"))
        self._donut = DonutChartWidget()
        self._donut.setFixedSize(170, 170)
        c2_lyt.addWidget(self._donut, 0, Qt.AlignCenter)
        self._legend_container = QWidget()
        self._legend_container.setStyleSheet("background: transparent; border: none;")
        self._legend_lyt = QVBoxLayout(self._legend_container)
        self._legend_lyt.setContentsMargins(4, 0, 4, 0)
        self._legend_lyt.setSpacing(6)
        c2_lyt.addWidget(self._legend_container)
        c2_lyt.addStretch()
        detail.addWidget(c2, 30)

        # ─ Col 3: Library Insights ─
        c3 = self._card()
        c3_lyt = QVBoxLayout(c3); c3_lyt.setContentsMargins(20, 20, 20, 20); c3_lyt.setSpacing(14)
        c3_lyt.addWidget(self._section_title("Library Insights"))

        # Compatibility summary
        self._compat_container = QWidget()
        self._compat_container.setStyleSheet("background: transparent; border: none;")
        self._compat_lyt = QVBoxLayout(self._compat_container)
        self._compat_lyt.setContentsMargins(0, 0, 0, 0)
        self._compat_lyt.setSpacing(6)
        c3_lyt.addWidget(self._compat_container)

        # Separator inside card
        c3_sep = QFrame(); c3_sep.setFrameShape(QFrame.HLine)
        c3_sep.setStyleSheet(f"background-color: {Colours.BORDER_SUBTLE}; max-height: 1px; border: none;")
        c3_lyt.addWidget(c3_sep)

        # Top genres
        c3_lyt.addWidget(self._section_title("Top Genres"))
        self._genres_container = QWidget()
        self._genres_container.setStyleSheet("background: transparent; border: none;")
        self._genres_lyt = QVBoxLayout(self._genres_container)
        self._genres_lyt.setContentsMargins(0, 0, 0, 0)
        self._genres_lyt.setSpacing(4)
        c3_lyt.addWidget(self._genres_container)

        # Separator inside card
        c3_sep2 = QFrame(); c3_sep2.setFrameShape(QFrame.HLine)
        c3_sep2.setStyleSheet(f"background-color: {Colours.BORDER_SUBTLE}; max-height: 1px; border: none;")
        c3_lyt.addWidget(c3_sep2)

        # Bitrate / Sample-rate summary
        c3_lyt.addWidget(self._section_title("Audio Quality"))
        self._quality_container = QWidget()
        self._quality_container.setStyleSheet("background: transparent; border: none;")
        self._quality_lyt = QVBoxLayout(self._quality_container)
        self._quality_lyt.setContentsMargins(0, 0, 0, 0)
        self._quality_lyt.setSpacing(4)
        c3_lyt.addWidget(self._quality_container)

        c3_lyt.addStretch()
        detail.addWidget(c3, 35)

        layout.addLayout(detail, 1)
        scroll.setWidget(container)
        root.addWidget(scroll)

    # ── Builder helpers ─────────────────────────────────────────

    @staticmethod
    def _card() -> QFrame:
        f = QFrame()
        f.setStyleSheet(
            f"QFrame {{ background-color: {Colours.BG_SURFACE}; "
            f"border: 1px solid {Colours.BORDER_SUBTLE}; }} "
            f"QLabel {{ border: none; background: transparent; }}"
        )
        return f

    @staticmethod
    def _section_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Colours.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; "
            f"text-transform: uppercase; letter-spacing: 1px; background: transparent; border: none;"
        )
        return lbl

    @staticmethod
    def _dim_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Colours.TEXT_SECONDARY}; font-size: 12px; font-weight: 600; "
            f"background: transparent; border: none;"
        )
        return lbl

    # ── Clear dynamic layouts ───────────────────────────────────

    @staticmethod
    def _clear_layout(lyt):
        while lyt.count():
            item = lyt.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ================================================================== #
    #  Public API                                                          #
    # ================================================================== #

    def set_drive(self, path: str) -> None:
        """Update the UI with storage info for the given path."""
        info = QStorageInfo(path)

        if not info.isValid() or not info.isReady():
            self._title_lbl.setText("Drive Unavailable")
            return

        name = info.name() or "Local Disk"
        self._title_lbl.setText(f"{name} ({info.rootPath()})")

        # Filesystem
        fs_type = bytes(info.fileSystemType()).decode("utf-8", errors="ignore").upper()
        if not fs_type:
            fs_type = "UNKNOWN"

        supported_fs = ["FAT16", "FAT32", "EXFAT", "FAT", "MSDOS"]
        is_ok = fs_type in supported_fs or fs_type == "UNKNOWN"

        self._fs_badge.setText(fs_type)
        badge_bg = Colours.STATUS_SUPPORTED if is_ok else Colours.STATUS_UNSUPPORTED
        self._fs_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: white; "
            f"padding: 1px 8px; font-size: 10px; font-weight: bold; letter-spacing: 0.5px;"
        )
        self._fs_val.setText(fs_type)
        status_text = "OK" if is_ok else "UNSUPPORTED"
        self._fs_status_badge.setText(status_text)
        self._fs_status_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: white; "
            f"padding: 1px 8px; font-size: 10px; font-weight: bold;"
        )

        # Capacity
        total = info.bytesTotal()
        free = info.bytesAvailable()
        used = total - free
        t_gb = total / (1024 ** 3)
        u_gb = used / (1024 ** 3)

        self._sc_capacity._val.setText(f"{t_gb:.1f} GB")
        self._header_note.setText(
            f"{u_gb:.1f} GB used  •  {t_gb - u_gb:.1f} GB free"
        )
        self._storage_ring.set_data(u_gb, t_gb)

        # Scanning placeholders
        self._sc_tracks._val.setText("Scanning…")
        self._sc_duration._val.setText("…")
        self._sc_mediasize._val.setText("…")
        self._sc_artists._val.setText("…")
        self._sc_albums._val.setText("…")

        self._track_val.setText(f"Scanning… / {MAX_TRACK_LIMIT:,}")
        self._track_badge.setText("SCANNING")
        self._track_badge.setStyleSheet(
            f"background-color: {Colours.TEXT_TERTIARY}; color: white; "
            f"padding: 1px 8px; font-size: 10px; font-weight: bold;"
        )

    def populate_data(self, data_model) -> None:
        """Populate the drive info and breakdown based on the scanned data model."""
        tracks = list(data_model.tracks.values())
        count = len(tracks)

        # ── Stat cards ──────────────────────────────────────────
        self._sc_tracks._val.setText(f"{count:,}")

        total_dur = sum(t.duration_seconds for t in tracks)
        self._sc_duration._val.setText(_format_duration(total_dur))

        self._sc_mediasize._val.setText(_format_bytes(data_model.total_size_bytes))

        artists = {t.artist for t in tracks if t.artist and t.artist != "Unknown Artist"}
        self._sc_artists._val.setText(f"{len(artists):,}")

        albums = {t.album for t in tracks if t.album and t.album != "Unknown Album"}
        self._sc_albums._val.setText(f"{len(albums):,}")

        # ── Track limit ─────────────────────────────────────────
        self._track_val.setText(f"{count:,} / {MAX_TRACK_LIMIT:,}")
        if count > MAX_TRACK_LIMIT:
            self._track_badge.setText("OVER LIMIT")
            self._track_badge.setStyleSheet(
                f"background-color: {Colours.STATUS_UNSUPPORTED}; color: white; "
                f"padding: 1px 8px; font-size: 10px; font-weight: bold;"
            )
        else:
            self._track_badge.setText("OK")
            self._track_badge.setStyleSheet(
                f"background-color: {Colours.STATUS_SUPPORTED}; color: white; "
                f"padding: 1px 8px; font-size: 10px; font-weight: bold;"
            )

        # ── Format breakdown donut ──────────────────────────────
        ext_counts: dict[str, int] = {}
        for t in tracks:
            ext = t.extension.lower() or "unknown"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

        sorted_ext = dict(sorted(ext_counts.items(), key=lambda x: x[1], reverse=True))
        self._donut.set_data(sorted_ext, f"{count:,}", "tracks")

        self._clear_layout(self._legend_lyt)
        total_tracks = sum(sorted_ext.values())
        for i, (ext, val) in enumerate(sorted_ext.items()):
            color = self._donut._colors[i % len(self._donut._colors)]
            pct = (val / total_tracks * 100) if total_tracks > 0 else 0

            row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
            dot = QLabel(); dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background-color: {color.name()}; border-radius: 4px; border: none;")
            ext_l = QLabel(ext.upper()); ext_l.setFixedWidth(50)
            ext_l.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 11px; font-weight: 700; border: none;")
            cnt_l = QLabel(f"{val:,}")
            cnt_l.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 11px; border: none;")
            pct_l = QLabel(f"{pct:.0f}%"); pct_l.setAlignment(Qt.AlignRight)
            pct_l.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 11px; font-weight: 600; border: none;")
            row.addWidget(dot); row.addWidget(ext_l); row.addWidget(cnt_l)
            row.addStretch(); row.addWidget(pct_l)
            w = QWidget(); w.setStyleSheet("background: transparent; border: none;"); w.setLayout(row)
            self._legend_lyt.addWidget(w)

        # ── Compatibility summary (audio only, exclude .lrc) ────
        self._clear_layout(self._compat_lyt)
        audio_tracks = [t for t in tracks if t.extension.lower() != ".lrc"]
        audio_count = len(audio_tracks)
        status_counts = Counter(t.comp_status for t in audio_tracks)
        status_config = [
            ("SUPPORTED",   Colours.STATUS_SUPPORTED,   "Supported"),
            ("LIMITED",     Colours.STATUS_LIMITED,      "Limited"),
            ("UNSUPPORTED", Colours.STATUS_UNSUPPORTED,  "Unsupported"),
            ("UNKNOWN",     Colours.STATUS_UNKNOWN,      "Unknown"),
        ]
        for key, color, label in status_config:
            c = status_counts.get(key, 0)
            if c == 0:
                continue
            pct = (c / audio_count * 100) if audio_count else 0
            row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(6)
            dot = QLabel(); dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 4px; border: none;")
            lbl = QLabel(f"{label}: {c:,}")
            lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 12px; font-weight: 600; border: none;")
            plbl = QLabel(f"{pct:.0f}%"); plbl.setAlignment(Qt.AlignRight)
            plbl.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 12px; border: none;")
            row.addWidget(dot); row.addWidget(lbl); row.addStretch(); row.addWidget(plbl)
            w = QWidget(); w.setStyleSheet("background: transparent; border: none;"); w.setLayout(row)
            self._compat_lyt.addWidget(w)

        # Album art / lyrics counts
        art_count = sum(1 for t in tracks if t.has_album_art)
        lyr_count = sum(1 for t in tracks if t.has_lyrics)
        for label, val in [("Has Album Art", art_count), ("Has Lyrics", lyr_count)]:
            if val == 0:
                continue
            row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(6)
            ico = QLabel("●")
            ico.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 8px; border: none;")
            lbl = QLabel(f"{label}: {val:,}")
            lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px; border: none;")
            row.addWidget(ico); row.addWidget(lbl); row.addStretch()
            w = QWidget(); w.setStyleSheet("background: transparent; border: none;"); w.setLayout(row)
            self._compat_lyt.addWidget(w)

        # ── Top genres ──────────────────────────────────────────
        self._clear_layout(self._genres_lyt)
        genre_counts = Counter(t.genre for t in tracks if t.genre and t.genre.strip())
        top_genres = genre_counts.most_common(6)
        if not top_genres:
            lbl = QLabel("No genre tags found")
            lbl.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 12px; font-style: italic; border: none;")
            self._genres_lyt.addWidget(lbl)
        else:
            for genre, gc in top_genres:
                row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(6)
                lbl = QLabel(genre)
                lbl.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 12px; font-weight: 600; border: none;")
                cnt = QLabel(f"{gc:,}")
                cnt.setAlignment(Qt.AlignRight)
                cnt.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 12px; border: none;")
                row.addWidget(lbl); row.addStretch(); row.addWidget(cnt)
                w = QWidget(); w.setStyleSheet("background: transparent; border: none;"); w.setLayout(row)
                self._genres_lyt.addWidget(w)

        # ── Audio quality ───────────────────────────────────────
        self._clear_layout(self._quality_lyt)

        # Average bitrate
        bitrates = [t.bitrate_kbps for t in tracks if t.bitrate_kbps > 0]
        if bitrates:
            avg_br = sum(bitrates) / len(bitrates)
            self._add_quality_row("Avg Bitrate", f"{avg_br:.0f} kbps")

        # Sample rate distribution
        sr_counts = Counter(t.sample_rate_hz for t in tracks if t.sample_rate_hz > 0)
        top_sr = sr_counts.most_common(3)
        if top_sr:
            sr_str = ", ".join(f"{sr // 1000}kHz ({c})" for sr, c in top_sr)
            self._add_quality_row("Sample Rates", sr_str)

        # Channel distribution
        ch_counts = Counter(t.channels for t in tracks if t.channels > 0)
        if ch_counts:
            parts = []
            for ch, c in ch_counts.most_common(3):
                tag = "Mono" if ch == 1 else "Stereo" if ch == 2 else f"{ch}ch"
                parts.append(f"{tag} ({c})")
            self._add_quality_row("Channels", ", ".join(parts))

        # Lossless vs lossy breakdown
        lossless_exts = {".flac", ".fla", ".wav", ".ape", ".dff", ".dsf", ".m4a"}
        lossy_exts = {".mp3", ".mp1", ".mp2", ".wma", ".ogg", ".aac", ".3gp", ".mp4"}
        n_lossless = sum(1 for t in tracks if t.extension.lower() in lossless_exts)
        n_lossy = sum(1 for t in tracks if t.extension.lower() in lossy_exts)
        if n_lossless > 0 or n_lossy > 0:
            self._add_quality_row("Lossless / Lossy", f"{n_lossless:,} / {n_lossy:,}")

    def _add_quality_row(self, label: str, value: str):
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(6)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px; border: none;")
        val = QLabel(value)
        val.setAlignment(Qt.AlignRight)
        val.setStyleSheet(f"color: {Colours.TEXT_PRIMARY}; font-size: 12px; font-weight: 600; border: none;")
        row.addWidget(lbl); row.addStretch(); row.addWidget(val)
        w = QWidget(); w.setStyleSheet("background: transparent; border: none;"); w.setLayout(row)
        self._quality_lyt.addWidget(w)
