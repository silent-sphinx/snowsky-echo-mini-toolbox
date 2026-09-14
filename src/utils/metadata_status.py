"""Core-tag completeness for the Metadata Browser."""

from __future__ import annotations

from ..models.drive_data import TrackMetadata
from .tag_normalization import is_placeholder

_SKIP_EXTENSIONS = {".lrc", ".cue"}


def is_metadata_track(track: TrackMetadata) -> bool:
    return track.extension.lower() not in _SKIP_EXTENSIONS


def is_missing_title(track: TrackMetadata) -> bool:
    return is_placeholder(track.title)


def is_missing_artist(track: TrackMetadata) -> bool:
    return is_placeholder(track.artist)


def is_missing_album(track: TrackMetadata) -> bool:
    return is_placeholder(track.album)


def missing_core_labels(track: TrackMetadata) -> list[str]:
    missing: list[str] = []
    if is_missing_title(track):
        missing.append("Title")
    if is_missing_artist(track):
        missing.append("Artist")
    if is_missing_album(track):
        missing.append("Album")
    return missing


def metadata_status(track: TrackMetadata) -> tuple[str, str]:
    missing = missing_core_labels(track)
    if not missing:
        return "COMPLETE", "All core tags present"
    return "MISSING", "Missing " + ", ".join(missing)
