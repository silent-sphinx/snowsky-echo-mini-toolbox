"""
Centralised theme system for the Snowsky Echo Mini Toolbox.

Provides colour palette, QPalette configuration, and global QSS stylesheet
with a cool dark slate + teal accent aesthetic.
"""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


# ── Colour Palette ──────────────────────────────────────────────────────────

class Colours:
    """Central colour constants for the entire application."""

    # Backgrounds
    BG_DARKEST = "#131418"
    BG_DARK = "#18191E"
    BG_BASE = "#1E1F25"
    BG_SURFACE = "#24262D"
    BG_ELEVATED = "#2B2D35"
    BG_HOVER = "#353842"

    # Borders
    BORDER_SUBTLE = "#2E3039"
    BORDER_DEFAULT = "#3B3E49"
    BORDER_STRONG = "#4D515E"

    # Text
    TEXT_PRIMARY = "#ECEDF2"
    TEXT_SECONDARY = "#A4A8B8"
    TEXT_TERTIARY = "#737889"
    TEXT_DISABLED = "#4C505D"

    # Accent
    ACCENT = "#2DB8A6"
    ACCENT_HOVER = "#139687"
    ACCENT_MUTED = "#0F7A6E"
    ACCENT_BG = "#173B3A"

    # Status, one pair per semantic, reused for badges, cells, and stats
    STATUS_SUPPORTED = "#2F7A55"
    STATUS_SUPPORTED_TEXT = "#D4F5E3"
    STATUS_UNSUPPORTED = "#A83A42"
    STATUS_UNSUPPORTED_TEXT = "#FFDADD"
    STATUS_LIMITED = "#A8650F"
    STATUS_LIMITED_TEXT = "#FFEBCC"
    STATUS_UNKNOWN = "#4F5DAE"
    STATUS_UNKNOWN_TEXT = "#DDE1FF"
    STATUS_MISSING = "#A3346B"
    STATUS_MISSING_TEXT = "#FFD8EB"
    STATUS_SKIPPED = BORDER_DEFAULT
    STATUS_SKIPPED_TEXT = TEXT_PRIMARY

    # Compatibility aliases — same palette, universal names
    STATUS_COMPATIBLE = STATUS_SUPPORTED
    STATUS_COMPATIBLE_TEXT = STATUS_SUPPORTED_TEXT
    STATUS_INCOMPATIBLE = STATUS_UNSUPPORTED
    STATUS_INCOMPATIBLE_TEXT = STATUS_UNSUPPORTED_TEXT

    # Stat card colours
    STAT_TOTAL = TEXT_TERTIARY
    STAT_MISSING = STATUS_MISSING
    STAT_TITLE = STATUS_MISSING
    STAT_ARTIST = STATUS_MISSING
    STAT_ALBUM = STATUS_MISSING

    # Table
    TABLE_ALT_ROW = "#1C1D23"
    TABLE_SELECTION = ACCENT_BG
    TABLE_SELECTION_BORDER = ACCENT
    TABLE_HEADER_BG = "#15161B"
    TABLE_HEADER_BORDER = "#2A2C34"
    TABLE_GRIDLINE = "#272930"

    # Scrollbar
    SCROLLBAR_BG = BG_DARK
    SCROLLBAR_HANDLE = "#3A3D47"
    SCROLLBAR_HANDLE_HOVER = "#535767"


_STATUS_COLOURS: dict[str, tuple[str, str]] = {
    "COMPATIBLE": (Colours.STATUS_COMPATIBLE, Colours.STATUS_COMPATIBLE_TEXT),
    "COMPLETE": (Colours.STATUS_COMPATIBLE, Colours.STATUS_COMPATIBLE_TEXT),
    "SUPPORTED": (Colours.STATUS_SUPPORTED, Colours.STATUS_SUPPORTED_TEXT),
    "EQ COMPATIBLE": (Colours.STATUS_COMPATIBLE, Colours.STATUS_COMPATIBLE_TEXT),
    "LIMITED": (Colours.STATUS_LIMITED, Colours.STATUS_LIMITED_TEXT),
    "INCOMPATIBLE": (Colours.STATUS_INCOMPATIBLE, Colours.STATUS_INCOMPATIBLE_TEXT),
    "UNSUPPORTED": (Colours.STATUS_UNSUPPORTED, Colours.STATUS_UNSUPPORTED_TEXT),
    "NOT EQ COMPATIBLE": (Colours.STATUS_INCOMPATIBLE, Colours.STATUS_INCOMPATIBLE_TEXT),
    "UNKNOWN": (Colours.STATUS_UNKNOWN, Colours.STATUS_UNKNOWN_TEXT),
    "MISSING": (Colours.STATUS_MISSING, Colours.STATUS_MISSING_TEXT),
    "SKIPPED": (Colours.STATUS_SKIPPED, Colours.STATUS_SKIPPED_TEXT),
    "RENAME": (Colours.STATUS_INCOMPATIBLE, Colours.STATUS_INCOMPATIBLE_TEXT),
    "MATCHING": (Colours.STATUS_COMPATIBLE, Colours.STATUS_COMPATIBLE_TEXT),
    "CONFLICT": (Colours.STATUS_LIMITED, Colours.STATUS_LIMITED_TEXT),
    "AUDIO": (Colours.STATUS_SUPPORTED, Colours.STATUS_SUPPORTED_TEXT),
    "IMAGE": (Colours.STATUS_UNKNOWN, Colours.STATUS_UNKNOWN_TEXT),
    "VIDEO": (Colours.STATUS_LIMITED, Colours.STATUS_LIMITED_TEXT),
    "DOCUMENT": (Colours.STATUS_SKIPPED, Colours.STATUS_SKIPPED_TEXT),
    "ARCHIVE": (Colours.STATUS_LIMITED, Colours.STATUS_LIMITED_TEXT),
    "PLAYLIST": (Colours.STATUS_UNKNOWN, Colours.STATUS_UNKNOWN_TEXT),
    "SUBTITLE": (Colours.STATUS_MISSING, Colours.STATUS_MISSING_TEXT),
    "EXECUTABLE": (Colours.STATUS_UNSUPPORTED, Colours.STATUS_UNSUPPORTED_TEXT),
    "HIDDEN": (Colours.STATUS_INCOMPATIBLE, Colours.STATUS_INCOMPATIBLE_TEXT),
    "OTHER": (Colours.STATUS_SKIPPED, Colours.STATUS_SKIPPED_TEXT),
}


def colours_for_status(status: str | None) -> tuple[str, str] | tuple[None, None]:
    """Return (background, foreground) hex colours for a status token."""
    if status is None:
        return None, None
    try:
        token = str(status).strip().upper()
    except Exception:
        return None, None
    if not token:
        return None, None
    return _STATUS_COLOURS.get(token, (None, None))


# ── Font Setup ──────────────────────────────────────────────────────────────

_PREFERRED_UI_FONTS = (
    "Inter",
    "Segoe UI",          # Windows
    "Helvetica Neue",    # macOS
    "Helvetica",         # macOS
    "Noto Sans",         # Fedora / many Linux desktops
    "Ubuntu",            # Ubuntu
    "Cantarell",         # GNOME
    "DejaVu Sans",       # common Linux fallback
    "Liberation Sans",   # common Linux fallback
    "Arial",
)
_GENERIC_FONT_ALIASES = {
    "sans-serif",
    "sans serif",
    "serif",
    "monospace",
    "cursive",
    "fantasy",
}


def _is_real_font(name: str) -> bool:
    return bool(name) and name.casefold() not in _GENERIC_FONT_ALIASES and QFontDatabase.hasFamily(name)


def _first_installed_font(preferred: tuple[str, ...]) -> str:
    """Return an installed UI font. Never use CSS generics such as sans-serif."""
    for name in preferred:
        if _is_real_font(name):
            return name

    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    if _is_real_font(system):
        return system

    for name in QFontDatabase.families():
        if _is_real_font(name):
            return name

    return "Helvetica"


def ui_font_family() -> str:
    return f"'{_first_installed_font(_PREFERRED_UI_FONTS)}'"


def setup_fonts() -> QFont:
    """Set up the application font. Returns the configured QFont."""
    font = QFont(_first_installed_font(_PREFERRED_UI_FONTS))
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setPointSize(13)
    return font


# ── QPalette ────────────────────────────────────────────────────────────────

def create_palette() -> QPalette:
    """Create the dark slate QPalette."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(Colours.BG_BASE))
    p.setColor(QPalette.ColorRole.WindowText, QColor(Colours.TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.Base, QColor(Colours.BG_DARK))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(Colours.TABLE_ALT_ROW))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(Colours.BG_ELEVATED))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(Colours.TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.Text, QColor(Colours.TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.Button, QColor(Colours.BG_ELEVATED))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(Colours.TEXT_PRIMARY))
    p.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.Link, QColor(Colours.ACCENT))
    p.setColor(QPalette.ColorRole.Highlight, QColor(Colours.ACCENT_MUTED))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(Colours.TEXT_TERTIARY))
    # Bevel roles used by Fusion for QFrame lines and borders
    p.setColor(QPalette.ColorRole.Light, QColor(Colours.BORDER_SUBTLE))
    p.setColor(QPalette.ColorRole.Midlight, QColor(Colours.BORDER_SUBTLE))
    p.setColor(QPalette.ColorRole.Mid, QColor(Colours.BORDER_DEFAULT))
    p.setColor(QPalette.ColorRole.Dark, QColor(Colours.BORDER_SUBTLE))
    p.setColor(QPalette.ColorRole.Shadow, QColor(Colours.BG_DARKEST))
    return p


# ── Global QSS Stylesheet ──────────────────────────────────────────────────
import os

def global_stylesheet() -> str:
    """Return the global QSS stylesheet string."""
    tick_path = os.path.join(os.path.dirname(__file__), "assets", "tick.svg")
    # Qt requires forward slashes even on Windows for QSS URLs
    tick_path = tick_path.replace('\\', '/')
    
    return f"""
        /* ── Base ─────────────────────────────────────────── */
        * {{
            font-family: {ui_font_family()};
        }}

        QMainWindow {{
            background-color: {Colours.BG_BASE};
        }}

        QWidget {{
            color: {Colours.TEXT_PRIMARY};
        }}

        /* ── Tab Widget ───────────────────────────────────── */
        QTabWidget::pane {{
            border: 1px solid {Colours.BORDER_SUBTLE};
            border-radius: 0px;
            background-color: {Colours.BG_DARK};
            top: -1px;
        }}

        QTabBar {{
            background: transparent;
        }}

        QTabBar::tab {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_SECONDARY};
            border: 1px solid {Colours.BORDER_SUBTLE};
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            padding: 8px 20px;
            margin-right: 2px;
            font-weight: 500;
            font-size: 12px;
        }}

        QTabBar::tab:selected {{
            background-color: {Colours.BG_DARK};
            color: {Colours.TEXT_PRIMARY};
            border-top: 2px solid {Colours.ACCENT};
            border-bottom: 1px solid {Colours.BG_DARK}; /* Blends into pane */
            margin-bottom: -1px; /* Overlap pane border */
            padding-bottom: 9px; /* Keep height consistent */
            font-weight: 600;
        }}

        QTabBar::tab:hover:!selected {{
            background-color: {Colours.BG_HOVER};
            color: {Colours.TEXT_PRIMARY};
        }}

        /* ── Table View ───────────────────────────────────── */
        QTableView {{
            background-color: {Colours.BG_DARK};
            alternate-background-color: {Colours.TABLE_ALT_ROW};
            color: {Colours.TEXT_PRIMARY};
            gridline-color: {Colours.TABLE_GRIDLINE};
            border: 1px solid {Colours.BORDER_SUBTLE};
            border-radius: 0px;
            selection-background-color: {Colours.TABLE_SELECTION};
            selection-color: {Colours.TEXT_PRIMARY};
            outline: none;
            font-size: 12px;
        }}

        QTableView::item {{
            padding: 4px 8px;
            border: none;
        }}

        QTableView::item:selected {{
            background-color: {Colours.ACCENT_BG};
            border-left: 2px solid {Colours.TABLE_SELECTION_BORDER};
        }}

        QTableView::item:hover {{
            background-color: {Colours.BG_HOVER};
        }}

        /* ── List Widget ──────────────────────────────────── */
        QListWidget {{
            background-color: {Colours.BG_DARK};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_SUBTLE};
            outline: none;
            font-size: 12px;
        }}

        /* Do not pad ::item globally — custom item widgets (drive picker) get clipped. */
        QListWidget::item:selected {{
            background-color: {Colours.ACCENT_BG};
            color: {Colours.TEXT_PRIMARY};
        }}

        QListWidget::item:hover {{
            background-color: {Colours.BG_HOVER};
        }}
        
        /* ── Tree View ────────────────────────────────────── */
        QTreeView {{
            background-color: transparent;
            color: {Colours.TEXT_PRIMARY};
            border: none;
            outline: none;
        }}
        
        QTreeView::item {{
            padding: 4px 0px;
            border: none;
        }}
        
        QTreeView::item:selected {{
            background-color: {Colours.ACCENT_BG};
            /* Removed left border as it causes visual glitches on tree items */
        }}

        QTreeView::item:hover {{
            background-color: {Colours.BG_HOVER};
        }}
        
        QTreeView::indicator, QTableView::indicator {{
            width: 14px;
            height: 14px;
            border-radius: 0px;
            border: 1px solid {Colours.BORDER_DEFAULT};
            background-color: {Colours.BG_SURFACE};
            margin-right: 6px;
        }}

        QTreeView::indicator:checked, QTableView::indicator:checked {{
            background-color: {Colours.ACCENT};
            border-color: {Colours.ACCENT};
            image: url({tick_path});
        }}

        QTreeView::indicator:hover, QTableView::indicator:hover {{
            border-color: {Colours.BORDER_STRONG};
        }}

        /* ── Header View ──────────────────────────────────── */
        QHeaderView {{
            background-color: transparent;
        }}

        QHeaderView::section {{
            background-color: {Colours.TABLE_HEADER_BG};
            color: {Colours.TEXT_SECONDARY};
            border: none;
            border-bottom: 2px solid {Colours.BORDER_SUBTLE};
            border-right: 1px solid {Colours.TABLE_HEADER_BORDER};
            padding: 8px 10px;
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        QHeaderView::section:hover {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_PRIMARY};
        }}

        QHeaderView::section:pressed {{
            background-color: {Colours.BG_ELEVATED};
        }}

        QTableCornerButton::section {{
            background-color: {Colours.TABLE_HEADER_BG};
            border: none;
            border-bottom: 2px solid {Colours.BORDER_SUBTLE};
        }}

        /* ── Line Edit ────────────────────────────────────── */
        QLineEdit {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
            border-radius: 0px;
            padding: 7px 12px;
            font-size: 13px;
            selection-background-color: {Colours.ACCENT_MUTED};
        }}

        QLineEdit:focus {{
            border: 1px solid {Colours.ACCENT};
        }}

        QLineEdit:hover {{
            border: 1px solid {Colours.BORDER_STRONG};
        }}

        /* ── Push Button ──────────────────────────────────── */
        QPushButton {{
            background-color: {Colours.BG_ELEVATED};
            border: 1px solid {Colours.BORDER_DEFAULT};
            border-radius: 0px;
            color: {Colours.TEXT_PRIMARY};
            padding: 7px 16px;
            font-weight: 600;
            font-size: 12px;
        }}

        QPushButton:hover {{
            background-color: {Colours.BG_HOVER};
            border-color: {Colours.BORDER_STRONG};
        }}

        QPushButton:pressed {{
            background-color: {Colours.BG_SURFACE};
        }}

        QPushButton:disabled {{
            background-color: {Colours.BG_SURFACE};
            border-color: {Colours.BORDER_SUBTLE};
            color: {Colours.TEXT_DISABLED};
        }}

        QPushButton#accentButton {{
            background-color: {Colours.ACCENT_MUTED};
            border: 1px solid {Colours.ACCENT_HOVER};
            color: #FFFFFF;
        }}

        QPushButton#accentButton:hover {{
            background-color: {Colours.ACCENT_HOVER};
        }}

        QPushButton#accentButton:disabled {{
            background-color: {Colours.BG_SURFACE};
            border-color: {Colours.BORDER_SUBTLE};
            color: {Colours.TEXT_DISABLED};
        }}

        /* ── Combo Box ────────────────────────────────────── */
        QComboBox {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
            border-radius: 0px;
            padding: 6px 12px;
            font-size: 12px;
            min-width: 100px;
        }}

        QComboBox:hover {{
            border-color: {Colours.BORDER_STRONG};
        }}

        QComboBox:focus {{
            border-color: {Colours.ACCENT};
        }}

        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}

        QComboBox::down-arrow {{
            image: none;
            border: none;
        }}
        
        QComboBox::down-arrow:on {{ /* shift the arrow when popup is open */
            top: 1px;
            left: 1px;
        }}

        QComboBox QAbstractItemView {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
            selection-background-color: {Colours.ACCENT_MUTED};
            selection-color: #FFFFFF;
            outline: none;
            border-radius: 0px;
        }}

        QComboBox QAbstractItemView::item {{
            padding: 6px 12px;
        }}

        QComboBox QAbstractItemView::item:hover {{
            background-color: {Colours.BG_HOVER};
        }}

        /* ── Check Box ────────────────────────────────────── */
        QCheckBox {{
            color: {Colours.TEXT_SECONDARY};
            spacing: 8px;
            font-size: 12px;
        }}

        QCheckBox:hover {{
            color: {Colours.TEXT_PRIMARY};
        }}

        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 0px;
            border: 1px solid {Colours.BORDER_DEFAULT};
            background-color: {Colours.BG_SURFACE};
        }}

        QCheckBox::indicator:checked {{
            background-color: {Colours.ACCENT};
            border-color: {Colours.ACCENT};
            image: url({tick_path});
        }}

        QCheckBox::indicator:hover {{
            border-color: {Colours.ACCENT};
        }}

        /* ── Scrollbar ────────────────────────────────────── */
        QScrollBar:vertical {{
            background-color: {Colours.SCROLLBAR_BG};
            width: 10px;
            margin: 0;
            border: none;
            border-radius: 0px;
        }}

        QScrollBar::handle:vertical {{
            background-color: {Colours.SCROLLBAR_HANDLE};
            min-height: 30px;
            border-radius: 0px;
            margin: 2px;
        }}

        QScrollBar::handle:vertical:hover {{
            background-color: {Colours.SCROLLBAR_HANDLE_HOVER};
        }}

        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0px;
        }}

        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: none;
        }}

        QScrollBar:horizontal {{
            background-color: {Colours.SCROLLBAR_BG};
            height: 10px;
            margin: 0;
            border: none;
            border-radius: 0px;
        }}

        QScrollBar::handle:horizontal {{
            background-color: {Colours.SCROLLBAR_HANDLE};
            min-width: 30px;
            border-radius: 0px;
            margin: 2px;
        }}

        QScrollBar::handle:horizontal:hover {{
            background-color: {Colours.SCROLLBAR_HANDLE_HOVER};
        }}

        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}

        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            background: none;
        }}

        /* ── Progress Bar ─────────────────────────────────── */
        QProgressBar {{
            background-color: {Colours.BG_SURFACE};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_SUBTLE};
            border-radius: 0px;
            text-align: center;
            min-height: 20px;
            font-size: 11px;
        }}

        QProgressBar::chunk {{
            background-color: {Colours.ACCENT};
            border-radius: 0px;
        }}

        /* ── Tool Tip ─────────────────────────────────────── */
        QToolTip {{
            background-color: {Colours.BG_ELEVATED};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
            border-radius: 0px;
            padding: 6px 10px;
            font-size: 12px;
        }}

        /* ── Status Bar ───────────────────────────────────── */
        QStatusBar {{
            background-color: {Colours.BG_DARKEST};
            color: {Colours.TEXT_TERTIARY};
            border-top: 1px solid {Colours.BORDER_SUBTLE};
            font-size: 11px;
        }}

        QStatusBar QLabel {{
            color: {Colours.TEXT_TERTIARY};
            font-size: 11px;
        }}

        /* ── Message Box ──────────────────────────────────── */
        QMessageBox {{
            background-color: {Colours.BG_BASE};
        }}

        QMessageBox QLabel {{
            color: {Colours.TEXT_PRIMARY};
        }}

        /* ── Menu ─────────────────────────────────────────── */
        QMenu {{
            background-color: {Colours.BG_ELEVATED};
            color: {Colours.TEXT_PRIMARY};
            border: 1px solid {Colours.BORDER_DEFAULT};
            padding: 4px 0px;
        }}

        QMenu::item {{
            padding: 6px 32px 6px 24px;
        }}

        QMenu::item:selected {{
            background-color: {Colours.ACCENT_MUTED};
            color: #FFFFFF;
        }}

        QMenu::separator {{
            height: 1px;
            background-color: {Colours.BORDER_DEFAULT};
            margin: 4px 8px;
        }}

        /* ── Splitter ─────────────────────────────────────── */
        QSplitter::handle {{
            background-color: {Colours.BORDER_SUBTLE};
        }}

        QSplitter::handle:horizontal {{
            width: 1px;
        }}

        QSplitter::handle:vertical {{
            height: 1px;
        }}

        /* ── Labels ───────────────────────────────────────── */
        QLabel#sectionLabel {{
            color: {Colours.TEXT_PRIMARY};
            font-size: 15px;
            font-weight: 700;
        }}

        QLabel#subtitleLabel {{
            color: {Colours.TEXT_SECONDARY};
            font-size: 12px;
        }}

        QLabel#headerTitle {{
            color: {Colours.TEXT_PRIMARY};
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.3px;
        }}

        QLabel#headerSubtitle {{
            color: {Colours.TEXT_TERTIARY};
            font-size: 12px;
        }}

        QLabel#flowStepNumber {{
            color: {Colours.TEXT_PRIMARY};
            font-size: 18px;
            font-weight: 800;
        }}

        QLabel#flowStepTitle {{
            color: {Colours.TEXT_PRIMARY};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}

        QLabel#flowStepBody {{
            color: {Colours.TEXT_TERTIARY};
            font-size: 12px;
        }}

        QLabel#toolbarLabel {{
            color: {Colours.TEXT_SECONDARY};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}

        /* ── Panels ───────────────────────────────────────── */
        QWidget#panelSection {{
            background-color: {Colours.BG_SURFACE};
            border: 1px solid {Colours.BORDER_SUBTLE};
            border-radius: 0px;
        }}

        /* ── Frame separator ──────────────────────────────── */
        QFrame[frameShape="4"], QFrame[frameShape="5"] {{
            border: none;
            background-color: {Colours.BORDER_SUBTLE};
        }}

        QFrame[frameShape="4"] {{
            max-height: 1px;
        }}

        QFrame[frameShape="5"] {{
            max-width: 1px;
        }}

        QFrame#separator {{
            background-color: {Colours.BORDER_SUBTLE};
            max-height: 1px;
        }}
    """


# ── Apply Theme ─────────────────────────────────────────────────────────────

def apply_theme(app: QApplication) -> None:
    """Apply the full dark theme to the application."""
    QApplication.setStyle("Fusion")
    app.setPalette(create_palette())
    app.setFont(setup_fonts())
    app.setStyleSheet(global_stylesheet())
