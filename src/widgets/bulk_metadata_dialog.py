"""Dialog to apply or delete tag values across multiple selected files."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models.drive_data import TrackMetadata
from ..theme import Colours

_SKIP_EXTENSIONS = {".lrc", ".cue"}

# Tag key, row label, and the TrackMetadata attribute holding the current value.
_FIELDS = (
    ("title", "Title", "title"),
    ("artist", "Artist", "artist"),
    ("album", "Album", "album"),
    ("albumartist", "Album Artist", "album_artist"),
    ("genre", "Genre", "genre"),
    ("date", "Year", "year"),
)

_MAX_HINT_CHARS = 42


class BulkMetadataDialog(QDialog):
    def __init__(self, tracks: list[TrackMetadata], parent=None):
        super().__init__(parent)
        self._tracks = [
            track for track in tracks
            if track.extension.lower() not in _SKIP_EXTENSIONS
        ]
        self._labels: dict[str, str] = {key: label for key, label, _ in _FIELDS}
        self._edits: dict[str, QLineEdit] = {}
        self._clear_buttons: dict[str, QPushButton] = {}

        self.setObjectName("bulkMetadataDialog")
        self.setWindowTitle("Bulk Edit Metadata")
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(560)
        self.setStyleSheet(
            f"""
            QDialog#bulkMetadataDialog {{
                background-color: {Colours.BG_ELEVATED};
                color: {Colours.TEXT_PRIMARY};
                border: 1px solid {Colours.BORDER_DEFAULT};
            }}
            QDialog#bulkMetadataDialog QLabel {{
                background-color: transparent;
                color: {Colours.TEXT_PRIMARY};
            }}
            QWidget#fieldPanel {{
                background-color: {Colours.BG_SURFACE};
                border: 1px solid {Colours.BORDER_SUBTLE};
            }}
            QPushButton#clearToggle {{
                padding: 6px 14px;
            }}
            QPushButton#clearToggle:checked {{
                background-color: {Colours.STATUS_UNSUPPORTED};
                border-color: {Colours.STATUS_UNSUPPORTED};
                color: {Colours.STATUS_UNSUPPORTED_TEXT};
            }}
            """
        )
        self._init_ui()
        self._update_summary()

    # ── UI construction ─────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        song_count = len(self._tracks)
        title = QLabel(f"Bulk Edit Metadata — {song_count} Song{'s' if song_count != 1 else ''}")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Type a value to set it on every song, or use Clear to remove the tag. "
            "Empty fields are left untouched."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 13px;")
        layout.addWidget(subtitle)

        layout.addWidget(self._build_field_panel())

        self._summary_lbl = QLabel()
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(f"color: {Colours.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self._summary_lbl)

        separator = QFrame()
        separator.setObjectName("separator")
        separator.setFrameShape(QFrame.HLine)
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        button_row = QHBoxLayout()
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setToolTip("Clear every field back to 'leave unchanged'.")
        self._reset_btn.clicked.connect(self._on_reset)
        button_row.addWidget(self._reset_btn)
        button_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)

        self._apply_btn = QPushButton(f"Apply to {song_count} Song{'s' if song_count != 1 else ''}")
        self._apply_btn.setObjectName("accentButton")
        self._apply_btn.setMinimumHeight(34)
        self._apply_btn.clicked.connect(self._on_apply)
        button_row.addWidget(self._apply_btn)
        layout.addLayout(button_row)

    def _build_field_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("fieldPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)

        for index, (key, label, attribute) in enumerate(_FIELDS):
            row = index * 2

            label_widget = QLabel(label)
            label_widget.setStyleSheet("font-weight: 600; font-size: 13px;")
            grid.addWidget(label_widget, row, 0, Qt.AlignRight | Qt.AlignVCenter)

            edit = QLineEdit()
            edit.setMinimumHeight(30)
            edit.setPlaceholderText(self._placeholder_for(attribute))
            edit.textChanged.connect(self._update_summary)
            self._edits[key] = edit
            grid.addWidget(edit, row, 1)

            clear_btn = QPushButton("Clear")
            clear_btn.setObjectName("clearToggle")
            clear_btn.setCheckable(True)
            clear_btn.setMinimumHeight(30)
            clear_btn.setToolTip(f"Remove the {label} tag from every selected song.")
            clear_btn.toggled.connect(lambda checked, k=key: self._on_clear_toggled(k, checked))
            self._clear_buttons[key] = clear_btn
            grid.addWidget(clear_btn, row, 2)

            hint = QLabel(self._current_value_summary(attribute))
            hint.setStyleSheet(f"color: {Colours.TEXT_TERTIARY}; font-size: 11px;")
            grid.addWidget(hint, row + 1, 1, 1, 2)

        return panel

    # ── Current values across the selection ─────────────────────────

    def _values_for(self, attribute: str) -> list[str]:
        return [str(getattr(track, attribute, "") or "").strip() for track in self._tracks]

    def _common_value(self, attribute: str) -> str | None:
        """The single value shared by every song, or None when they differ."""
        unique = set(self._values_for(attribute))
        if len(unique) == 1:
            return unique.pop()
        return None

    def _placeholder_for(self, attribute: str) -> str:
        common = self._common_value(attribute)
        if common:
            return f"Keep “{_shorten(common)}”"
        return "Leave unchanged"

    def _current_value_summary(self, attribute: str) -> str:
        values = self._values_for(attribute)
        filled = [value for value in values if value]
        unique = sorted(set(filled))

        if not filled:
            return "Currently not set on any song"

        scope = (
            "on all songs" if len(filled) == len(values)
            else f"on {len(filled)} of {len(values)} songs"
        )

        if len(unique) == 1:
            return f"Currently “{_shorten(unique[0])}” {scope}"

        # Values that match apart from capitalisation read as "different" but
        # are usually a tagging slip the user wants to normalise.
        if len({value.casefold() for value in unique}) == 1:
            shown = ", ".join(f"“{_shorten(value)}”" for value in unique[:3])
            if len(unique) > 3:
                shown += f" +{len(unique) - 3} more"
            return f"Currently {shown} {scope} — same value, different capitalisation"

        return f"Currently {len(unique)} different values across {len(values)} songs"

    # ── Interaction ─────────────────────────────────────────────────

    def _on_clear_toggled(self, key: str, checked: bool) -> None:
        edit = self._edits[key]
        edit.setEnabled(not checked)
        if checked:
            edit.clear()
        self._update_summary()

    def _on_reset(self) -> None:
        for key, edit in self._edits.items():
            self._clear_buttons[key].setChecked(False)
            edit.clear()
        self._update_summary()

    def _update_summary(self) -> None:
        changes = self.tags_to_apply()
        if not changes:
            self._summary_lbl.setText("No changes yet. Every tag will be left as it is.")
            self._summary_lbl.setStyleSheet(
                f"color: {Colours.TEXT_TERTIARY}; font-size: 12px;"
            )
            self._apply_btn.setEnabled(False)
            return

        parts = []
        for key, value in changes.items():
            label = self._labels[key]
            if value is None:
                parts.append(f"{label} removed")
            else:
                parts.append(f"{label} → “{_shorten(value)}”")

        self._summary_lbl.setText(
            f"{len(parts)} change{'s' if len(parts) != 1 else ''}: " + ", ".join(parts)
        )
        self._summary_lbl.setStyleSheet(
            f"color: {Colours.TEXT_SECONDARY}; font-size: 12px;"
        )
        self._apply_btn.setEnabled(True)

    def _on_apply(self) -> None:
        if not self.tags_to_apply():
            return
        if not self._tracks:
            QMessageBox.warning(
                self,
                "No eligible files",
                "None of the selected files can store audio metadata.",
            )
            return
        self.accept()

    # ── Results ─────────────────────────────────────────────────────

    def tags_to_apply(self) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for key, edit in self._edits.items():
            if self._clear_buttons[key].isChecked():
                result[key] = None
            elif edit.text().strip():
                result[key] = edit.text().strip()
        return result

    def eligible_tracks(self) -> list[TrackMetadata]:
        return list(self._tracks)


def _shorten(value: str) -> str:
    if len(value) <= _MAX_HINT_CHARS:
        return value
    return value[: _MAX_HINT_CHARS - 1] + "…"
