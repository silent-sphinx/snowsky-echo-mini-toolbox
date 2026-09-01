"""Plan remediation actions for music compatibility conversion."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..models.drive_data import TrackMetadata


@dataclass
class ConversionPlan:
    filepath: str
    relative_path: str
    title: str
    status: str
    issues: str
    actions: list[str]
    should_convert: bool
    needs_sanitize: bool
    needs_downmix: bool
    sample_rate: str
    bit_depth: str

    @property
    def has_action(self) -> bool:
        return self.should_convert or self.needs_sanitize

    def to_candidate(self) -> dict[str, object]:
        return {
            "filepath": self.filepath,
            "relative_file": self.relative_path,
            "sample_rate": self.sample_rate if self.sample_rate != "-" else "",
            "bit_depth": self.bit_depth if self.bit_depth != "-" else "",
            "should_convert": self.should_convert,
            "needs_sanitize": self.needs_sanitize,
            "needs_downmix": self.needs_downmix,
        }


def plan_conversion_for_track(
    track: TrackMetadata,
    root_path: str,
    *,
    make_eq_compatible: bool,
) -> ConversionPlan:
    status = (track.comp_status or "").strip().upper()
    eq_text = (track.comp_eq or "").strip().lower()
    channel_incompatible = track.comp_channel_compat == "INCOMPATIBLE"

    should_convert = False
    needs_sanitize = False
    needs_downmix = False
    actions: list[str] = []

    if status == "UNSUPPORTED":
        should_convert = True

    if status == "LIMITED":
        needs_sanitize = True

    eq_actionable = {"not eq compatible"}
    if make_eq_compatible:
        eq_actionable.add("unknown")

    if make_eq_compatible and eq_text in eq_actionable:
        should_convert = True

    if should_convert:
        if make_eq_compatible:
            actions.append("Convert to EQ-compatible FLAC (≤16-bit / ≤192 kHz)")
        else:
            actions.append("Convert to FLAC (≤24-bit / ≤192 kHz)")

    if should_convert and channel_incompatible:
        needs_downmix = True
        actions.append("Downmix to stereo")

    if needs_sanitize:
        actions.append("Sanitize metadata (strip non-core tags)")

    if not actions:
        actions = ["No change — already compatible"]

    try:
        relative_path = os.path.relpath(track.filepath, root_path)
    except Exception:
        relative_path = track.filepath

    return ConversionPlan(
        filepath=track.filepath,
        relative_path=relative_path,
        title=track.title or track.filename,
        status=track.comp_status or "UNKNOWN",
        issues=track.comp_reason or "",
        actions=actions,
        should_convert=should_convert,
        needs_sanitize=needs_sanitize,
        needs_downmix=needs_downmix,
        sample_rate=track.comp_sample_rate or "-",
        bit_depth=track.comp_bit_depth or "-",
    )


def plan_conversions_for_tracks(
    tracks: list[TrackMetadata],
    root_path: str,
    *,
    make_eq_compatible: bool,
) -> list[ConversionPlan]:
    return [
        plan_conversion_for_track(track, root_path, make_eq_compatible=make_eq_compatible)
        for track in tracks
    ]


def actionable_candidates(plans: list[ConversionPlan]) -> list[dict[str, object]]:
    return [plan.to_candidate() for plan in plans if plan.has_action]


def apply_compatibility_result(track: TrackMetadata, comp_result: dict[str, str]) -> None:
    """Copy evaluate_music_file output onto a TrackMetadata instance."""
    track.comp_status = comp_result.get("status", "UNKNOWN")
    track.comp_category = comp_result.get("category", "unknown")
    track.comp_reason = comp_result.get("reason", "")
    track.comp_eq = comp_result.get("eq_compatibility", "-")
    track.comp_codec = comp_result.get("codec", "-")
    track.comp_sample_rate = comp_result.get("sample_rate", "-")
    track.comp_bit_depth = comp_result.get("bit_depth", "-")
    track.comp_block_size = comp_result.get("block_size", "-")
    track.comp_dsd_profile = comp_result.get("dsd_profile", "-")
    track.comp_channels = comp_result.get("channels", "-")
    track.comp_streams = comp_result.get("stream_count", "-")
    track.comp_filename = comp_result.get("filename_compatibility", "-")
    track.comp_filename_reason = comp_result.get("filename_compatibility_reason", "-")
    track.comp_metadata = comp_result.get("metadata_compatibility", "-")
    track.comp_metadata_reason = comp_result.get("metadata_compatibility_reason", "-")
    track.comp_channel_compat = comp_result.get("channel_compatibility", "-")
    track.comp_channel_compat_reason = comp_result.get("channel_compatibility_reason", "-")
    track.comp_wav_codec = comp_result.get("wav_codec_compatibility", "-")
    track.comp_wav_codec_reason = comp_result.get("wav_codec_compatibility_reason", "-")
    track.comp_dsd_bitdepth = comp_result.get("dsd_bitdepth_compatibility", "-")
    track.comp_dsd_bitdepth_reason = comp_result.get("dsd_bitdepth_compatibility_reason", "-")
    track.comp_tag_encoding = comp_result.get("tag_encoding_compatibility", "-")
    track.comp_tag_encoding_reason = comp_result.get("tag_encoding_compatibility_reason", "-")
    track.comp_tag_length = comp_result.get("tag_length_compatibility", "-")
    track.comp_tag_length_reason = comp_result.get("tag_length_compatibility_reason", "-")
