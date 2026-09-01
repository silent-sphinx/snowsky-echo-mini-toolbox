"""Embedded album art inspection and compatibility rules.

This module must stay free of Qt imports: it runs inside the drive scanner's
ProcessPoolExecutor workers.
"""

from __future__ import annotations

import logging
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover

logger = logging.getLogger(__name__)

MAX_ART_DIMENSION = 1000

# Extensions that cannot carry embedded artwork.
NON_ARTWORK_EXTENSIONS = {".lrc", ".cue"}

_PROGRESSIVE_SOF_MARKERS = {0xC2, 0xC6, 0xCA, 0xCE}
_BASELINE_SOF_MARKERS = {0xC0, 0xC1, 0xC3, 0xC5, 0xC7, 0xC9, 0xCB, 0xCD, 0xCF}


def is_progressive_jpeg(data: bytes) -> bool:
    """Detect if a JPEG image uses a progressive scan."""
    return jpeg_scan_type(data) == "Progressive"


def jpeg_scan_type(data: bytes) -> str:
    """Classify a JPEG as Progressive, Non-progressive, Unknown or Not JPEG."""
    if not data or not data.startswith(b"\xFF\xD8"):
        return "Not JPEG"

    index = 2
    while index + 1 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue

        marker = data[index + 1]
        index += 2

        # Standalone markers carry no payload.
        if marker in (0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue

        if marker in _PROGRESSIVE_SOF_MARKERS:
            return "Progressive"
        if marker in _BASELINE_SOF_MARKERS:
            return "Non-progressive"

        if index + 1 >= len(data):
            break
        segment_len = int.from_bytes(data[index : index + 2], "big")
        if segment_len < 2:
            break
        index += segment_len

    return "Unknown"


def image_size_from_bytes(data: bytes) -> tuple[int, int] | None:
    if not data or len(data) < 10:
        return None

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")

    if data.startswith(b"BM") and len(data) >= 26:
        return int.from_bytes(data[18:22], "little"), int.from_bytes(data[22:26], "little")

    if data.startswith(b"\xFF\xD8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                return width, height
            if index + 1 < len(data):
                segment_len = int.from_bytes(data[index : index + 2], "big")
                index += segment_len
            else:
                break

    return None


def read_embedded_album_art(path: Path) -> tuple[bytes | None, str]:
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        return None, f"Tag read failed: {exc}"

    if not audio or not getattr(audio, "tags", None):
        return None, "No embedded art"

    if isinstance(audio, FLAC):
        pictures = getattr(audio, "pictures", [])
        if pictures:
            picture = pictures[0]
            mime = getattr(picture, "mime", "image/unknown") or "image/unknown"
            return bytes(picture.data), mime

    try:
        apic_frames = audio.tags.getall("APIC")
        if apic_frames:
            frame = apic_frames[0]
            mime = getattr(frame, "mime", "image/unknown") or "image/unknown"
            return bytes(frame.data), mime
    except (AttributeError, TypeError, ValueError):
        logger.debug("Failed reading ID3 APIC album art from %s", path, exc_info=True)

    if isinstance(audio, MP4):
        covr = audio.tags.get("covr")
        if covr:
            cover = covr[0]
            mime = "image/unknown"
            if isinstance(cover, MP4Cover):
                if cover.imageformat == MP4Cover.FORMAT_JPEG:
                    mime = "image/jpeg"
                elif cover.imageformat == MP4Cover.FORMAT_PNG:
                    mime = "image/png"
            return bytes(cover), mime

    return None, "No embedded art"


def _art_source(path: Path) -> str:
    """Identify which tag container holds the embedded picture."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return "-"

    if audio is None:
        return "-"

    if isinstance(audio, FLAC) and getattr(audio, "pictures", []):
        return "FLAC Picture"

    tags = getattr(audio, "tags", None)
    if tags is not None:
        try:
            if tags.getall("APIC"):
                return "ID3 APIC"
        except (AttributeError, TypeError, ValueError):
            pass

    if isinstance(audio, MP4) and tags is not None and tags.get("covr"):
        return "MP4 covr"

    return "-"


def _metadata_status(path: Path) -> str:
    """Report whether artist/album tags exist for artwork lookups."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return "Tag Read Error"

    if not audio:
        return "-"

    try:
        artist = (audio.get("artist", [""]) or [""])[0]
        album = (audio.get("album", [""]) or [""])[0]
    except Exception:
        return "Tag Read Error"

    if not artist and not album:
        return "Missing Artist/Album"
    if not artist:
        return "Missing Artist"
    if not album:
        return "Missing Album"
    return "OK"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def _base_result() -> dict[str, str]:
    return {
        "status": "UNKNOWN",
        "reason": "",
        "art_present": "False",
        "art_format": "-",
        "art_scan_type": "-",
        "art_resolution": "-",
        "art_width": "0",
        "art_height": "0",
        "art_size": "-",
        "art_source": "-",
        "format_compatibility": "-",
        "format_compatibility_reason": "-",
        "scan_compatibility": "-",
        "scan_compatibility_reason": "-",
        "resolution_compatibility": "-",
        "resolution_compatibility_reason": "-",
        "metadata_status": "-",
    }


def evaluate_album_art(path: Path) -> dict[str, str]:
    """Evaluate a file's embedded artwork against the device requirements."""
    result = _base_result()

    if path.suffix.lower() in NON_ARTWORK_EXTENSIONS:
        result["status"] = "SKIPPED"
        result["reason"] = f"Skipped non-artwork extension: {path.suffix.lower()}"
        return result

    try:
        audio = mutagen.File(path)
    except Exception as exc:
        result["status"] = "SKIPPED"
        result["reason"] = f"Tag read failed: {exc}"
        return result

    if audio is None:
        result["status"] = "SKIPPED"
        result["reason"] = "Unsupported or unreadable audio file"
        return result

    result["metadata_status"] = _metadata_status(path)

    art_bytes, art_mime = read_embedded_album_art(path)

    if not art_bytes:
        result["status"] = "MISSING"
        result["reason"] = "No embedded artwork"
        return result

    result["art_present"] = "True"
    result["art_size"] = _format_size(len(art_bytes))
    result["art_source"] = _art_source(path)

    mime = (art_mime or "").lower()
    if "/" in art_mime:
        result["art_format"] = art_mime.split("/")[-1].upper()
    else:
        result["art_format"] = art_mime.upper() or "-"

    dims = image_size_from_bytes(art_bytes)
    if dims:
        result["art_width"] = str(dims[0])
        result["art_height"] = str(dims[1])
        result["art_resolution"] = f"{dims[0]} x {dims[1]}"

    issues: list[str] = []

    if mime == "image/jpeg":
        result["format_compatibility"] = "COMPATIBLE"
        result["format_compatibility_reason"] = "JPEG artwork"
    else:
        result["format_compatibility"] = "INCOMPATIBLE"
        result["format_compatibility_reason"] = (
            f"Artwork must be JPEG (image/jpeg); found {art_mime}"
        )
        issues.append(f"Unsupported artwork format: {art_mime}")

    scan_type = jpeg_scan_type(art_bytes)
    result["art_scan_type"] = scan_type
    if scan_type == "Progressive":
        result["scan_compatibility"] = "INCOMPATIBLE"
        result["scan_compatibility_reason"] = "Progressive JPEGs will not display"
        issues.append("Progressive JPEG")
    elif scan_type == "Non-progressive":
        result["scan_compatibility"] = "COMPATIBLE"
        result["scan_compatibility_reason"] = "Baseline (non-progressive) JPEG"
    else:
        result["scan_compatibility"] = "UNKNOWN"
        result["scan_compatibility_reason"] = f"Could not determine JPEG scan type ({scan_type})"

    if dims:
        if dims[0] > MAX_ART_DIMENSION or dims[1] > MAX_ART_DIMENSION:
            result["resolution_compatibility"] = "INCOMPATIBLE"
            result["resolution_compatibility_reason"] = (
                f"Resolution must be {MAX_ART_DIMENSION}x{MAX_ART_DIMENSION} or lower"
            )
            issues.append(f"Resolution {dims[0]} x {dims[1]} exceeds {MAX_ART_DIMENSION}px")
        else:
            result["resolution_compatibility"] = "COMPATIBLE"
            result["resolution_compatibility_reason"] = (
                f"Within {MAX_ART_DIMENSION}x{MAX_ART_DIMENSION}"
            )
    else:
        result["resolution_compatibility"] = "UNKNOWN"
        result["resolution_compatibility_reason"] = "Could not read image dimensions"

    if issues:
        result["status"] = "INCOMPATIBLE"
        result["reason"] = "; ".join(issues)
    else:
        result["status"] = "COMPATIBLE"
        result["reason"] = "Artwork meets device requirements"

    return result
