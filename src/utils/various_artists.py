"""Find artist tags that are a generic "various artists" placeholder."""

from __future__ import annotations

import re

from ..models.drive_data import TrackMetadata
from .file_rename import is_rename_track

DEFAULT_TARGET = "Various Artists"

FIELDS_BOTH = "both"
FIELDS_ARTIST = "artist"
FIELDS_ALBUM_ARTIST = "albumartist"

FIELD_CHOICES = {
    FIELDS_BOTH: "Artist and Album Artist",
    FIELDS_ARTIST: "Artist only",
    FIELDS_ALBUM_ARTIST: "Album Artist only",
}

_FIELD_KEYS = {
    FIELDS_BOTH: ("artist", "albumartist"),
    FIELDS_ARTIST: ("artist",),
    FIELDS_ALBUM_ARTIST: ("albumartist",),
}

# Compared after normalise_artist(), so "V.A." and "various  artists" also match.
VARIOUS_ARTIST_VARIANTS = frozenset({
    "various",
    "various artist",
    "various artists",
    "multiple artists",
    "va",
})

_WHITESPACE = re.compile(r"\s+")


def normalise_artist(value: str | None) -> str:
    if not value:
        return ""
    return _WHITESPACE.sub(" ", value.replace(".", "")).strip().casefold()


def is_various_artist(value: str | None) -> bool:
    return normalise_artist(value) in VARIOUS_ARTIST_VARIANTS


def _current_value(track: TrackMetadata, key: str) -> str:
    return track.artist if key == "artist" else track.album_artist


def tags_to_normalise(track: TrackMetadata, fields: str, target: str) -> dict[str, str]:
    """Tags on this track that hold a Various Artists variant other than `target` exactly."""
    if not is_rename_track(track):
        return {}
    return {
        key: target
        for key in _FIELD_KEYS[fields]
        if is_various_artist(_current_value(track, key))
        and _current_value(track, key) != target
    }
