import os
import json
import re
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
import logging


LOSSY_FORMATS = {".mp3", ".ogg", ".m4a", ".wma"}
PCM_FORMATS = {".wav", ".flac", ".ape"}
DSD_FORMATS = {".dsf", ".dff"}
EXPLICIT_UNSUPPORTED = {".dts", ".dtshd", ".sacd", ".iso"}
OTHER_AUDIO_FORMATS = {
    ".aac",
    ".aif",
    ".aifc",
    ".aiff",
    ".alac",
    ".m4b",
    ".m4p",
    ".mka",
    ".mp1",
    ".mp2",
    ".opus",
    ".oga",
    ".wv",
}
KNOWN_AUDIO_FORMATS = (
    LOSSY_FORMATS | PCM_FORMATS | DSD_FORMATS | EXPLICIT_UNSUPPORTED | OTHER_AUDIO_FORMATS
)

FLAC_BLOCK_MAX_LIMIT = 4608
SUPPORTED_DSD_MULTIPLES = {64, 128, 256}

_SCAN_RESULT_CACHE_LIMIT = 20000
_SCAN_RESULT_CACHE: OrderedDict[str, tuple[int, int, dict[str, str]]] = OrderedDict()


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _positive_int_or_none(value: int | None) -> int | None:
    if value is None or value <= 0:
        return None
    return value


def _first_valid_int(*values) -> int | None:
    for value in values:
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed
    return None


def _extract_flac_max_block_size(stream: dict, codec_name: str | None) -> int | None:
    """Extract FLAC max block size from ffprobe stream keys across versions."""
    if codec_name != "flac":
        return None

    # Different ffprobe builds expose this using different key names.
    max_block_size = _first_valid_int(
        stream.get("max_block_size"),
        stream.get("max_blocksize"),
        stream.get("max_samples_per_frame"),
    )
    return _positive_int_or_none(max_block_size)


def _read_flac_streaminfo_block_max_size(path: Path) -> int | None:
    """Read FLAC STREAMINFO max block size directly from file metadata."""
    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"fLaC":
                return None

            while True:
                header = handle.read(4)
                if len(header) < 4:
                    return None

                is_last = bool(header[0] & 0x80)
                block_type = header[0] & 0x7F
                block_length = int.from_bytes(header[1:4], "big")

                if block_type == 0:  # STREAMINFO
                    payload = handle.read(block_length)
                    if len(payload) < 4:
                        return None
                    max_block_size = int.from_bytes(payload[2:4], "big")
                    return _positive_int_or_none(max_block_size)

                # Skip non-STREAMINFO metadata blocks.
                handle.seek(block_length, os.SEEK_CUR)
                if is_last:
                    return None
    except Exception:
        return None


def _resolve_ffprobe_executable() -> str | None:
    ffprobe_path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if ffprobe_path:
        return ffprobe_path

    repo_root = Path(__file__).resolve().parent.parent
    local_candidates = [
        repo_root / "build_assets" / "ffprobe",
        repo_root / "build_assets" / "ffprobe.exe",
    ]
    for candidate in local_candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def _subprocess_no_window_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}

    kwargs: dict[str, object] = {}

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is not None:
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs


def _infer_bit_depth_from_sample_fmt(sample_fmt: str | None) -> int | None:
    if not sample_fmt:
        return None

    value = str(sample_fmt).strip().lower()
    if not value:
        return None

    if value.startswith("flt"):
        return 32
    if value.startswith("dbl"):
        return 64

    match = re.search(r"(\d+)", value)
    if not match:
        return None

    return _positive_int_or_none(_safe_int(match.group(1)))


def _ffprobe_audio_info(path: Path) -> dict[str, int | None | str] | None:
    """Extract comprehensive audio metadata using ffprobe.
    
    Returns dict with:
      - sample_rate: int or None
      - bit_depth: int or None  
      - codec_name: str or None
      - channels: int or None
      - bitrate: int or None
      - duration: float or None
      - format_long_name: str or None
      - max_block_size: int or None (for FLAC)
      - tags: dict of metadata tags
    """
    logger = logging.getLogger(__name__)

    ffprobe_executable = _resolve_ffprobe_executable()
    if ffprobe_executable is None:
        return {"error": "ffprobe not found"}

    cmd = [
        ffprobe_executable,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,sample_fmt,bits_per_sample,bits_per_raw_sample,max_block_size,max_blocksize,max_samples_per_frame,codec_name,channels,bit_rate,"
        "duration,codec_long_name",
        "-show_entries",
        "format=duration,bit_rate,format_long_name",
        "-show_entries",
        "stream_tags",
        "-show_entries",
        "format_tags",
        "-of",
        "json",
        str(path),
    ]

    # Try a couple of times for transient failures (timeouts, intermittent I/O)
    last_err: str | None = None
    for attempt in range(2):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                **_subprocess_no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            last_err = f"ffprobe timeout: {exc}"
            logger.debug("ffprobe timeout for %s (attempt %d): %s", path, attempt + 1, exc)
            continue
        except Exception as exc:
            last_err = f"ffprobe invocation failed: {exc}"
            logger.debug("ffprobe invocation failed for %s (attempt %d): %s", path, attempt + 1, exc)
            continue

        if result.returncode != 0:
            stderr_text = (result.stderr or "").strip()
            last_err = f"ffprobe returned non-zero exit code {result.returncode}: {stderr_text}"
            logger.debug("ffprobe stderr for %s (attempt %d): %s", path, attempt + 1, stderr_text)
            continue

        try:
            payload = json.loads(result.stdout)
        except Exception as exc:
            last_err = f"failed to parse ffprobe JSON output: {exc}"
            logger.debug("ffprobe JSON parse failed for %s: %s", path, exc)
            continue

        # Success
        break
    else:
        return {"error": last_err or "ffprobe failed"}

    # Extract stream information
    streams = payload.get("streams") or []
    if not streams:
        return {"error": "no streams found in ffprobe output"}

    stream = streams[0]
    
    # Extract basic audio properties
    sample_rate = _positive_int_or_none(_safe_int(stream.get("sample_rate")))
    channels = _positive_int_or_none(_safe_int(stream.get("channels")))
    
    # Bit depth extraction with fallback logic
    bit_depth = _first_valid_int(
        stream.get("bits_per_raw_sample"),
        stream.get("bits_per_sample"),
    )
    bit_depth = _positive_int_or_none(bit_depth)
    if bit_depth is None:
        bit_depth = _infer_bit_depth_from_sample_fmt(stream.get("sample_fmt"))
    
    codec_name = str(stream.get("codec_name", "")).strip().lower() or None
    codec_long_name = str(stream.get("codec_long_name", "")).strip() or None
    max_block_size = _extract_flac_max_block_size(stream, codec_name)
    if max_block_size is None and path.suffix.lower() == ".flac":
        max_block_size = _read_flac_streaminfo_block_max_size(path)
    
    # Duration and bitrate from stream first, then format
    duration = None
    try:
        stream_duration = float(stream.get("duration", 0))
        if stream_duration > 0:
            duration = stream_duration
    except (ValueError, TypeError):
        pass
    
    if duration is None:
        format_info = payload.get("format") or {}
        try:
            format_duration = float(format_info.get("duration", 0))
            if format_duration > 0:
                duration = format_duration
        except (ValueError, TypeError):
            pass
    
    bit_rate = None
    try:
        stream_bitrate = int(stream.get("bit_rate", 0))
        if stream_bitrate > 0:
            bit_rate = stream_bitrate
    except (ValueError, TypeError):
        pass
    
    if bit_rate is None:
        format_info = payload.get("format") or {}
        try:
            format_bitrate = int(format_info.get("bit_rate", 0))
            if format_bitrate > 0:
                bit_rate = format_bitrate
        except (ValueError, TypeError):
            pass
    
    format_long_name = str(payload.get("format", {}).get("format_long_name", "")).strip() or None
    
    # Extract tags from both stream and format level
    tags = {}
    stream_tags = stream.get("tags") or {}
    format_tags = payload.get("format", {}).get("tags") or {}
    
    if stream_tags:
        tags.update({k.lower(): v for k, v in stream_tags.items()})
    if format_tags:
        tags.update({k.lower(): v for k, v in format_tags.items()})

    return {
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "flac_block_max": max_block_size,
        "codec_name": codec_name,
        "codec_long_name": codec_long_name,
        "channels": channels,
        "bitrate": bit_rate,
        "duration": duration,
        "format_long_name": format_long_name,
        "tags": tags,
    }


def _map_dsd_multiple(sample_rate: int) -> int | None:
    bases = [44100, 48000]
    best: tuple[int, float] | None = None
    for base in bases:
        ratio = sample_rate / float(base)
        nearest = int(round(ratio))
        diff = abs(ratio - nearest)
        if diff <= 0.5:
            if best is None or diff < best[1]:
                best = (nearest, diff)
    return best[0] if best else None


def _get_audio_codec(path: Path) -> str | None:
    """Extract the actual audio codec name from the file using ffprobe."""
    ffprobe_info = _ffprobe_audio_info(path)
    if not ffprobe_info:
        return None
    if isinstance(ffprobe_info, dict) and ffprobe_info.get("error"):
        return None
    return ffprobe_info.get("codec_name")


def _read_audio_metadata(path: Path) -> dict[str, int | None | str]:
    """Read audio metadata using ffprobe exclusively."""
    ffprobe_info = _ffprobe_audio_info(path)
    if ffprobe_info is None:
        return {
            "sample_rate": None,
            "bit_depth": None,
            "flac_block_max": None,
            "codec_name": None,
        }

    # If ffprobe reported an error, return that information for higher-level
    # callers to surface a more informative reason instead of failing silently.
    if isinstance(ffprobe_info, dict) and ffprobe_info.get("error"):
        return {
            "sample_rate": None,
            "bit_depth": None,
            "flac_block_max": None,
            "codec_name": None,
            "ffprobe_error": ffprobe_info.get("error"),
        }

    return {
        "sample_rate": ffprobe_info.get("sample_rate"),
        "bit_depth": ffprobe_info.get("bit_depth"),
        "flac_block_max": ffprobe_info.get("flac_block_max"),
        "codec_name": ffprobe_info.get("codec_name"),
    }


def _short_missing_metadata_reason(missing_fields: list[str]) -> str:
    if len(missing_fields) >= 2:
        return "Missing required metadata"
    if "sample rate" in missing_fields:
        return "Missing sample rate"
    if "bit depth" in missing_fields:
        return "Missing bit depth"
    return "Missing metadata"


def _relative_path_for_target(path: Path, target_dir: Path) -> str:
    try:
        return path.relative_to(target_dir).as_posix()
    except Exception:
        return str(path)


def _validate_codec_for_container(extension: str, codec_name: str | None) -> tuple[bool, str]:
    """Validate that the audio codec matches the container format expectations.
    
    Returns:
        (is_valid, reason_if_invalid)
    """
    if codec_name is None:
        return True, ""  # Can't validate if codec unknown
    
    # M4A/M4B/M4P containers should only have AAC, ALAC, or related codecs
    if extension in {".m4a", ".m4b", ".m4p"}:
        codec_lower = codec_name.lower().strip()
        
        # Valid codec names for M4A containers
        valid_m4a_codecs = {
            "aac", "aac_lc", "he-aac", "aac-lc", "he_aac",
            "alac",  # Apple Lossless Audio Codec
            "ac-3", "ec-3", "dts", "dts-hd",  # Sometimes found in M4A containers
        }
        
        # MP4 audio object type codes (returned by ffprobe for M4A files)
        # mp4a.40.2 = AAC-LC Low Complexity (most common)
        # mp4a.40.x / m4a.40.x = AAC family codecs
        # mp4a.66.x / m4a.66.x = AAC-LC SBR (Spectral Band Replication)
        # mp4a.67.x / m4a.67.x = AAC-LC PS (Parametric Stereo)
        # mp4a.68.x / m4a.68.x = AAC-LC PS Enhanced
        if codec_lower.startswith(("mp4a.", "m4a.")):
            # MP4 audio object type code - these are valid AAC variants
            return True, ""
        
        if codec_lower not in valid_m4a_codecs:
            return False, f"M4A container has incorrect codec '{codec_name}' (expected AAC or ALAC)"
    
    return True, ""



def _evaluate_music_file_with_cache(path: Path, target_dir: Path) -> dict[str, str]:
    cache_key = str(path)
    signature: tuple[int, int] | None = None

    try:
        stat_info = path.stat()
        signature = (int(stat_info.st_mtime_ns), int(stat_info.st_size))
    except Exception:
        signature = None

    if signature is not None:
        cached = _SCAN_RESULT_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature[0] and cached[1] == signature[1]:
            result = dict(cached[2])
            result["file"] = _relative_path_for_target(path, target_dir)
            _SCAN_RESULT_CACHE.move_to_end(cache_key)
            return result

    result = evaluate_music_file(path, target_dir)

    if signature is not None:
        cache_payload = dict(result)
        # Store absolute key path in cache so file text can be regenerated per target root.
        cache_payload["file"] = cache_key
        _SCAN_RESULT_CACHE[cache_key] = (signature[0], signature[1], cache_payload)
        _SCAN_RESULT_CACHE.move_to_end(cache_key)
        while len(_SCAN_RESULT_CACHE) > _SCAN_RESULT_CACHE_LIMIT:
            _SCAN_RESULT_CACHE.popitem(last=False)

    return result


def evaluate_music_file(path: Path, target_dir: Path) -> dict[str, str]:
    relative_path = _relative_path_for_target(path, target_dir)

    extension = path.suffix.lower()
    extension_display = extension[1:] if extension else "(none)"
    status = "SKIPPED"
    category = "skipped"
    reason = "Non-audio file type"
    sample_rate_text = "-"
    bit_depth_text = "-"
    block_size_text = "N/A"
    dsd_profile = "-"
    codec_text = "-"
    eq_sample_rate = None
    eq_bit_depth = None

    if path.name.startswith("."):
        reason = "Hidden dot-file ignored by compatibility rules"
        return {
            "file": relative_path,
            "extension": extension_display,
            "codec": codec_text,
            "status": status,
            "category": category,
            "reason": reason,
            "sample_rate": sample_rate_text,
            "bit_depth": bit_depth_text,
            "block_size": block_size_text,
            "dsd_profile": dsd_profile,
            "eq_compatibility": "N/A",
        }

    if not extension:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = "No file extension; cannot classify codec reliably"
    elif extension not in KNOWN_AUDIO_FORMATS:
        status = "SKIPPED"
        category = "skipped"
        reason = f"Skipped non-audio extension: {extension}"
    elif extension in EXPLICIT_UNSUPPORTED:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = f"Explicitly unsupported format: {extension}"
    elif extension in LOSSY_FORMATS:
        metadata = _read_audio_metadata(path)
        ffprobe_error = metadata.get("ffprobe_error")
        sample_rate = metadata.get("sample_rate")
        bit_depth = metadata.get("bit_depth")
        codec_name = metadata.get("codec_name")
        eq_sample_rate = sample_rate
        eq_bit_depth = bit_depth

        codec_text = codec_name if codec_name else "-"
        if ffprobe_error:
            status = "UNKNOWN"
            category = "unknown"
            reason = f"ffprobe error: {ffprobe_error}"
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "N/A"
        
        # If ffprobe failed, surface that and skip further validation
        if ffprobe_error:
            pass
        else:
            # Validate codec matches container format
            codec_valid, codec_error = _validate_codec_for_container(extension, codec_name)
            if not codec_valid:
                status = "UNSUPPORTED"
                category = "unsupported"
                reason = codec_error
            else:
                status = "SUPPORTED"
                category = "supported"
                reason = "Lossy format supported"
    elif extension in PCM_FORMATS:
        metadata = _read_audio_metadata(path)
        ffprobe_error = metadata.get("ffprobe_error")
        sample_rate = metadata.get("sample_rate")
        bit_depth = metadata.get("bit_depth")
        flac_block_max = metadata.get("flac_block_max")
        codec_name = metadata.get("codec_name")
        eq_sample_rate = sample_rate
        eq_bit_depth = bit_depth

        codec_text = codec_name if codec_name else "-"
        if ffprobe_error:
            status = "UNKNOWN"
            category = "unknown"
            reason = f"ffprobe error: {ffprobe_error}"
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "-"
        if extension == ".flac":
            block_size_text = str(flac_block_max) if flac_block_max is not None else "-"
        else:
            block_size_text = "N/A"

        # If ffprobe failed, surface that and skip deeper PCM validation.
        if ffprobe_error:
            pass
        else:
            missing_pcm_fields: list[str] = []
            if sample_rate is None:
                missing_pcm_fields.append("sample rate")
            if bit_depth is None:
                missing_pcm_fields.append("bit depth")

            if missing_pcm_fields:
                status = "UNKNOWN"
                category = "unknown"
                reason = _short_missing_metadata_reason(missing_pcm_fields)
            elif extension == ".flac" and flac_block_max is None:
                status = "UNKNOWN"
                category = "unknown"
                reason = "Missing FLAC block size"
            elif extension == ".flac" and flac_block_max > FLAC_BLOCK_MAX_LIMIT:
                status = "UNSUPPORTED"
                category = "unsupported"
                reason = f"FLAC block size {flac_block_max} exceeds limit {FLAC_BLOCK_MAX_LIMIT}"
            elif sample_rate > 192000 or bit_depth > 24:
                status = "UNSUPPORTED"
                category = "unsupported"
                reason_parts: list[str] = []
                if sample_rate > 192000:
                    reason_parts.append(f"sample rate {sample_rate} > 192000 Hz")
                if bit_depth > 24:
                    reason_parts.append(f"bit depth {bit_depth} > 24-bit")
                reason = "Exceeds PCM limits: " + ", ".join(reason_parts)
            else:
                status = "SUPPORTED"
                category = "supported"
                reason = "Within PCM limits"
    elif extension in DSD_FORMATS:
        metadata = _read_audio_metadata(path)
        ffprobe_error = metadata.get("ffprobe_error")
        sample_rate = metadata.get("sample_rate")
        codec_name = metadata.get("codec_name")
        eq_sample_rate = sample_rate
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        codec_text = codec_name if codec_name else "-"

        if ffprobe_error:
            status = "UNKNOWN"
            category = "unknown"
            reason = f"ffprobe error: {ffprobe_error}"
        else:
            if sample_rate is None:
                status = "UNKNOWN"
                category = "unknown"
                reason = "Missing sample rate"
            else:
                mapped_multiple = _map_dsd_multiple(sample_rate)
                if mapped_multiple is None:
                    status = "UNSUPPORTED"
                    category = "unsupported"
                    reason = f"Unrecognized DSD sample rate: {sample_rate} Hz"
                else:
                    dsd_profile = f"DSD{mapped_multiple}"
                    if mapped_multiple > 256:
                        status = "UNSUPPORTED"
                        category = "unsupported"
                        reason = f"Exceeds DSD256 (detected DSD{mapped_multiple})"
                    elif mapped_multiple not in SUPPORTED_DSD_MULTIPLES:
                        status = "UNSUPPORTED"
                        category = "unsupported"
                        reason = f"Unsupported DSD profile DSD{mapped_multiple}"
                    else:
                        status = "SUPPORTED"
                        category = "supported"
                        reason = "Supported DSD profile"
    else:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = f"Recognized audio format not supported: {extension}"

    if category == "skipped":
        eq_compatibility = "N/A"
    else:
        exceeds_eq_sample_rate = eq_sample_rate is not None and eq_sample_rate > 192000
        exceeds_eq_bit_depth = eq_bit_depth is not None and eq_bit_depth > 16

        if exceeds_eq_sample_rate or exceeds_eq_bit_depth:
            eq_compatibility = "Not Compatible"
        elif eq_sample_rate is None or eq_bit_depth is None:
            eq_compatibility = "UNKNOWN"
        else:
            eq_compatibility = "Compatible"

    return {
        "file": relative_path,
        "extension": extension_display,
        "codec": codec_text,
        "status": status,
        "category": category,
        "reason": reason,
        "sample_rate": sample_rate_text,
        "bit_depth": bit_depth_text,
        "block_size": block_size_text,
        "dsd_profile": dsd_profile,
        "eq_compatibility": eq_compatibility,
    }


class MusicCompatibilityScanWorker(QObject):
    progress = Signal(int, int, int, int, int, int)
    finished = Signal(list, int, int, int, int, int)
    cancelled = Signal(int, int, int, int, int, int)
    failed = Signal(str)

    def __init__(self, target_path: Path):
        super().__init__()
        self.target_path = target_path
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            candidate_files: list[Path] = []
            for root_dir, dir_names, file_names in os.walk(self.target_path):
                if self._cancel_requested:
                    self.cancelled.emit(0, 0, 0, 0, 0, 0)
                    return

                # Do not descend into hidden directories.
                dir_names[:] = [name for name in dir_names if not name.startswith(".")]

                for file_name in file_names:
                    if self._cancel_requested:
                        self.cancelled.emit(0, 0, 0, 0, 0, 0)
                        return
                    if file_name.startswith("."):
                        continue
                    file_path = Path(root_dir) / file_name
                    if file_path.is_symlink():
                        # Ignore symlinks so conversions cannot escape the selected target root.
                        continue
                    if file_path.is_file() and file_path.suffix.lower() in KNOWN_AUDIO_FORMATS:
                        candidate_files.append(file_path)

            total_files = len(candidate_files)
            supported = 0
            unsupported = 0
            unknown = 0
            skipped = 0
            rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str]] = []

            self.progress.emit(0, total_files, supported, unsupported, unknown, skipped)

            for index, file_path in enumerate(candidate_files, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(index - 1, total_files, supported, unsupported, unknown, skipped)
                    return

                result = _evaluate_music_file_with_cache(file_path, self.target_path)
                rows.append(
                    (
                        result["file"],
                        result["extension"],
                        result["codec"],
                        result["status"],
                        result["reason"],
                        result["sample_rate"],
                        result["bit_depth"],
                        result["block_size"],
                        result["dsd_profile"],
                        result["eq_compatibility"],
                        result["category"],
                    )
                )

                category = result["category"]
                if category == "supported":
                    supported += 1
                elif category == "unsupported":
                    unsupported += 1
                elif category == "unknown":
                    unknown += 1
                else:
                    skipped += 1

                if index == total_files or index % 25 == 0:
                    self.progress.emit(index, total_files, supported, unsupported, unknown, skipped)

            self.finished.emit(rows, supported, unsupported, unknown, skipped, total_files)
        except Exception as exc:
            self.failed.emit(str(exc))
