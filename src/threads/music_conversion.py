"""Background worker for converting incompatible music files."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import mutagen
from PySide6.QtCore import QObject, Signal, Slot

from ..utils.album_art import (
    read_embedded_album_art,
    to_non_progressive_jpeg,
    write_embedded_album_art,
)
from ..utils.metadata_sanitizer import MetadataSanitizer
from ..utils.music_compatibility import (
    _ffprobe_audio_info,
    _resolve_ffmpeg_executable,
    _subprocess_no_window_kwargs,
)

logger = logging.getLogger(__name__)


class MusicConversionWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        target_path: Path,
        candidates: list[dict[str, object]],
        make_eq_compatible: bool,
        compression_level: int,
        dry_run: bool,
        backup_root: Path | None,
        preserve_tags: bool,
    ):
        super().__init__()
        self.target_path = target_path
        self.candidates = candidates
        self.make_eq_compatible = make_eq_compatible
        self.compression_level = compression_level
        self.dry_run = dry_run
        self.backup_root = backup_root
        self.preserve_tags = preserve_tags
        self._cancel_requested = False
        self._active_process: subprocess.Popen | None = None
        self._ffmpeg_executable = _resolve_ffmpeg_executable()
        self.CONVERSION_TIMEOUT = 600
        self._processed_paths: list[dict[str, str]] = []

    def request_cancel(self) -> None:
        self._cancel_requested = True
        process = self._active_process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

    def _is_path_within_target(self, target_path: Path, candidate_path: Path) -> bool:
        try:
            candidate_path.relative_to(target_path)
            return True
        except ValueError:
            return False

    def _parse_optional_int(self, value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _resolve_sample_rate_for_eq_conversion(self, source_file: Path) -> int | None:
        try:
            audio = mutagen.File(source_file)
        except Exception:
            audio = None

        info = getattr(audio, "info", None) if audio else None
        if info is not None:
            for attr_name in ("sample_rate", "samplerate"):
                value = getattr(info, attr_name, None)
                if value is None:
                    continue
                try:
                    return int(value)
                except Exception:
                    continue

        probe_info = _ffprobe_audio_info(source_file)
        if not probe_info or probe_info.get("error"):
            return None
        return self._parse_optional_int(probe_info.get("sample_rate"))

    def _resolve_conversion_stream_metadata(self, source_file: Path) -> tuple[int | None, int | None, int | None]:
        probe_info = _ffprobe_audio_info(source_file)
        if not probe_info or probe_info.get("error"):
            return None, None, None

        stream_index = self._parse_optional_int(probe_info.get("stream_index"))
        sample_rate = self._parse_optional_int(probe_info.get("sample_rate"))
        bit_depth = self._parse_optional_int(probe_info.get("bit_depth"))
        return stream_index, sample_rate, bit_depth

    def _build_music_conversion_command(
        self,
        source_file: Path,
        output_file: Path,
        audio_stream_index: int | None,
        sample_rate: int | None,
        bit_depth: int | None,
        needs_downmix: bool,
    ) -> list[str]:
        max_sample_rate = 192000
        target_bit_depth = 16 if self.make_eq_compatible else 24
        ffmpeg_exec = self._ffmpeg_executable or "ffmpeg"

        command = [
            ffmpeg_exec,
            "-y",
            "-v",
            "error",
            "-i",
            str(source_file),
            "-map",
            f"0:a:{audio_stream_index if audio_stream_index is not None else 0}",
            "-map_metadata",
            "0",
            "-c:a",
            "flac",
            "-f",
            "flac",
            "-compression_level",
            str(self.compression_level),
            "-frame_size",
            "4096",
        ]

        if needs_downmix:
            command.extend(["-ac", "2"])

        if sample_rate is not None and sample_rate > max_sample_rate:
            command.extend(["-ar", str(max_sample_rate)])

        if bit_depth is not None and bit_depth > target_bit_depth:
            if target_bit_depth == 16:
                command.extend(["-sample_fmt", "s16", "-bits_per_raw_sample", "16"])
            else:
                command.extend(["-sample_fmt", "s32", "-bits_per_raw_sample", "24"])
        elif self.make_eq_compatible:
            command.extend(["-sample_fmt", "s16", "-bits_per_raw_sample", "16"])

        command.append(str(output_file))
        return command

    def _run_conversion_subprocess(self, command: list[str]) -> tuple[bool, str]:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **_subprocess_no_window_kwargs(),
            )
        except Exception as exc:
            return False, str(exc)

        self._active_process = process
        start_time = time.time()
        try:
            while True:
                if self._cancel_requested:
                    try:
                        process.terminate()
                        process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    return True, "Cancelled by user"

                try:
                    process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if time.time() - start_time > self.CONVERSION_TIMEOUT:
                        try:
                            process.kill()
                        except Exception:
                            pass
                        return False, f"ffmpeg conversion timed out after {self.CONVERSION_TIMEOUT} seconds"
                    continue

            if process.returncode != 0:
                stderr_bytes = b""
                try:
                    if process.stderr is not None:
                        stderr_bytes = process.stderr.read() or b""
                except Exception:
                    pass
                stderr_text = stderr_bytes.decode(errors="replace").strip()
                logger.debug("ffmpeg stderr: %s", stderr_text)
                short = stderr_text[:2000]
                return False, f"ffmpeg conversion failed (exit code {process.returncode}): {short}"

            return False, ""
        finally:
            self._active_process = None

    def _next_available_backup_path(self, base_path: Path) -> Path:
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

    def _backup_original_file(self, source_path: Path, relative_file: str) -> tuple[bool, str]:
        if self.backup_root is None:
            return True, ""

        backup_target = self.backup_root / Path(relative_file)
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        backup_target = self._next_available_backup_path(backup_target)
        try:
            shutil.copy2(str(source_path), str(backup_target))
            return True, ""
        except Exception as exc:
            return False, f"backup failed: {exc}"

    def _resolve_source_path(self, candidate: dict[str, object]) -> tuple[Path, str]:
        filepath = str(candidate.get("filepath") or "")
        relative_file = str(candidate.get("relative_file") or "")
        if filepath:
            return Path(filepath), relative_file or filepath
        return self.target_path / Path(relative_file), relative_file

    @Slot()
    def run(self) -> None:
        converted = 0
        failed = 0
        planned = 0
        failures: list[str] = []
        total = len(self.candidates)

        try:
            if self.backup_root is not None:
                self.backup_root.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(converted, failed, planned, total, failures))
                    return

                source_input_path, relative_file = self._resolve_source_path(candidate)
                sample_rate = self._parse_optional_int(candidate.get("sample_rate"))
                bit_depth = self._parse_optional_int(candidate.get("bit_depth"))
                needs_downmix = bool(candidate.get("needs_downmix"))

                detail_label = f"Processing {index}/{total}: {source_input_path.name}"
                self.progress.emit(index - 1, total, detail_label)

                if source_input_path.is_symlink():
                    failed += 1
                    failures.append(f"{relative_file}: symlinked files are not converted")
                    self.progress.emit(index, total, detail_label)
                    continue

                source_path = source_input_path.resolve()
                if not self._is_path_within_target(self.target_path.resolve(), source_path):
                    failed += 1
                    failures.append(f"{relative_file}: resolves outside the selected target")
                    self.progress.emit(index, total, detail_label)
                    continue

                stream_index, detected_sample_rate, detected_bit_depth = self._resolve_conversion_stream_metadata(
                    source_path
                )
                if sample_rate is None:
                    sample_rate = detected_sample_rate
                if bit_depth is None:
                    bit_depth = detected_bit_depth

                if not source_path.exists() or not source_path.is_file():
                    failed += 1
                    failures.append(f"{relative_file}: file not found")
                    self.progress.emit(index, total, detail_label)
                    continue

                if self.make_eq_compatible and sample_rate is None:
                    sample_rate = self._resolve_sample_rate_for_eq_conversion(source_path)
                    if sample_rate is None:
                        failed += 1
                        failures.append(
                            f"{relative_file}: sample rate unavailable; cannot guarantee EQ compatibility"
                        )
                        self.progress.emit(index, total, detail_label)
                        continue

                should_convert = bool(candidate.get("should_convert", True))
                needs_sanitize = bool(candidate.get("needs_sanitize", True))
                will_modify = needs_sanitize or should_convert

                if self.dry_run:
                    planned += 1
                    self.progress.emit(index, total, f"Would process: {source_path.name}")
                    continue

                if will_modify and self.backup_root is not None:
                    ok, backup_error = self._backup_original_file(source_path, relative_file)
                    if not ok:
                        failed += 1
                        failures.append(f"{relative_file}: {backup_error}")
                        self.progress.emit(index, total, detail_label)
                        continue

                if needs_sanitize:
                    try:
                        sanitizer = MetadataSanitizer()
                        sanitizer.sanitize(source_path, preserve_third_party_tags=self.preserve_tags)
                    except Exception:
                        pass

                if not should_convert:
                    converted += 1
                    self._processed_paths.append(
                        {"old_path": str(source_path), "new_path": str(source_path)}
                    )
                    self.progress.emit(index, total, detail_label)
                    continue

                output_path = source_path.with_suffix(".flac")
                try:
                    fd, tmp_name = tempfile.mkstemp(
                        prefix=f".{output_path.stem}.tmp",
                        suffix=output_path.suffix,
                        dir=str(source_path.parent),
                    )
                    os.close(fd)
                    temp_output_path = Path(tmp_name)
                except Exception:
                    temp_output_path = output_path.with_name(f".{output_path.stem}.tmp{output_path.suffix}")

                command = self._build_music_conversion_command(
                    source_path,
                    temp_output_path,
                    stream_index,
                    sample_rate,
                    bit_depth,
                    needs_downmix,
                )

                was_cancelled, error_text = self._run_conversion_subprocess(command)
                if was_cancelled:
                    try:
                        if temp_output_path.exists():
                            temp_output_path.unlink()
                    except Exception:
                        pass
                    self.cancelled.emit(self._payload(converted, failed, planned, total, failures))
                    return

                if error_text:
                    failed += 1
                    failures.append(f"{relative_file}: {error_text}")
                    try:
                        if temp_output_path.exists():
                            temp_output_path.unlink()
                    except Exception:
                        pass
                    self.progress.emit(index, total, detail_label)
                    continue

                try:
                    art_bytes, _art_mime = read_embedded_album_art(source_path)

                    if output_path.exists() and output_path != source_path:
                        output_path.unlink()
                    temp_output_path.replace(output_path)

                    if art_bytes:
                        try:
                            converted_jpeg = to_non_progressive_jpeg(art_bytes)
                            if converted_jpeg:
                                ok, detail = write_embedded_album_art(output_path, converted_jpeg)
                                if not ok:
                                    logger.debug("Failed to re-embed artwork for %s: %s", output_path, detail)
                        except Exception as exc:
                            logger.debug("Exception re-embedding artwork for %s: %s", output_path, exc)

                    if source_path != output_path and source_path.exists():
                        source_path.unlink()

                    self._processed_paths.append(
                        {"old_path": str(source_path), "new_path": str(output_path)}
                    )
                    converted += 1
                except Exception as exc:
                    failed += 1
                    failures.append(f"{relative_file}: {exc}")
                    try:
                        if temp_output_path.exists():
                            temp_output_path.unlink()
                    except Exception:
                        pass

                self.progress.emit(index, total, detail_label)

            self.finished.emit(
                self._payload(
                    converted,
                    failed,
                    planned,
                    total,
                    failures,
                    include_backup=True,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    def _payload(
        self,
        converted: int,
        failed: int,
        planned: int,
        total: int,
        failures: list[str],
        *,
        include_backup: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "converted": converted,
            "failed": failed,
            "planned": planned,
            "total": total,
            "failures": failures,
            "dry_run": self.dry_run,
            "processed_paths": list(self._processed_paths),
        }
        if include_backup:
            payload["backup_root"] = str(self.backup_root) if self.backup_root is not None else ""
        return payload
