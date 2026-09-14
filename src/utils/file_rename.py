"""Propose standardised filenames from track metadata.

Kept free of Qt imports so it can run from workers and table models.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..models.drive_data import TrackMetadata
from .tag_normalization import is_placeholder

SKIP_EXTENSIONS = {".lrc", ".cue"}

_TEMPLATE_TOKEN = re.compile(r"\{(track_no|artist|album|title)\}")
_FIELD_LABELS = {
    "track_no": "track number",
    "artist": "artist",
    "album": "album",
    "title": "title",
}


@dataclass(frozen=True)
class RenamePreset:
    id: str
    label: str
    template: str

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(_TEMPLATE_TOKEN.findall(self.template))


RENAME_PRESETS: tuple[RenamePreset, ...] = (
    RenamePreset("trackno_trackname", "01. Title", "{track_no}. {title}"),
    RenamePreset("trackno_artist_title", "01. Artist - Title", "{track_no}. {artist} - {title}"),
    RenamePreset("trackno_artist_album_title", "01. Artist - Album - Title", "{track_no}. {artist} - {album} - {title}"),
    RenamePreset("trackno_album_title", "01. Album - Title", "{track_no}. {album} - {title}"),
    RenamePreset("trackno_dash_title", "01 - Title", "{track_no} - {title}"),
    RenamePreset("trackno_dash_artist_title", "01 - Artist - Title", "{track_no} - {artist} - {title}"),
    RenamePreset("trackno_dash_artist_album_title", "01 - Artist - Album - Title", "{track_no} - {artist} - {album} - {title}"),
    RenamePreset("artist_title", "Artist - Title", "{artist} - {title}"),
    RenamePreset("artist_album_title", "Artist - Album - Title", "{artist} - {album} - {title}"),
    RenamePreset("album_title", "Album - Title", "{album} - {title}"),
    RenamePreset("artist_trackno_title", "Artist - 01. Title", "{artist} - {track_no}. {title}"),
    RenamePreset("title_only", "Title", "{title}"),
)

PRESET_BY_ID = {preset.id: preset for preset in RENAME_PRESETS}
PRESET_TRACKNO_TRACKNAME = RENAME_PRESETS[0].id
PRESET_LABELS = {preset.id: preset.label for preset in RENAME_PRESETS}

REASON_DIFFERS = "Name differs from preset"
REASON_CASE = "Metadata Title uses different name"
REASON_MATCHING = "Already matches preset"
REASON_DUPLICATE = "Duplicate suggested target name"
REASON_EXISTS = "Target name already exists"

_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DIGITS = re.compile(r"\d+")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def is_rename_track(track: TrackMetadata) -> bool:
    return track.extension.lower() not in SKIP_EXTENSIONS


def metadata_value_to_text(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "text"):
        try:
            return metadata_value_to_text(getattr(value, "text"))
        except Exception:
            pass
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "ignore").strip()
        except Exception:
            return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        first_value = value[0]
        if isinstance(first_value, tuple) and first_value:
            first_value = first_value[0]
        return metadata_value_to_text(first_value)
    return str(value).strip()


def extract_track_number(raw_value) -> str | None:
    text = metadata_value_to_text(raw_value)
    if not text:
        return None
    primary = text.split("/", 1)[0].strip()
    match = _DIGITS.search(primary or text)
    if not match:
        return None
    return match.group(0)


def format_track_number(track_number: str) -> str:
    parsed = extract_track_number(track_number)
    if not parsed:
        return track_number
    try:
        return f"{int(parsed):02d}"
    except Exception:
        return parsed


def extract_tag_text(raw_value) -> str | None:
    text = metadata_value_to_text(raw_value)
    if not text or is_placeholder(text):
        return None
    collapsed = " ".join(text.split()).strip()
    return collapsed or None


def extract_track_title(raw_value) -> str | None:
    return extract_tag_text(raw_value)


def _missing_reason(missing: list[str]) -> str:
    labels = [_FIELD_LABELS[key] for key in missing if key in _FIELD_LABELS]
    if not labels:
        return "Missing metadata"
    if len(labels) == 1:
        return f"Missing {labels[0]}"
    if len(labels) == 2:
        return f"Missing {labels[0]} and {labels[1]}"
    return "Missing " + ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _unsafe_reason(field: str) -> str:
    label = _FIELD_LABELS.get(field, field)
    return f"{label.capitalize()} cannot be used as a file name"


def resolve_preset(preset_id: str | None) -> RenamePreset:
    if preset_id and preset_id in PRESET_BY_ID:
        return PRESET_BY_ID[preset_id]
    return PRESET_BY_ID[PRESET_TRACKNO_TRACKNAME]


def safe_filename_component(name: str) -> str:
    cleaned = name.strip()
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", cleaned)
    if os.path.sep:
        cleaned = cleaned.replace(os.path.sep, "_")
    if os.path.altsep:
        cleaned = cleaned.replace(os.path.altsep, "_")
    cleaned = " ".join(cleaned.split()).strip().rstrip(" .")
    if not cleaned:
        return ""
    reserved_stem = cleaned.split(".", 1)[0].upper()
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES or reserved_stem in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def paths_are_same_file(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError:
        return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(
            os.path.normpath(str(second))
        )


def relative_display_path(path: str, root_path: str) -> str:
    try:
        return os.path.relpath(path, root_path)
    except Exception:
        return path


def suggested_name_for_track(
    track: TrackMetadata,
    preset_id: str | None = None,
) -> tuple[str | None, str, str, str | None]:
    """Return (suggested filename, track no, safe title, missing-reason)."""
    preset = resolve_preset(preset_id)
    track_no = extract_track_number(track.track_num)
    formatted_track_no = format_track_number(track_no) if track_no else ""
    title = extract_tag_text(track.title)
    artist = extract_tag_text(track.artist)
    album = extract_tag_text(track.album)

    raw_values = {
        "track_no": formatted_track_no or None,
        "artist": artist,
        "album": album,
        "title": title,
    }
    missing = [token for token in preset.tokens if not raw_values.get(token)]
    if missing:
        return None, formatted_track_no, title or "", _missing_reason(missing)

    safe_values: dict[str, str] = {}
    for token in preset.tokens:
        value = raw_values[token] or ""
        if token == "track_no":
            safe_values[token] = value
            continue
        safe = safe_filename_component(value)
        if not safe:
            return None, formatted_track_no, title or "", _unsafe_reason(token)
        safe_values[token] = safe

    stem = " ".join(preset.template.format(**safe_values).split()).strip()
    if not stem:
        return None, formatted_track_no, safe_values.get("title") or "", _missing_reason(list(preset.tokens))

    suffix = Path(track.filename).suffix or track.extension
    return f"{stem}{suffix}", formatted_track_no, safe_values.get("title") or title or "", None


def _apply_track_suggestion(track: TrackMetadata, suggested_name: str | None, track_no: str, title: str, missing_reason: str | None) -> None:
    track.rename_current = track.filename
    track.rename_track_no = track_no
    track.rename_title = title
    track.rename_suggested = suggested_name or ""

    if missing_reason:
        track.rename_status = "MISSING"
        track.rename_reason = missing_reason
        track.rename_is_checked = False
        return

    assert suggested_name is not None
    if suggested_name == track.filename:
        track.rename_status = "MATCHING"
        track.rename_reason = REASON_MATCHING
        track.rename_is_checked = False
    elif suggested_name.lower() == track.filename.lower():
        track.rename_status = "RENAME"
        track.rename_reason = REASON_CASE
        track.rename_is_checked = True
    else:
        track.rename_status = "RENAME"
        track.rename_reason = REASON_DIFFERS
        track.rename_is_checked = True


def apply_rename_evaluations(
    tracks: list[TrackMetadata],
    *,
    preset_id: str | None = None,
    reset_checks: bool = True,
) -> None:
    """Fill rename_* fields on audio tracks, including conflict detection."""
    candidates: list[tuple[TrackMetadata, Path]] = []
    target_counts: dict[str, int] = {}
    preset = resolve_preset(preset_id)

    for track in tracks:
        if not is_rename_track(track):
            continue

        previous_checked = track.rename_is_checked
        suggested_name, track_no, title, missing_reason = suggested_name_for_track(
            track, preset.id
        )
        _apply_track_suggestion(track, suggested_name, track_no, title, missing_reason)

        if not reset_checks:
            if track.rename_status == "RENAME":
                track.rename_is_checked = previous_checked
            else:
                track.rename_is_checked = False

        if track.rename_status != "RENAME" or not suggested_name:
            continue

        source_path = Path(track.filepath)
        target_path = source_path.with_name(suggested_name)
        candidates.append((track, target_path))
        key = str(target_path).lower()
        target_counts[key] = target_counts.get(key, 0) + 1

    vacating_keys = {
        str(Path(track.filepath)).lower()
        for track, target_path in candidates
        if str(target_path).lower() != str(Path(track.filepath)).lower()
    }

    for track, target_path in candidates:
        key = str(target_path).lower()
        conflict_reason = ""
        if target_counts.get(key, 0) > 1:
            conflict_reason = REASON_DUPLICATE
        elif target_path.exists():
            source_path = Path(track.filepath)
            if not paths_are_same_file(target_path, source_path) and key not in vacating_keys:
                conflict_reason = REASON_EXISTS

        if conflict_reason:
            track.rename_status = "CONFLICT"
            track.rename_reason = conflict_reason
            track.rename_is_checked = False


def rename_candidate_dict(track: TrackMetadata, root_path: str) -> dict[str, object]:
    source_path = Path(track.filepath)
    suggested = track.rename_suggested or track.filename
    target_path = source_path.with_name(suggested)
    return {
        "filepath": track.filepath,
        "relative_file": relative_display_path(track.filepath, root_path),
        "suggested_path": str(target_path),
    }
