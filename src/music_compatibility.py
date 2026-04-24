import os
import json
import re
import shutil
import subprocess
import wave
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

try:
    import mutagen
except Exception:
    mutagen = None


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


def _ffprobe_audio_info(path: Path) -> dict[str, int | None] | None:
    ffprobe_executable = _resolve_ffprobe_executable()
    if ffprobe_executable is None:
        return None

    cmd = [
        ffprobe_executable,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,sample_fmt,bits_per_sample,bits_per_raw_sample,max_block_size",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout)
    except Exception:
        return None

    streams = payload.get("streams") or []
    if not streams:
        return None

    stream = streams[0]
    sample_rate = _positive_int_or_none(_safe_int(stream.get("sample_rate")))
    bit_depth = _first_valid_int(
        stream.get("bits_per_raw_sample"),
        stream.get("bits_per_sample"),
    )
    bit_depth = _positive_int_or_none(bit_depth)
    if bit_depth is None:
        bit_depth = _infer_bit_depth_from_sample_fmt(stream.get("sample_fmt"))

    flac_block_max = _positive_int_or_none(_safe_int(stream.get("max_block_size")))

    if sample_rate is None and bit_depth is None and flac_block_max is None:
        return None

    return {
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "flac_block_max": flac_block_max,
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


def _read_audio_metadata(path: Path) -> dict[str, int | None]:
    sample_rate = None
    bit_depth = None
    flac_block_max = None

    if mutagen is not None:
        try:
            audio = mutagen.File(path)
        except Exception:
            audio = None

        info = getattr(audio, "info", None) if audio else None
        if info is not None:
            sample_rate = _positive_int_or_none(
                _first_valid_int(
                getattr(info, "sample_rate", None),
                getattr(info, "samplerate", None),
                )
            )
            bit_depth = _positive_int_or_none(
                _first_valid_int(
                getattr(info, "bits_per_sample", None),
                getattr(info, "bit_depth", None),
                getattr(info, "bits_per_raw_sample", None),
                )
            )
            flac_block_max = _positive_int_or_none(
                _first_valid_int(
                getattr(info, "max_blocksize", None),
                getattr(info, "max_block_size", None),
                )
            )

    if path.suffix.lower() == ".wav" and (sample_rate is None or bit_depth is None):
        try:
            with wave.open(str(path), "rb") as wav_file:
                if sample_rate is None:
                    sample_rate = _positive_int_or_none(_safe_int(wav_file.getframerate()))
                if bit_depth is None:
                    bit_depth = _positive_int_or_none(_safe_int(wav_file.getsampwidth() * 8))
        except Exception:
            pass

    if sample_rate is None or bit_depth is None or flac_block_max is None:
        ffprobe_info = _ffprobe_audio_info(path)
        if ffprobe_info:
            if sample_rate is None:
                sample_rate = ffprobe_info.get("sample_rate")
            if bit_depth is None:
                bit_depth = ffprobe_info.get("bit_depth")
            if flac_block_max is None:
                flac_block_max = ffprobe_info.get("flac_block_max")

    return {
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "flac_block_max": flac_block_max,
    }


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
    eq_sample_rate = None
    eq_bit_depth = None

    if path.name.startswith("."):
        reason = "Hidden dot-file ignored by compatibility rules"
        return {
            "file": relative_path,
            "extension": extension_display,
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
        sample_rate = metadata.get("sample_rate")
        bit_depth = metadata.get("bit_depth")
        eq_sample_rate = sample_rate
        eq_bit_depth = bit_depth

        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "N/A"
        status = "SUPPORTED"
        category = "supported"
        reason = "Lossy format supported"
    elif extension in PCM_FORMATS:
        metadata = _read_audio_metadata(path)
        sample_rate = metadata.get("sample_rate")
        bit_depth = metadata.get("bit_depth")
        flac_block_max = metadata.get("flac_block_max")
        eq_sample_rate = sample_rate
        eq_bit_depth = bit_depth

        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "-"
        if extension == ".flac":
            block_size_text = str(flac_block_max) if flac_block_max is not None else "-"
        else:
            block_size_text = "N/A"

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
        sample_rate = metadata.get("sample_rate")
        eq_sample_rate = sample_rate
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"

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
            rows: list[tuple[str, str, str, str, str, str, str, str, str, str]] = []

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
