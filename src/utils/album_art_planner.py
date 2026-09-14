"""Plan remediation actions for embedded album artwork."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..models.drive_data import TrackMetadata
from .album_art_validation import MAX_ART_DIMENSION


@dataclass
class ArtFixPlan:
    filepath: str
    relative_path: str
    title: str
    status: str
    issues: str
    actions: list[str]
    needs_reencode: bool
    needs_resize: bool

    @property
    def has_action(self) -> bool:
        return self.needs_reencode or self.needs_resize

    def to_candidate(self) -> dict[str, object]:
        return {
            "filepath": self.filepath,
            "relative_file": self.relative_path,
            "needs_reencode": self.needs_reencode,
            "needs_resize": self.needs_resize,
        }


def plan_art_fix_for_track(track: TrackMetadata, root_path: str) -> ArtFixPlan:
    status = (track.art_status or "UNKNOWN").strip().upper()

    needs_reencode = False
    needs_resize = False
    actions: list[str] = []

    if status == "INCOMPATIBLE":
        needs_reencode = (
            track.art_format_compat == "INCOMPATIBLE"
            or track.art_scan_compat == "INCOMPATIBLE"
        )
        needs_resize = track.art_resolution_compat == "INCOMPATIBLE"

    if needs_reencode:
        actions.append("Re-encode to baseline JPEG")
    if needs_resize:
        actions.append(f"Scale down to {MAX_ART_DIMENSION}x{MAX_ART_DIMENSION}")

    if not actions:
        if status == "MISSING":
            actions = ["No embedded artwork to convert"]
        elif status == "SKIPPED":
            actions = ["Skipped — file cannot carry artwork"]
        else:
            actions = ["No change — artwork already compatible"]

    try:
        relative_path = os.path.relpath(track.filepath, root_path)
    except Exception:
        relative_path = track.filepath

    return ArtFixPlan(
        filepath=track.filepath,
        relative_path=relative_path,
        title=track.title or track.filename,
        status=track.art_status or "UNKNOWN",
        issues=track.art_reason or "",
        actions=actions,
        needs_reencode=needs_reencode,
        needs_resize=needs_resize,
    )


def plan_art_fixes_for_tracks(tracks: list[TrackMetadata], root_path: str) -> list[ArtFixPlan]:
    return [plan_art_fix_for_track(track, root_path) for track in tracks]


def actionable_candidates(plans: list[ArtFixPlan]) -> list[dict[str, object]]:
    return [plan.to_candidate() for plan in plans if plan.has_action]


def apply_album_art_result(track: TrackMetadata, art_result: dict[str, str]) -> None:
    """Copy evaluate_album_art output onto a TrackMetadata instance."""
    track.art_status = art_result.get("status", "UNKNOWN")
    track.art_reason = art_result.get("reason", "")
    track.art_format = art_result.get("art_format", "-")
    track.art_scan_type = art_result.get("art_scan_type", "-")
    track.art_resolution = art_result.get("art_resolution", "-")
    track.art_size = art_result.get("art_size", "-")
    track.art_source = art_result.get("art_source", "-")
    track.art_format_compat = art_result.get("format_compatibility", "-")
    track.art_format_compat_reason = art_result.get("format_compatibility_reason", "-")
    track.art_scan_compat = art_result.get("scan_compatibility", "-")
    track.art_scan_compat_reason = art_result.get("scan_compatibility_reason", "-")
    track.art_resolution_compat = art_result.get("resolution_compatibility", "-")
    track.art_resolution_compat_reason = art_result.get("resolution_compatibility_reason", "-")
    track.art_metadata_status = art_result.get("metadata_status", "-")

    try:
        track.art_width = int(art_result.get("art_width", "0") or 0)
        track.art_height = int(art_result.get("art_height", "0") or 0)
    except (TypeError, ValueError):
        track.art_width = 0
        track.art_height = 0

    track.has_album_art = art_result.get("art_present") == "True"
