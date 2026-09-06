"""Plan convert / lookup actions for lyrics sidecars."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..models.drive_data import TrackMetadata
from .lyrics import (
    apply_lyrics_result,
    best_lrc_text_from_entries,
    evaluate_lyrics,
    extract_embedded_lyrics,
    lrc_sidecar_path,
    preview_lyrics,
)
from .lyrics_lookup import LyricsLookupQuery, LyricsLookupResult


@dataclass
class LyricsConvertPlan:
    filepath: str
    relative_path: str
    title: str
    status: str
    issues: str
    action: str
    lyrics_text: str
    will_overwrite: bool

    @property
    def has_action(self) -> bool:
        return bool(self.lyrics_text.strip())

    def to_candidate(self) -> dict[str, object]:
        return {
            "filepath": self.filepath,
            "relative_file": self.relative_path,
            "lyrics_text": self.lyrics_text,
        }


def plan_convert_for_track(track: TrackMetadata, root_path: str) -> LyricsConvertPlan:
    lyrics_text = ""
    if track.has_lyrics:
        lyrics_text = (track.lyrics_text or "").strip()
        if not lyrics_text:
            entries, error = extract_embedded_lyrics(Path(track.filepath))
            if not error:
                lyrics_text = best_lrc_text_from_entries(entries)

    try:
        relative_path = os.path.relpath(track.filepath, root_path)
    except Exception:
        relative_path = track.filepath

    lrc_path = lrc_sidecar_path(Path(track.filepath))
    will_overwrite = lrc_path.exists()

    if lyrics_text:
        action = f"Overwrite {lrc_path.name}" if will_overwrite else f"Write {lrc_path.name}"
        issues = track.lyrics_reason or ""
    elif track.lyrics_embedded == "Error":
        action = "Skip — could not read lyrics"
        issues = track.lyrics_reason or "Unable to read embedded lyrics"
    else:
        action = "Skip — no embedded lyrics"
        issues = track.lyrics_reason or "No embedded lyrics to convert"

    return LyricsConvertPlan(
        filepath=track.filepath,
        relative_path=relative_path,
        title=track.title or track.filename,
        status=track.lyrics_status or "UNKNOWN",
        issues=issues,
        action=action,
        lyrics_text=lyrics_text,
        will_overwrite=will_overwrite,
    )


def plan_converts_for_tracks(
    tracks: list[TrackMetadata], root_path: str
) -> list[LyricsConvertPlan]:
    return [plan_convert_for_track(track, root_path) for track in tracks]


def actionable_convert_candidates(plans: list[LyricsConvertPlan]) -> list[dict[str, object]]:
    return [plan.to_candidate() for plan in plans if plan.has_action]


def apply_lyrics_evaluation(track: TrackMetadata) -> None:
    apply_lyrics_result(track, evaluate_lyrics(Path(track.filepath)))


def result_from_lookup(
    track: TrackMetadata,
    root_path: str,
    query: LyricsLookupQuery,
    status: str,
    source: str,
    lyrics_text: str,
) -> LyricsLookupResult:
    try:
        relative_path = os.path.relpath(track.filepath, root_path)
    except Exception:
        relative_path = track.filepath

    can_apply = status == "Found" and bool(lyrics_text.strip())
    return LyricsLookupResult(
        filepath=track.filepath,
        relative_path=relative_path,
        title=query.title,
        artist=query.artist,
        album=query.album,
        status=status,
        source=source,
        preview=preview_lyrics(lyrics_text),
        lyrics_text=lyrics_text,
        apply_status="Ready" if can_apply else "-",
        is_selected=can_apply,
        error="" if can_apply else source,
    )


def lookup_candidates_from_results(results: list[LyricsLookupResult]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for result in results:
        if not result.is_selected or not result.can_apply:
            continue
        candidates.append(
            {
                "filepath": result.filepath,
                "relative_file": result.relative_path,
                "lyrics_text": result.lyrics_text,
            }
        )
    return candidates
