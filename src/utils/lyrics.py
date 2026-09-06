"""Embedded lyrics extraction, sidecar detection, and .lrc writing.

Kept free of Qt imports so the drive scanner can call it from worker threads.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..models.drive_data import TrackMetadata


def lyrics_value_to_text(value) -> str:
    """Normalise a mutagen lyrics value into plain text."""
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "ignore").strip()
        except Exception:
            return ""

    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], (str, bytes)):
        return lyrics_value_to_text(value[0])

    if hasattr(value, "text"):
        try:
            return lyrics_value_to_text(getattr(value, "text"))
        except Exception:
            pass

    if isinstance(value, (list, tuple, set)):
        parts = [lyrics_value_to_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()

    return str(value).strip()


def decode_text_file_bytes(data: bytes) -> str:
    """Decode an .lrc (or other text) file with a few common encodings."""
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def preview_lyrics(lyrics_text: str, max_length: int = 96) -> str:
    """Return the first meaningful line, truncated for table display."""
    if not lyrics_text:
        return ""

    for line in lyrics_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > max_length:
            return stripped[: max_length - 3] + "..."
        return stripped
    return ""


def lrc_sidecar_path(audio_path: Path) -> Path:
    """Return the device-required sidecar path: song.ext -> song.lrc."""
    return audio_path.with_suffix(".lrc")


def best_lrc_text_from_entries(entries: list[tuple[str, str]]) -> str:
    """Pick the most useful embedded lyrics block for a sidecar file."""
    if not entries:
        return ""

    def score(source: str) -> int:
        normalized = source.lower()
        if "uslt" in normalized:
            return 0
        if "unsyncedlyrics" in normalized or "tag lyrics" in normalized:
            return 1
        if "\xa9lyr" in normalized or "©lyr" in normalized:
            return 2
        if "sylt" in normalized:
            return 3
        return 4

    ordered = sorted(entries, key=lambda item: (score(item[0]), item[0].lower()))
    return ordered[0][1].replace("\r\n", "\n").replace("\r", "\n").strip()


def embedded_lyrics_entries(audio) -> list[tuple[str, str]]:
    """Collect labelled lyrics blocks from an already-opened mutagen file."""
    tags = getattr(audio, "tags", None) if audio else None
    if not tags:
        return []

    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_entry(source: str, text: str) -> None:
        normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
        if not normalized:
            return
        key = (source.lower(), normalized)
        if key in seen:
            return
        seen.add(key)
        entries.append((source, normalized))

    getall = getattr(tags, "getall", None)
    if callable(getall):
        try:
            uslt_frames = list(getall("USLT"))
        except Exception:
            uslt_frames = []

        for index, frame in enumerate(uslt_frames, start=1):
            text = lyrics_value_to_text(getattr(frame, "text", ""))
            lang = str(getattr(frame, "lang", "") or "").strip().upper()
            desc = str(getattr(frame, "desc", "") or "").strip()
            detail_parts = [part for part in (lang, desc) if part]
            label = "ID3 USLT"
            if detail_parts:
                label += f" ({', '.join(detail_parts)})"
            elif len(uslt_frames) > 1:
                label += f" #{index}"
            add_entry(label, text)

        try:
            sylt_frames = list(getall("SYLT"))
        except Exception:
            sylt_frames = []

        for index, frame in enumerate(sylt_frames, start=1):
            text = lyrics_value_to_text(getattr(frame, "text", ""))
            label = "ID3 SYLT"
            if len(sylt_frames) > 1:
                label += f" #{index}"
            add_entry(label, text)

    candidate_keys = ["lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS", "\xa9lyr", "©lyr"]
    for key in candidate_keys:
        try:
            raw_value = getall(key) if callable(getall) else tags.get(key)
        except Exception:
            raw_value = None
        text = lyrics_value_to_text(raw_value)
        if text:
            add_entry(f"Tag {key}", text)

    try:
        tag_keys = list(tags.keys())
    except Exception:
        tag_keys = []

    for key in tag_keys:
        key_text = str(key)
        normalized_key = key_text.lower()
        if "lyric" not in normalized_key or "lyricist" in normalized_key:
            continue
        try:
            raw_value = getall(key) if callable(getall) else tags.get(key)
        except Exception:
            raw_value = None
        text = lyrics_value_to_text(raw_value)
        if text:
            add_entry(f"Tag {key_text}", text)

    return entries


def extract_embedded_lyrics(filepath: Path) -> tuple[list[tuple[str, str]], str | None]:
    """Open a file and return (entries, error)."""
    try:
        import mutagen

        audio = mutagen.File(filepath)
    except Exception as exc:
        return [], str(exc)

    if not audio:
        return [], None

    return embedded_lyrics_entries(audio), None


def evaluate_lyrics_from_audio(filepath: Path, audio) -> dict[str, str]:
    """Build lyrics status fields from an opened mutagen file (or None)."""
    if filepath.suffix.lower() == ".lrc":
        return {
            "status": "SKIPPED",
            "reason": "This is an LRC sidecar file",
            "embedded": "-",
            "lrc_status": filepath.name,
            "source": "-",
            "preview": "",
            "has_lyrics": "False",
            "lyrics_text": "",
        }

    error = ""
    entries: list[tuple[str, str]] = []
    if audio is not None:
        try:
            entries = embedded_lyrics_entries(audio)
        except Exception as exc:
            error = str(exc)
    elif audio is None:
        error = "Unable to read audio tags"

    lyrics_text = best_lrc_text_from_entries(entries)
    has_lyrics = bool(lyrics_text)
    source = "-"
    if lyrics_text:
        for entry_source, entry_text in entries:
            if entry_text.replace("\r\n", "\n").replace("\r", "\n").strip() == lyrics_text:
                source = entry_source
                break

    lrc_path = lrc_sidecar_path(filepath)
    has_lrc = lrc_path.exists()
    lrc_name = lrc_path.name if has_lrc else "-"

    if has_lrc:
        status = "COMPATIBLE"
        reason = "Matching .lrc sidecar is present"
        if not lyrics_text:
            try:
                lyrics_text = decode_text_file_bytes(lrc_path.read_bytes()).strip()
            except Exception:
                pass
            if lyrics_text:
                source = "LRC sidecar"
    elif has_lyrics:
        status = "INCOMPATIBLE"
        reason = "Embedded lyrics found; the device needs a matching .lrc sidecar"
    elif error:
        status = "UNKNOWN"
        reason = error
    else:
        status = "MISSING"
        reason = "No embedded lyrics and no matching .lrc file"

    if error and not has_lyrics and not has_lrc:
        embedded_label = "Error"
    elif has_lyrics:
        embedded_label = "Yes"
    else:
        embedded_label = "No"

    return {
        "status": status,
        "reason": reason,
        "embedded": embedded_label,
        "lrc_status": lrc_name,
        "source": source,
        "preview": preview_lyrics(lyrics_text),
        "has_lyrics": "True" if has_lyrics else "False",
        "lyrics_text": lyrics_text,
    }


def evaluate_lyrics(filepath: Path) -> dict[str, str]:
    """Open a file and evaluate its lyrics status."""
    audio = None
    try:
        import mutagen

        audio = mutagen.File(filepath)
    except Exception:
        audio = None
    return evaluate_lyrics_from_audio(filepath, audio)


def apply_lyrics_result(track: TrackMetadata, result: dict[str, str]) -> None:
    """Copy evaluate_lyrics output onto a TrackMetadata instance."""
    track.lyrics_status = result.get("status", "UNKNOWN")
    track.lyrics_reason = result.get("reason", "")
    track.lyrics_embedded = result.get("embedded", "-")
    track.lyrics_lrc = result.get("lrc_status", "-")
    track.lyrics_source = result.get("source", "-")
    track.lyrics_preview = result.get("preview", "")
    track.has_lyrics = result.get("has_lyrics") == "True"
    lyrics_text = result.get("lyrics_text") or ""
    track.lyrics_text = lyrics_text or None


def lyrics_text_for_preview(track: TrackMetadata) -> str:
    """Best available lyrics text for a preview dialog."""
    if track.lyrics_text:
        return track.lyrics_text

    lrc_path = lrc_sidecar_path(Path(track.filepath))
    if lrc_path.exists():
        try:
            return decode_text_file_bytes(lrc_path.read_bytes())
        except Exception as exc:
            return f"Failed to read LRC file: {exc}"
    return ""


def _next_available_backup_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path

    stem = base_path.stem
    suffix = base_path.suffix
    parent = base_path.parent
    counter = 1
    while counter <= 10000:
        candidate = parent / f"{stem}.bak{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
    return base_path.with_name(f"{stem}.bak-{int(time.time())}{suffix}")


def backup_existing_lrc(
    lrc_path: Path,
    backup_root: Path,
    relative_file: str,
) -> None:
    """Copy an existing sidecar into the backup tree before overwriting."""
    if not lrc_path.exists():
        return

    relative_lrc = Path(relative_file).with_suffix(".lrc")
    backup_target = backup_root / relative_lrc
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    backup_target = _next_available_backup_path(backup_target)
    shutil.copy2(str(lrc_path), str(backup_target))


def write_lrc_sidecar(
    audio_path: Path,
    lyrics_text: str,
    *,
    dry_run: bool = False,
    backup_root: Path | None = None,
    relative_file: str = "",
) -> Path:
    """Write song.lrc next to the audio file. Returns the sidecar path."""
    text = lyrics_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("No lyrics text to write")
    if not text.endswith("\n"):
        text += "\n"

    lrc_path = lrc_sidecar_path(audio_path)
    if dry_run:
        return lrc_path

    if backup_root is not None and lrc_path.exists():
        backup_existing_lrc(lrc_path, backup_root, relative_file or audio_path.name)

    lrc_path.write_text(text, encoding="utf-8")
    return lrc_path
