"""Group tracks missing artwork into albums for a single cover lookup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..models.drive_data import TrackMetadata
from .album_art import supports_artwork_write
from .album_art_download import CoverCandidate
from .tag_normalization import (
    extract_year,
    is_disc_folder_name,
    normalize_album_key,
    normalize_artist_key,
    search_album_title,
    search_artist_name,
    tag_or_empty,
)

# Reasons a selected track cannot take part in a download.
SKIP_NO_ALBUM = "No album tag to search with"
SKIP_NOT_MISSING = "Artwork already present"
SKIP_UNSUPPORTED = "Format does not support embedded artwork"


@dataclass
class SkippedTrack:
    filepath: str
    relative_path: str
    reason: str


@dataclass
class AlbumGroup:
    """One album's worth of tracks that all need the same cover."""
    key: str
    album: str
    artist: str
    year: str
    tracks: list[TrackMetadata] = field(default_factory=list)
    folder: str = ""

    # Populated by the lookup worker and the review dialog.
    candidates: list[CoverCandidate] = field(default_factory=list)
    selected_index: int = 0
    error: str = ""
    query: str = ""
    is_selected: bool = True

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def filepaths(self) -> list[str]:
        return [track.filepath for track in self.tracks]

    @property
    def display_artist(self) -> str:
        return self.artist or "Unknown Artist"

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)

    @property
    def selected_candidate(self) -> CoverCandidate | None:
        if not self.candidates:
            return None
        index = max(0, min(self.selected_index, len(self.candidates) - 1))
        return self.candidates[index]

    @property
    def is_actionable(self) -> bool:
        return self.is_selected and self.selected_candidate is not None

    def relative_paths(self, root_path: str) -> list[str]:
        paths = []
        for track in self.tracks:
            try:
                paths.append(os.path.relpath(track.filepath, root_path))
            except Exception:
                paths.append(track.filepath)
        return paths

    def to_candidate(self, root_path: str) -> dict[str, object]:
        cover = self.selected_candidate
        return {
            "key": self.key,
            "album": self.album,
            "artist": self.artist,
            "image_url": cover.image_url if cover else "",
            "release_id": cover.release_id if cover else "",
            "filepaths": self.filepaths,
            "relative_files": self.relative_paths(root_path),
        }


def album_folder_for(filepath: str) -> str:
    """Directory that represents the album, collapsing disc subfolders."""
    parent = Path(filepath).parent
    if is_disc_folder_name(parent.name) and parent.parent != parent:
        parent = parent.parent
    return str(parent)


def _lookup_artist(track: TrackMetadata) -> str:
    """Prefer album artist so compilations resolve to one release."""
    return tag_or_empty(track.album_artist) or tag_or_empty(track.artist)


def _group_key(track: TrackMetadata) -> tuple[str, str]:
    """Stable grouping key plus a human-readable label."""
    album_key = normalize_album_key(track.album)
    artist_key = normalize_artist_key(tag_or_empty(track.album_artist))

    if artist_key:
        return f"aa::{artist_key}::{album_key}", "album-artist"

    # Without an album artist, tracks may legitimately differ per track
    # (compilations), so the containing album folder decides the grouping.
    folder = album_folder_for(track.filepath)
    if album_key:
        return f"fa::{folder.casefold()}::{album_key}", "folder-album"

    return f"ta::{normalize_artist_key(track.artist)}::{album_key}", "track-artist"


def build_album_groups(
    tracks: list[TrackMetadata],
    root_path: str,
) -> tuple[list[AlbumGroup], list[SkippedTrack]]:
    """Split selected tracks into album groups and non-actionable leftovers."""
    groups: dict[str, AlbumGroup] = {}
    skipped: list[SkippedTrack] = []

    def _relative(filepath: str) -> str:
        try:
            return os.path.relpath(filepath, root_path)
        except Exception:
            return filepath

    for track in tracks:
        if track.art_status != "MISSING":
            skipped.append(
                SkippedTrack(track.filepath, _relative(track.filepath), SKIP_NOT_MISSING)
            )
            continue

        album = tag_or_empty(track.album)
        if not album:
            skipped.append(
                SkippedTrack(track.filepath, _relative(track.filepath), SKIP_NO_ALBUM)
            )
            continue

        if not supports_artwork_write(Path(track.filepath)):
            skipped.append(
                SkippedTrack(track.filepath, _relative(track.filepath), SKIP_UNSUPPORTED)
            )
            continue

        key, _kind = _group_key(track)
        group = groups.get(key)
        if group is None:
            group = AlbumGroup(
                key=key,
                album=search_album_title(track.album) or album,
                artist=search_artist_name(_lookup_artist(track)),
                year=extract_year(track.year),
                folder=album_folder_for(track.filepath),
            )
            groups[key] = group

        group.tracks.append(track)
        if not group.year:
            group.year = extract_year(track.year)

    ordered = sorted(groups.values(), key=lambda g: (g.display_artist.casefold(), g.album.casefold()))
    for group in ordered:
        group.tracks.sort(key=lambda t: t.filepath)

    return ordered, skipped


def actionable_groups(groups: list[AlbumGroup]) -> list[AlbumGroup]:
    return [group for group in groups if group.is_actionable]


def download_candidates(groups: list[AlbumGroup], root_path: str) -> list[dict[str, object]]:
    return [group.to_candidate(root_path) for group in actionable_groups(groups)]
