import os
import json
import shutil
import subprocess
import wave
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


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _first_valid_int(*values) -> int | None:
    for value in values:
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed
    return None


def _ffprobe_audio_info(path: Path) -> dict[str, int | None] | None:
    if shutil.which("ffprobe") is None:
        return None

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,bits_per_sample,bits_per_raw_sample,max_block_size",
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
    sample_rate = _safe_int(stream.get("sample_rate"))
    bit_depth = _first_valid_int(
        stream.get("bits_per_raw_sample"),
        stream.get("bits_per_sample"),
    )
    flac_block_max = _safe_int(stream.get("max_block_size"))

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
            sample_rate = _first_valid_int(
                getattr(info, "sample_rate", None),
                getattr(info, "samplerate", None),
            )
            bit_depth = _first_valid_int(
                getattr(info, "bits_per_sample", None),
                getattr(info, "bit_depth", None),
                getattr(info, "bits_per_raw_sample", None),
            )
            flac_block_max = _first_valid_int(
                getattr(info, "max_blocksize", None),
                getattr(info, "max_block_size", None),
            )

    if path.suffix.lower() == ".wav" and (sample_rate is None or bit_depth is None):
        try:
            with wave.open(str(path), "rb") as wav_file:
                if sample_rate is None:
                    sample_rate = _safe_int(wav_file.getframerate())
                if bit_depth is None:
                    bit_depth = _safe_int(wav_file.getsampwidth() * 8)
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
    try:
        relative_path = path.relative_to(target_dir).as_posix()
    except Exception:
        relative_path = str(path)

    extension = path.suffix.lower()
    extension_display = extension[1:] if extension else "(none)"
    status = "SKIPPED"
    category = "skipped"
    reason = "Non-audio file type"
    sample_rate_text = "-"
    bit_depth_text = "-"
    block_size_text = "N/A"
    dsd_profile = "-"

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
        }

    if not extension:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = "No extension"
    elif extension not in KNOWN_AUDIO_FORMATS:
        status = "SKIPPED"
        category = "skipped"
        reason = "Non-audio file type"
    elif extension in EXPLICIT_UNSUPPORTED:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = "Explicitly unsupported format"
    elif extension in LOSSY_FORMATS:
        metadata = _read_audio_metadata(path)
        sample_rate = metadata.get("sample_rate")
        bit_depth = metadata.get("bit_depth")

        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "N/A"
        status = "SUPPORTED"
        category = "supported"
        reason = "Lossy format (always supported)"
    elif extension in PCM_FORMATS:
        metadata = _read_audio_metadata(path)
        sample_rate = metadata.get("sample_rate")
        bit_depth = metadata.get("bit_depth")
        flac_block_max = metadata.get("flac_block_max")

        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"
        bit_depth_text = str(bit_depth) if bit_depth is not None else "-"
        if extension == ".flac":
            block_size_text = str(flac_block_max) if flac_block_max is not None else "-"
        else:
            block_size_text = "N/A"

        if sample_rate is None or bit_depth is None:
            status = "UNKNOWN"
            category = "unknown"
            reason = "Missing PCM metadata"
        elif extension == ".flac" and flac_block_max is None:
            status = "UNKNOWN"
            category = "unknown"
            reason = "Missing FLAC block size"
        elif extension == ".flac" and flac_block_max > FLAC_BLOCK_MAX_LIMIT:
            status = "UNSUPPORTED"
            category = "unsupported"
            reason = f"FLAC block size exceeds {FLAC_BLOCK_MAX_LIMIT}"
        elif sample_rate > 192000 or bit_depth > 24:
            status = "UNSUPPORTED"
            category = "unsupported"
            reason = "Exceeds PCM limits (<=192000 Hz, <=24-bit)"
        else:
            status = "SUPPORTED"
            category = "supported"
            reason = "PCM format within limits"
    elif extension in DSD_FORMATS:
        metadata = _read_audio_metadata(path)
        sample_rate = metadata.get("sample_rate")
        sample_rate_text = str(sample_rate) if sample_rate is not None else "-"

        if sample_rate is None:
            status = "UNKNOWN"
            category = "unknown"
            reason = "Missing DSD sample rate"
        else:
            mapped_multiple = _map_dsd_multiple(sample_rate)
            if mapped_multiple is None:
                status = "UNSUPPORTED"
                category = "unsupported"
                reason = "Unrecognized DSD rate"
            else:
                dsd_profile = f"DSD{mapped_multiple}"
                if mapped_multiple > 256:
                    status = "UNSUPPORTED"
                    category = "unsupported"
                    reason = "Exceeds DSD256"
                elif mapped_multiple not in SUPPORTED_DSD_MULTIPLES:
                    status = "UNSUPPORTED"
                    category = "unsupported"
                    reason = "DSD multiple not supported"
                else:
                    status = "SUPPORTED"
                    category = "supported"
                    reason = "DSD rate within supported set"
    else:
        status = "UNSUPPORTED"
        category = "unsupported"
        reason = "Format not listed"

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
    }


class MusicCompatibilityScanWorker(QObject):
    progress = Signal(int, int, int, int, int, int)
    finished = Signal(list, int, int, int, int, int)
    failed = Signal(str)

    def __init__(self, target_path: Path):
        super().__init__()
        self.target_path = target_path

    @Slot()
    def run(self) -> None:
        try:
            audio_files: list[Path] = []
            for root_dir, _dir_names, file_names in os.walk(self.target_path):
                for file_name in file_names:
                    if file_name.startswith("."):
                        continue
                    file_path = Path(root_dir) / file_name
                    if file_path.suffix.lower() in KNOWN_AUDIO_FORMATS:
                        audio_files.append(file_path)

            total_files = len(audio_files)
            supported = 0
            unsupported = 0
            unknown = 0
            skipped = 0
            rows: list[tuple[str, str, str, str, str, str, str, str, str]] = []

            self.progress.emit(0, total_files, supported, unsupported, unknown, skipped)

            for index, file_path in enumerate(audio_files, start=1):
                result = evaluate_music_file(file_path, self.target_path)
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
