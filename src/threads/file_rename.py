"""Background worker for applying File Rename suggestions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..utils.file_rename import paths_are_same_file

logger = logging.getLogger(__name__)


@dataclass
class _RenameJob:
    source: Path
    target: Path
    relative: str
    label: str
    lrc_needed: bool
    lrc_source: Path
    lrc_target: Path


class FileRenameWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, target_path: Path, candidates: list[dict[str, object]]):
        super().__init__()
        self.target_path = target_path
        self.candidates = candidates
        self._cancel_requested = False
        self._processed_paths: list[dict[str, str]] = []

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _is_path_within_target(self, candidate_path: Path) -> bool:
        try:
            candidate_path.resolve().relative_to(self.target_path.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _unique_temp_path(self, source_path: Path) -> Path:
        parent = source_path.parent
        stem = source_path.name
        counter = 0
        while counter <= 10000:
            suffix = "" if counter == 0 else f".{counter}"
            candidate = parent / f"{stem}.__sem_rename__{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
        raise RuntimeError("Could not allocate a temporary rename path")

    def _path_key(self, path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    def _restore_temp(self, temp_path: Path | None, original_path: Path) -> None:
        if temp_path is None or not temp_path.exists():
            return
        try:
            if original_path.exists() and not paths_are_same_file(temp_path, original_path):
                return
            temp_path.rename(original_path)
        except Exception:
            logger.debug("Failed to restore %s from %s", original_path, temp_path, exc_info=True)

    def _rename_path(self, source_path: Path, target_path: Path) -> None:
        if source_path == target_path:
            return
        if target_path.exists() and not paths_are_same_file(source_path, target_path):
            raise FileExistsError(f"target name already exists: {target_path.name}")
        if paths_are_same_file(source_path, target_path):
            temp_path = self._unique_temp_path(source_path)
            source_path.rename(temp_path)
            try:
                temp_path.rename(target_path)
            except Exception:
                self._restore_temp(temp_path, source_path)
                raise
            return
        source_path.rename(target_path)

    def _restore_staged(self, staged: list[tuple[_RenameJob, Path, Path | None]]) -> None:
        for job, audio_temp, lrc_temp in reversed(staged):
            if audio_temp.exists():
                self._restore_temp(lrc_temp, job.lrc_source)
                self._restore_temp(audio_temp, job.source)

    @Slot()
    def run(self) -> None:
        renamed = 0
        lrc_renamed = 0
        failed = 0
        failures: list[str] = []
        total = len(self.candidates)

        try:
            jobs: list[_RenameJob] = []
            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(renamed, lrc_renamed, failed, total, failures))
                    return

                source_path = Path(str(candidate.get("filepath") or ""))
                target_path = Path(str(candidate.get("suggested_path") or ""))
                relative_file = str(candidate.get("relative_file") or source_path.name)
                detail_label = f"Renaming {index}/{total}: {source_path.name}"
                self.progress.emit(index - 1, total, detail_label)

                if not source_path.name or not target_path.name:
                    failed += 1
                    failures.append(f"{relative_file}: missing source or target path")
                    continue

                if not self._is_path_within_target(source_path) or not self._is_path_within_target(target_path):
                    failed += 1
                    failures.append(f"{relative_file}: path is outside the scanned target")
                    continue

                if not source_path.exists():
                    failed += 1
                    failures.append(f"{relative_file}: source file no longer exists")
                    continue

                source_lrc_path = source_path.with_suffix(".lrc")
                target_lrc_path = target_path.with_suffix(".lrc")
                jobs.append(
                    _RenameJob(
                        source=source_path,
                        target=target_path,
                        relative=relative_file,
                        label=detail_label,
                        lrc_needed=source_lrc_path.exists() and target_lrc_path != source_lrc_path,
                        lrc_source=source_lrc_path,
                        lrc_target=target_lrc_path,
                    )
                )

            vacating = {self._path_key(job.source) for job in jobs}
            for job in jobs:
                if job.lrc_needed:
                    vacating.add(self._path_key(job.lrc_source))

            ready: list[_RenameJob] = []
            for job in jobs:
                if job.target.exists() and not paths_are_same_file(job.source, job.target):
                    if self._path_key(job.target) not in vacating:
                        failed += 1
                        failures.append(f"{job.relative}: target name already exists")
                        continue
                if job.lrc_needed and job.lrc_target.exists() and not paths_are_same_file(job.lrc_source, job.lrc_target):
                    if self._path_key(job.lrc_target) not in vacating:
                        failed += 1
                        failures.append(f"{job.relative}: matching .lrc target already exists")
                        continue
                ready.append(job)

            staged: list[tuple[_RenameJob, Path, Path | None]] = []
            try:
                for job in ready:
                    if self._cancel_requested:
                        break
                    audio_temp = self._unique_temp_path(job.source)
                    job.source.rename(audio_temp)
                    lrc_temp: Path | None = None
                    if job.lrc_needed:
                        try:
                            lrc_temp = self._unique_temp_path(job.lrc_source)
                            job.lrc_source.rename(lrc_temp)
                        except Exception as lrc_exc:
                            self._restore_temp(audio_temp, job.source)
                            failed += 1
                            failures.append(f"{job.relative}: failed to stage matching .lrc: {lrc_exc}")
                            continue
                    staged.append((job, audio_temp, lrc_temp))

                if not self._cancel_requested:
                    for job, audio_temp, lrc_temp in staged:
                        if self._cancel_requested:
                            self._restore_temp(lrc_temp, job.lrc_source)
                            self._restore_temp(audio_temp, job.source)
                            continue

                        try:
                            if job.target.exists() and not paths_are_same_file(audio_temp, job.target):
                                raise FileExistsError(f"target name already exists: {job.target.name}")
                            self._rename_path(audio_temp, job.target)
                        except Exception as exc:
                            self._restore_temp(lrc_temp, job.lrc_source)
                            self._restore_temp(audio_temp, job.source)
                            failed += 1
                            failures.append(f"{job.relative}: {exc}")
                            continue

                        lrc_was_renamed = False
                        if lrc_temp is not None:
                            try:
                                if job.lrc_target.exists() and not paths_are_same_file(lrc_temp, job.lrc_target):
                                    raise FileExistsError(
                                        f"matching .lrc target already exists: {job.lrc_target.name}"
                                    )
                                self._rename_path(lrc_temp, job.lrc_target)
                                lrc_was_renamed = True
                                lrc_renamed += 1
                            except Exception as lrc_exc:
                                rollback_reason = ""
                                try:
                                    self._rename_path(job.target, job.source)
                                except Exception as rollback_exc:
                                    rollback_reason = f"; rollback failed: {rollback_exc}"
                                self._restore_temp(lrc_temp, job.lrc_source)
                                failed += 1
                                failures.append(
                                    f"{job.relative}: renamed audio but failed to rename matching .lrc: {lrc_exc}{rollback_reason}"
                                )
                                continue

                        renamed += 1
                        processed = {
                            "old_path": str(job.source),
                            "new_path": str(job.target),
                        }
                        if lrc_was_renamed:
                            processed["old_lrc"] = str(job.lrc_source)
                            processed["new_lrc"] = str(job.lrc_target)
                        self._processed_paths.append(processed)
                        self.progress.emit(renamed + failed, total, job.label)
            finally:
                if self._cancel_requested:
                    self._restore_staged(staged)

            if self._cancel_requested:
                self.cancelled.emit(self._payload(renamed, lrc_renamed, failed, total, failures))
                return

            self.finished.emit(self._payload(renamed, lrc_renamed, failed, total, failures))
        except Exception as exc:
            logger.exception("File rename worker failed")
            self.failed.emit(str(exc))

    def _payload(
        self,
        renamed: int,
        lrc_renamed: int,
        failed: int,
        total: int,
        failures: list[str],
    ) -> dict[str, object]:
        return {
            "renamed": renamed,
            "lrc_renamed": lrc_renamed,
            "failed": failed,
            "total": total,
            "failures": failures,
            "processed_paths": list(self._processed_paths),
        }
