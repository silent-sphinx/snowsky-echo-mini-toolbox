"""Embedded album art inspection and compatibility rules.

This module must stay free of Qt imports: it runs inside the drive scanner's
thread-pool workers.
"""

from __future__ import annotations

import base64
import binascii
import logging
from pathlib import Path

import mutagen
from mutagen._vorbis import VComment
from mutagen.apev2 import APEv2
from mutagen.asf import ASFTags
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

from .tag_normalization import first_easy_tag

logger = logging.getLogger(__name__)

MAX_ART_DIMENSION = 1000

# Extensions that cannot carry embedded artwork.
NON_ARTWORK_EXTENSIONS = {".lrc", ".cue"}

VORBIS_PICTURE_KEY = "metadata_block_picture"
VORBIS_LEGACY_KEYS = ("coverart", "coverartmime")
# Matches the key string in the device firmware; APEv2 keys are case-insensitive.
APE_COVER_KEY = "Cover Art (front)"
ASF_PICTURE_KEY = "WM/Picture"

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


def sniff_image_mime(data: bytes) -> str:
    """Identify an image by its leading bytes rather than its tag label."""
    if not data:
        return "image/unknown"
    if data.startswith(b"\xFF\xD8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/unknown"


def parse_asf_picture(raw: bytes) -> tuple[bytes, str] | None:
    """Decode a WM/Picture blob: type, size, UTF-16 mime, UTF-16 desc, data."""
    if len(raw) < 5:
        return None
    size = int.from_bytes(raw[1:5], "little")
    index = 5
    strings = []
    for _ in range(2):
        end = index
        while end + 1 < len(raw) and raw[end:end + 2] != b"\x00\x00":
            end += 2
        if end + 1 >= len(raw):
            return None
        strings.append(raw[index:end].decode("utf-16-le", errors="replace"))
        index = end + 2
    data = raw[index:index + size]
    return (data, strings[0] or sniff_image_mime(data)) if data else None


def build_asf_picture(data: bytes, mime: str = "image/jpeg", picture_type: int = 3) -> bytes:
    """Encode a WM/Picture blob for ASF/WMA tags."""
    return (
        bytes([picture_type])
        + len(data).to_bytes(4, "little")
        + mime.encode("utf-16-le") + b"\x00\x00"
        + b"\x00\x00"
        + data
    )


def _vorbis_picture(tags) -> tuple[bytes, str] | None:
    for encoded in tags.get(VORBIS_PICTURE_KEY) or []:
        try:
            picture = Picture(base64.b64decode(encoded))
        except (binascii.Error, ValueError, TypeError, mutagen.MutagenError):
            continue
        if picture.data:
            return bytes(picture.data), picture.mime or sniff_image_mime(picture.data)

    for encoded in tags.get("coverart") or []:
        try:
            data = base64.b64decode(encoded)
        except (binascii.Error, ValueError):
            continue
        if data:
            mimes = tags.get("coverartmime") or []
            return data, (mimes[0] if mimes else "") or sniff_image_mime(data)
    return None


def _ape_picture(tags) -> tuple[bytes, str] | None:
    item = tags.get(APE_COVER_KEY)
    if item is None:
        return None
    raw = bytes(getattr(item, "value", b"") or b"")
    # The value is "<filename>\0<image bytes>".
    _name, sep, data = raw.partition(b"\x00")
    data = data if sep else raw
    return (data, sniff_image_mime(data)) if data else None


def _asf_picture(tags) -> tuple[bytes, str] | None:
    for attribute in tags.get(ASF_PICTURE_KEY) or []:
        parsed = parse_asf_picture(bytes(getattr(attribute, "value", b"") or b""))
        if parsed:
            return parsed
    return None


def _tag_picture(opened, tags) -> tuple[bytes, str] | None:
    """Read artwork from Vorbis, APEv2 or ASF tag containers."""
    # FLAC players read picture blocks, not pictures stored as Vorbis comments.
    if isinstance(tags, VComment) and not isinstance(opened, FLAC):
        return _vorbis_picture(tags)
    if isinstance(tags, APEv2):
        return _ape_picture(tags)
    if isinstance(tags, ASFTags):
        return _asf_picture(tags)
    return None


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


def _load_audio(path: Path, audio=None):
    if audio is not None:
        return audio, None
    try:
        return mutagen.File(path), None
    except Exception as exc:
        return None, exc


def read_embedded_album_art(path: Path, audio=None) -> tuple[bytes | None, str]:
    opened, error = _load_audio(path, audio)
    if error is not None:
        return None, f"Tag read failed: {error}"

    if not opened:
        return None, "No embedded art"

    if isinstance(opened, FLAC):
        pictures = getattr(opened, "pictures", [])
        if pictures:
            picture = pictures[0]
            mime = getattr(picture, "mime", "image/unknown") or "image/unknown"
            return bytes(picture.data), mime

    tags = getattr(opened, "tags", None)
    if tags is None:
        return None, "No embedded art"

    try:
        apic_frames = tags.getall("APIC")
        if apic_frames:
            frame = apic_frames[0]
            mime = getattr(frame, "mime", "image/unknown") or "image/unknown"
            return bytes(frame.data), mime
    except (AttributeError, TypeError, ValueError):
        logger.debug("Failed reading ID3 APIC album art from %s", path, exc_info=True)

    if isinstance(opened, MP4):
        covr = tags.get("covr")
        if covr:
            cover = covr[0]
            mime = "image/unknown"
            if isinstance(cover, MP4Cover):
                if cover.imageformat == MP4Cover.FORMAT_JPEG:
                    mime = "image/jpeg"
                elif cover.imageformat == MP4Cover.FORMAT_PNG:
                    mime = "image/png"
            return bytes(cover), mime

    found = _tag_picture(opened, tags)
    if found:
        return found

    return None, "No embedded art"


def _art_source(path: Path, audio=None) -> str:
    """Identify which tag container holds the embedded picture."""
    opened, _error = _load_audio(path, audio)
    if opened is None:
        return "-"

    if isinstance(opened, FLAC) and getattr(opened, "pictures", []):
        return "FLAC Picture"

    tags = getattr(opened, "tags", None)
    if tags is not None:
        try:
            if tags.getall("APIC"):
                return "ID3 APIC"
        except (AttributeError, TypeError, ValueError):
            pass

    if isinstance(opened, MP4) and tags is not None and tags.get("covr"):
        return "MP4 covr"

    if _tag_picture(opened, tags):
        if isinstance(tags, VComment):
            return "Vorbis Picture"
        if isinstance(tags, APEv2):
            return "APE Cover Art"
        return "WM/Picture"

    return "-"


def _metadata_status(path: Path, audio=None) -> str:
    """Report whether artist/album tags exist for artwork lookups."""
    opened = audio
    if opened is None:
        try:
            opened = mutagen.File(path, easy=True)
        except Exception:
            return "Tag Read Error"

    if not opened:
        return "-"

    try:
        artist = first_easy_tag(opened, "artist", "albumartist")
        album = first_easy_tag(opened, "album")
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


def evaluate_album_art(path: Path, audio=None) -> dict[str, str]:
    """Evaluate a file's embedded artwork against the device requirements."""
    result = _base_result()

    if path.suffix.lower() in NON_ARTWORK_EXTENSIONS:
        result["status"] = "SKIPPED"
        result["reason"] = f"Skipped non-artwork extension: {path.suffix.lower()}"
        return result

    opened, error = _load_audio(path, audio)
    if error is not None:
        result["status"] = "SKIPPED"
        result["reason"] = f"Tag read failed: {error}"
        return result

    if opened is None:
        result["status"] = "SKIPPED"
        result["reason"] = "Unsupported or unreadable audio file"
        return result

    result["metadata_status"] = _metadata_status(path, opened)

    art_bytes, art_mime = read_embedded_album_art(path, opened)

    if not art_bytes:
        result["status"] = "MISSING"
        result["reason"] = "No embedded artwork"
        return result

    result["art_present"] = "True"
    result["art_size"] = _format_size(len(art_bytes))
    result["art_source"] = _art_source(path, opened)

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

    actual_mime = sniff_image_mime(art_bytes)
    if mime == "image/jpeg" and actual_mime == "image/jpeg":
        result["format_compatibility"] = "COMPATIBLE"
        result["format_compatibility_reason"] = "JPEG artwork"
    elif mime == "image/jpeg":
        result["format_compatibility"] = "INCOMPATIBLE"
        result["format_compatibility_reason"] = (
            f"Labelled image/jpeg but the data is {actual_mime}"
        )
        issues.append(f"Artwork data is {actual_mime}, not JPEG")
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
