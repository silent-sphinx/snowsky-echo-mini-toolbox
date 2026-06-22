import os
import sys
import json
import re
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
import logging

EMOJI_PATTERN = re.compile(
    r'['
    r'\U0001f300-\U0001f64f'
    r'\U0001f680-\U0001f6ff'
    r'\U0001f900-\U0001f9ff'
    r'\U0001fa70-\U0001faff'
    r'\u2600-\u26ff'
    r'\u2700-\u27bf'
    r']'
)

ASIAN_SCRIPTS_PATTERN = re.compile(
    r'['
    r'\u0900-\u097f'
    r'\u0980-\u09ff'
    r'\u1780-\u17ff\u19e0-\u19ff'
    r'\u1000-\u109f\uaa60-\uaa7f\ua9e0-\ua9ff'
    r']'
)


def _evaluate_file_name_compatibility(filename: str) -> tuple[str, str]:
    issues = []
    if EMOJI_PATTERN.search(filename):
        issues.append("Emojis are unsupported")
    if ASIAN_SCRIPTS_PATTERN.search(filename):
        issues.append("Complex Asian scripts (e.g. Hindi, Bengali, Khmer, Burmese) are unsupported")

    if issues:
        return "INCOMPATIBLE", ", ".join(issues)
    return "COMPATIBLE", "File name is compatible"


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
    # Check PyInstaller bundle temp folder when frozen
    meipass = getattr(sys, "_MEIPASS", "")
    local_candidates = []
    if meipass:
        local_candidates.extend([Path(meipass) / "ffprobe", Path(meipass) / "ffprobe.exe"]) 
    else:
        repo_root = Path(__file__).resolve().parent.parent
        local_candidates.extend([repo_root / "build_assets" / "ffprobe", repo_root / "build_assets" / "ffprobe.exe"]) 

    for candidate in local_candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def _resolve_ffmpeg_executable() -> str | None:
    """Resolve an ffmpeg executable path.

    Preference order:
      1. System PATH (ffmpeg / ffmpeg.exe)
      2. Local bundled candidate under repo_root/build_assets/ffmpeg
    """
    ffmpeg_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg_path:
        return ffmpeg_path

    meipass = getattr(sys, "_MEIPASS", "")
    local_candidates = []
    if meipass:
        local_candidates.extend([Path(meipass) / "ffmpeg", Path(meipass) / "ffmpeg.exe"]) 
    else:
        repo_root = Path(__file__).resolve().parent.parent
        local_candidates.extend([repo_root / "build_assets" / "ffmpeg", repo_root / "build_assets" / "ffmpeg.exe"]) 

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


def _stream_with_fallback_int(stream: dict, *keys: str) -> int | None:
    values = [stream.get(key) for key in keys]
    return _positive_int_or_none(_first_valid_int(*values))


def _stream_bit_depth(stream: dict) -> int | None:
    bit_depth = _stream_with_fallback_int(
        stream,
        "bits_per_raw_sample",
        "bits_per_sample",
    )
    if bit_depth is not None:
        return bit_depth
    return _infer_bit_depth_from_sample_fmt(stream.get("sample_fmt"))


def _stream_disposition_default(stream: dict) -> bool:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return False
    value = _safe_int(disposition.get("default"))
    return bool(value and value > 0)


def _pcm_stream_is_within_limits(
    extension: str,
    sample_rate: int | None,
    bit_depth: int | None,
    flac_block_max: int | None,
) -> bool:
    if sample_rate is None or bit_depth is None:
        return False
    if sample_rate > 192000 or bit_depth > 24:
        return False
    if extension == ".flac":
        if flac_block_max is None:
            return False
        if flac_block_max > FLAC_BLOCK_MAX_LIMIT:
            return False
    return True


def _stream_selection_score(stream_info: dict, extension: str) -> int:
    score = 0

    if stream_info.get("is_default"):
        score += 200

    sample_rate = stream_info.get("sample_rate")
    bit_depth = stream_info.get("bit_depth")
    if sample_rate is not None:
        score += 20
    if bit_depth is not None:
        score += 20

    if stream_info.get("codec_name"):
        score += 10

    if extension in PCM_FORMATS:
        if _pcm_stream_is_within_limits(
            extension,
            sample_rate,
            bit_depth,
            stream_info.get("flac_block_max"),
        ):
            score += 1000
        elif sample_rate is not None and bit_depth is not None:
            score += 100

    # Prefer richer streams when all else is equal.
    channels = stream_info.get("channels") or 0
    score += min(int(channels), 16)

    return score


def _select_preferred_audio_stream(audio_streams: list[dict], extension: str) -> dict | None:
    if not audio_streams:
        return None

    sorted_streams = sorted(
        audio_streams,
        key=lambda stream_info: (
            _stream_selection_score(stream_info, extension),
            stream_info.get("channels") or 0,
            stream_info.get("sample_rate") or 0,
            stream_info.get("bit_depth") or 0,
            -int(stream_info.get("stream_pos") or 0),
        ),
        reverse=True,
    )
    return sorted_streams[0]


def _attempt_json_recovery_via_remux(path: Path, ffprobe_executable: str) -> bool:
    """Try to recover from JSON parse failure by remuxing file with ffmpeg to strip bad tags.
    
    Creates a temporary clean copy, probes it, and returns True if probe succeeds on clean copy.
    Original file is never modified. Temp file is cleaned up regardless of outcome.
    """
    ffmpeg_path = _resolve_ffmpeg_executable()
    if ffmpeg_path is None:
        return False

    logger = logging.getLogger(__name__)
    temp_path = None
    try:
        # Create temp file for clean remux copy.
        fd, temp_name = tempfile.mkstemp(prefix=".ffprobe_recovery_", suffix=path.suffix, dir=str(path.parent))
        os.close(fd)
        temp_path = Path(temp_name)

        # Remux with ffmpeg (copy codec, no re-encode) to strip metadata.
        cmd = [
            ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-c",
            "copy",
            "-map_metadata",
            "-1",
            str(temp_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **_subprocess_no_window_kwargs(),
        )
        if result.returncode != 0:
            logger.debug("ffmpeg remux for recovery failed: %s", result.stderr)
            return False

        # Try probing the clean copy.
        probe_cmd = [
            ffprobe_executable,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,sample_rate,sample_fmt,bits_per_sample,bits_per_raw_sample,max_block_size,max_blocksize,max_samples_per_frame,codec_name,channels,bit_rate,"
            "duration,codec_long_name",
            "-show_entries",
            "stream_disposition=default",
            "-show_entries",
            "format=duration,bit_rate,format_long_name",
            "-of",
            "json",
            str(temp_path),
        ]
        probe_result = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_subprocess_no_window_kwargs(),
        )
        if probe_result.returncode != 0:
            logger.debug("ffprobe of recovered file failed: %s", probe_result.stderr)
            return False

        # Validate clean JSON parse.
        try:
            json.loads(probe_result.stdout)
            logger.debug("JSON recovery successful for %s", path)
            return True
        except Exception:
            logger.debug("Recovered file still has unparseable JSON")
            return False
    except Exception as exc:
        logger.debug("Exception during recovery remux: %s", exc)
        return False
    finally:
        if temp_path is not None:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass


def _ffprobe_audio_info(path: Path) -> dict[str, object] | None:
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
        "-show_entries",
        "stream=index,codec_type,sample_rate,sample_fmt,bits_per_sample,bits_per_raw_sample,max_block_size,max_blocksize,max_samples_per_frame,codec_name,channels,bit_rate,"
        "duration,codec_long_name",
        "-show_entries",
        "stream_disposition=default",
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
                encoding="utf-8",
                errors="replace",
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
            # Attempt recovery via remux on first JSON parse failure only.
            if attempt == 0:
                logger.debug("Attempting tag recovery via ffmpeg remux for %s", path)
                try:
                    if _attempt_json_recovery_via_remux(path, ffprobe_executable):
                        logger.debug("Recovery succeeded; retrying ffprobe for %s", path)
                        continue
                except Exception as recovery_exc:
                    logger.debug("Tag recovery attempt failed for %s: %s", path, recovery_exc)
            continue

        # Success
        break
    else:
        return {"error": last_err or "ffprobe failed"}

    # Extract stream information
    raw_streams = payload.get("streams") or []
    audio_streams: list[dict[str, int | str | bool | None]] = []
    file_extension = path.suffix.lower()
    file_flac_block_max = None
    if file_extension == ".flac":
        file_flac_block_max = _read_flac_streaminfo_block_max_size(path)

    for stream_pos, stream in enumerate(raw_streams):
        codec_type = str(stream.get("codec_type", "")).strip().lower()
        if codec_type and codec_type != "audio":
            continue

        sample_rate = _stream_with_fallback_int(stream, "sample_rate")
        channels = _stream_with_fallback_int(stream, "channels")
        bit_depth = _stream_bit_depth(stream)
        codec_name = str(stream.get("codec_name", "")).strip().lower() or None
        codec_long_name = str(stream.get("codec_long_name", "")).strip() or None
        flac_block_max = _extract_flac_max_block_size(stream, codec_name)
        if flac_block_max is None and file_extension == ".flac" and codec_name == "flac":
            flac_block_max = file_flac_block_max

        try:
            duration_value = float(stream.get("duration", 0))
            stream_duration = duration_value if duration_value > 0 else None
        except (ValueError, TypeError):
            stream_duration = None

        stream_bitrate = _stream_with_fallback_int(stream, "bit_rate")

        audio_streams.append(
            {
                "stream_pos": stream_pos,
                "stream_index": _safe_int(stream.get("index")),
                "sample_rate": sample_rate,
                "bit_depth": bit_depth,
                "flac_block_max": flac_block_max,
                "codec_name": codec_name,
                "codec_long_name": codec_long_name,
                "channels": channels,
                "bitrate": stream_bitrate,
                "duration": stream_duration,
                "is_default": _stream_disposition_default(stream),
            }
        )

    if not audio_streams:
        return {"error": "no streams found in ffprobe output"}

    selected_stream = _select_preferred_audio_stream(audio_streams, file_extension)
    if selected_stream is None:
        return {"error": "unable to select audio stream"}

    sample_rate = selected_stream.get("sample_rate")
    channels = selected_stream.get("channels")
    bit_depth = selected_stream.get("bit_depth")
    codec_name = selected_stream.get("codec_name")
    codec_long_name = selected_stream.get("codec_long_name")
    max_block_size = selected_stream.get("flac_block_max")
    
    # Duration and bitrate from stream first, then format
    duration = None
    stream_duration = selected_stream.get("duration")
    if isinstance(stream_duration, (int, float)) and stream_duration > 0:
        duration = float(stream_duration)
    
    if duration is None:
        format_info = payload.get("format") or {}
        try:
            format_duration = float(format_info.get("duration", 0))
            if format_duration > 0:
                duration = format_duration
        except (ValueError, TypeError):
            pass
    
    bit_rate = None
    stream_bitrate = selected_stream.get("bitrate")
    if isinstance(stream_bitrate, int) and stream_bitrate > 0:
        bit_rate = stream_bitrate
    
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
    selected_pos = selected_stream.get("stream_pos")
    stream_tags = {}
    if isinstance(selected_pos, int) and 0 <= selected_pos < len(raw_streams):
        stream_tags = raw_streams[selected_pos].get("tags") or {}
    format_tags = payload.get("format", {}).get("tags") or {}
    
    if stream_tags:
        tags.update({k.lower(): v for k, v in stream_tags.items()})
    if format_tags:
        tags.update({k.lower(): v for k, v in format_tags.items()})

    return {
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "flac_block_max": max_block_size,
        "stream_index": selected_stream.get("stream_index"),
        "codec_name": codec_name,
        "codec_long_name": codec_long_name,
        "channels": channels,
        "bitrate": bit_rate,
        "duration": duration,
        "format_long_name": format_long_name,
        "tags": tags,
        "audio_stream_count": len(audio_streams),
        "total_stream_count": len(raw_streams),
        "audio_streams": audio_streams,
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


def get_all_streams(path: Path) -> list[dict[str, object]] | None:
    """Get all streams in a media file (audio, video, subtitle, etc.) with their specifications.
    
    Returns a list of stream dicts, each containing:
      - index: stream index
      - codec_type: 'audio', 'video', 'subtitle', etc.
      - codec_name: codec name
      - codec_long_name: full codec description
      - channels: channel count (audio only)
      - sample_rate: sample rate in Hz (audio only)
      - bit_depth: bits per sample (audio only)
      - width, height: resolution (video only)
      - bitrate: bitrate in bits/second
      - duration: duration in seconds
      - tags: metadata tags
    """
    logger = logging.getLogger(__name__)

    ffprobe_executable = _resolve_ffprobe_executable()
    if ffprobe_executable is None:
        return None

    cmd = [
        ffprobe_executable,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,codec_long_name,channels,sample_rate,bits_per_sample,"
        "width,height,bit_rate,duration",
        "-show_entries",
        "stream_tags",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
            **_subprocess_no_window_kwargs(),
        )
    except Exception as exc:
        logger.debug("Failed to get all streams for %s: %s", path, exc)
        return None

    if result.returncode != 0:
        logger.debug("ffprobe failed for %s: %s", path, result.stderr)
        return None

    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        logger.debug("Failed to parse ffprobe JSON for %s: %s", path, exc)
        return None

    raw_streams = payload.get("streams") or []
    streams_info: list[dict[str, object]] = []

    for stream in raw_streams:
        stream_index = _safe_int(stream.get("index"))
        codec_type = str(stream.get("codec_type", "unknown")).strip().lower()
        codec_name = str(stream.get("codec_name", "")).strip() or None
        codec_long_name = str(stream.get("codec_long_name", "")).strip() or None
        bitrate = _stream_with_fallback_int(stream, "bit_rate")
        
        try:
            duration_value = float(stream.get("duration", 0))
            duration = duration_value if duration_value > 0 else None
        except (ValueError, TypeError):
            duration = None

        stream_info = {
            "index": stream_index,
            "codec_type": codec_type,
            "codec_name": codec_name,
            "codec_long_name": codec_long_name,
            "bitrate": bitrate,
            "duration": duration,
            "tags": stream.get("tags") or {},
        }

        # Audio-specific fields
        if codec_type == "audio":
            stream_info["channels"] = _stream_with_fallback_int(stream, "channels")
            stream_info["sample_rate"] = _stream_with_fallback_int(stream, "sample_rate")
            stream_info["bit_depth"] = _stream_bit_depth(stream)

        # Video-specific fields
        if codec_type == "video":
            stream_info["width"] = _safe_int(stream.get("width"))
            stream_info["height"] = _safe_int(stream.get("height"))

        streams_info.append(stream_info)

    return streams_info if streams_info else None


def _get_audio_codec(path: Path) -> str | None:
    """Extract the actual audio codec name from the file using ffprobe."""
    ffprobe_info = _ffprobe_audio_info(path)
    if not ffprobe_info:
        return None
    if isinstance(ffprobe_info, dict) and ffprobe_info.get("error"):
        return None
    return ffprobe_info.get("codec_name")


def _read_audio_metadata(path: Path) -> dict[str, object]:
    """Read audio metadata using ffprobe exclusively."""
    ffprobe_info = _ffprobe_audio_info(path)
    if ffprobe_info is None:
        return {
            "sample_rate": None,
            "bit_depth": None,
            "flac_block_max": None,
            "stream_index": None,
            "codec_name": None,
            "channels": None,
            "audio_stream_count": None,
            "total_stream_count": None,
            "audio_streams": [],
        }

    # If ffprobe reported an error, return that information for higher-level
    # callers to surface a more informative reason instead of failing silently.
    if isinstance(ffprobe_info, dict) and ffprobe_info.get("error"):
        return {
            "sample_rate": None,
            "bit_depth": None,
            "flac_block_max": None,
            "stream_index": None,
            "codec_name": None,
            "channels": None,
            "audio_stream_count": None,
            "total_stream_count": None,
            "ffprobe_error": ffprobe_info.get("error"),
            "audio_streams": [],
        }

    return {
        "sample_rate": ffprobe_info.get("sample_rate"),
        "bit_depth": ffprobe_info.get("bit_depth"),
        "flac_block_max": ffprobe_info.get("flac_block_max"),
        "stream_index": ffprobe_info.get("stream_index"),
        "codec_name": ffprobe_info.get("codec_name"),
        "channels": ffprobe_info.get("channels"),
        "audio_stream_count": ffprobe_info.get("audio_stream_count"),
        "total_stream_count": ffprobe_info.get("total_stream_count"),
        "audio_streams": ffprobe_info.get("audio_streams") or [],
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
    block_size_text = "-"
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
            "eq_compatibility": "-",
            "channels": "-",
            "stream_count": "-",
            "filename_compatibility": "-",
            "filename_compatibility_reason": "-",
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
        audio_streams = metadata.get("audio_streams") or []
        eq_sample_rate = sample_rate
        eq_bit_depth = bit_depth

        codec_text = codec_name if codec_name else "-"
        if ffprobe_error:
            status = "UNKNOWN"
            category = "unknown"
            reason = f"ffprobe error: {ffprobe_error}"
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "-"
        
        # If ffprobe failed, surface that and skip further validation
        if ffprobe_error:
            pass
        else:
            # Strict mode: every audio stream in the container must match format rules.
            if isinstance(audio_streams, list) and audio_streams:
                invalid_reason = ""
                for idx, stream in enumerate(audio_streams, start=1):
                    stream_codec = stream.get("codec_name")
                    codec_valid, codec_error = _validate_codec_for_container(extension, stream_codec)
                    if not codec_valid:
                        invalid_reason = f"Stream {idx}: {codec_error}"
                        break
                if invalid_reason:
                    status = "UNSUPPORTED"
                    category = "unsupported"
                    reason = invalid_reason
                else:
                    status = "SUPPORTED"
                    category = "supported"
                    reason = "All audio streams pass lossy format checks"
            else:
                # Fallback for older metadata payloads.
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
        audio_streams = metadata.get("audio_streams") or []
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
            block_size_text = "-"

        # If ffprobe failed, surface that and skip deeper PCM validation.
        if ffprobe_error:
            pass
        else:
            # Strict mode: every stream must satisfy PCM/FLAC limits.
            strict_streams = audio_streams if isinstance(audio_streams, list) and audio_streams else [
                {
                    "sample_rate": sample_rate,
                    "bit_depth": bit_depth,
                    "flac_block_max": flac_block_max,
                }
            ]

            max_eq_sample_rate = None
            max_eq_bit_depth = None
            first_unknown_reason = ""
            first_unsupported_reason = ""

            for idx, stream in enumerate(strict_streams, start=1):
                stream_sample_rate = _positive_int_or_none(_safe_int(stream.get("sample_rate")))
                stream_bit_depth = _positive_int_or_none(_safe_int(stream.get("bit_depth")))
                stream_flac_block_max = _positive_int_or_none(_safe_int(stream.get("flac_block_max")))

                if stream_sample_rate is not None:
                    max_eq_sample_rate = stream_sample_rate if max_eq_sample_rate is None else max(max_eq_sample_rate, stream_sample_rate)
                if stream_bit_depth is not None:
                    max_eq_bit_depth = stream_bit_depth if max_eq_bit_depth is None else max(max_eq_bit_depth, stream_bit_depth)

                missing_pcm_fields: list[str] = []
                if stream_sample_rate is None:
                    missing_pcm_fields.append("sample rate")
                if stream_bit_depth is None:
                    missing_pcm_fields.append("bit depth")

                if missing_pcm_fields:
                    if not first_unknown_reason:
                        first_unknown_reason = f"Stream {idx}: {_short_missing_metadata_reason(missing_pcm_fields)}"
                    continue

                if extension == ".flac" and stream_flac_block_max is None:
                    if not first_unknown_reason:
                        first_unknown_reason = f"Stream {idx}: Missing FLAC block size"
                    continue

                if extension == ".flac" and stream_flac_block_max > FLAC_BLOCK_MAX_LIMIT:
                    if not first_unsupported_reason:
                        first_unsupported_reason = (
                            f"Stream {idx}: FLAC block size {stream_flac_block_max} exceeds limit {FLAC_BLOCK_MAX_LIMIT}"
                        )
                    continue

                if stream_sample_rate > 192000 or stream_bit_depth > 24:
                    reason_parts: list[str] = []
                    if stream_sample_rate > 192000:
                        reason_parts.append(f"sample rate {stream_sample_rate} > 192000 Hz")
                    if stream_bit_depth > 24:
                        reason_parts.append(f"bit depth {stream_bit_depth} > 24-bit")
                    if not first_unsupported_reason:
                        first_unsupported_reason = f"Stream {idx}: Exceeds PCM limits: " + ", ".join(reason_parts)

            if first_unsupported_reason:
                status = "UNSUPPORTED"
                category = "unsupported"
                reason = first_unsupported_reason
            elif first_unknown_reason:
                status = "UNKNOWN"
                category = "unknown"
                reason = first_unknown_reason
            else:
                status = "SUPPORTED"
                category = "supported"
                reason = "All audio streams are within PCM limits"

            eq_sample_rate = max_eq_sample_rate
            eq_bit_depth = max_eq_bit_depth
    elif extension in DSD_FORMATS:
        metadata = _read_audio_metadata(path)
        ffprobe_error = metadata.get("ffprobe_error")
        sample_rate = metadata.get("sample_rate")
        codec_name = metadata.get("codec_name")
        audio_streams = metadata.get("audio_streams") or []
        eq_sample_rate = sample_rate
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        codec_text = codec_name if codec_name else "-"

        if ffprobe_error:
            status = "UNKNOWN"
            category = "unknown"
            reason = f"ffprobe error: {ffprobe_error}"
        else:
            strict_streams = audio_streams if isinstance(audio_streams, list) and audio_streams else [
                {"sample_rate": sample_rate}
            ]

            first_unknown_reason = ""
            first_unsupported_reason = ""
            best_profile = None

            for idx, stream in enumerate(strict_streams, start=1):
                stream_sample_rate = _positive_int_or_none(_safe_int(stream.get("sample_rate")))
                if stream_sample_rate is None:
                    if not first_unknown_reason:
                        first_unknown_reason = f"Stream {idx}: Missing sample rate"
                    continue

                mapped_multiple = _map_dsd_multiple(stream_sample_rate)
                if mapped_multiple is None:
                    if not first_unsupported_reason:
                        first_unsupported_reason = f"Stream {idx}: Unrecognized DSD sample rate: {stream_sample_rate} Hz"
                    continue

                if best_profile is None:
                    best_profile = mapped_multiple
                else:
                    best_profile = max(best_profile, mapped_multiple)

                if mapped_multiple > 256:
                    if not first_unsupported_reason:
                        first_unsupported_reason = f"Stream {idx}: Exceeds DSD256 (detected DSD{mapped_multiple})"
                    continue

                if mapped_multiple not in SUPPORTED_DSD_MULTIPLES:
                    if not first_unsupported_reason:
                        first_unsupported_reason = f"Stream {idx}: Unsupported DSD profile DSD{mapped_multiple}"

            if best_profile is not None:
                dsd_profile = f"DSD{best_profile}"

            if first_unsupported_reason:
                status = "UNSUPPORTED"
                category = "unsupported"
                reason = first_unsupported_reason
            elif first_unknown_reason:
                status = "UNKNOWN"
                category = "unknown"
                reason = first_unknown_reason
            else:
                status = "SUPPORTED"
                category = "supported"
                reason = "All audio streams have supported DSD profiles"
    else:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = f"Recognized audio format not supported: {extension}"

    if category == "skipped":
        eq_compatibility = "-"
    else:
        exceeds_eq_sample_rate = eq_sample_rate is not None and eq_sample_rate > 192000
        exceeds_eq_bit_depth = eq_bit_depth is not None and eq_bit_depth > 16

        if exceeds_eq_sample_rate or exceeds_eq_bit_depth:
            eq_compatibility = "Not EQ Compatible"
        elif eq_sample_rate is None or eq_bit_depth is None:
            eq_compatibility = "UNKNOWN"
        else:
            eq_compatibility = "EQ Compatible"

    filename_comp_status, filename_comp_reason = _evaluate_file_name_compatibility(path.name)

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
        "channels": str(metadata.get("channels") or "-") if extension in (PCM_FORMATS | DSD_FORMATS | LOSSY_FORMATS) else "-",
        "stream_count": str(metadata.get("total_stream_count") or "-") if extension in (PCM_FORMATS | DSD_FORMATS | LOSSY_FORMATS) else "-",
        "filename_compatibility": filename_comp_status,
        "filename_compatibility_reason": filename_comp_reason,
    }


class MusicCompatibilityScanWorker(QObject):
    progress = Signal(int, int, int, int, int, int, int, str)
    finished = Signal(list, int, int, int, int, int, int)
    cancelled = Signal(int, int, int, int, int, int, int)
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
                    self.cancelled.emit(0, 0, 0, 0, 0, 0, 0)
                    return

                # Do not descend into hidden directories.
                dir_names[:] = [name for name in dir_names if not name.startswith(".")]

                for file_name in file_names:
                    if self._cancel_requested:
                        self.cancelled.emit(0, 0, 0, 0, 0, 0, 0)
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
            eq_incompatible = 0
            rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str]] = []

            self.progress.emit(0, total_files, supported, unsupported, unknown, skipped, eq_incompatible, "")

            for index, file_path in enumerate(candidate_files, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(index - 1, total_files, supported, unsupported, unknown, skipped, eq_incompatible)
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
                        result.get("channels", "-"),
                        result.get("stream_count", "-"),
                        result.get("filename_compatibility", "-"),
                        result.get("filename_compatibility_reason", "-"),
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

                # Count EQ-incompatible (and unknown) supported files
                if category == "supported" and result["eq_compatibility"].lower() != "eq compatible":
                    eq_incompatible += 1

                self.progress.emit(index, total_files, supported, unsupported, unknown, skipped, eq_incompatible, file_path.name)

            self.finished.emit(rows, supported, unsupported, unknown, skipped, total_files, eq_incompatible)
        except Exception as exc:
            self.failed.emit(str(exc))
