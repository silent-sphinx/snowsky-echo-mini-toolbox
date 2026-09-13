"""Background worker for applying File Rename suggestions."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..utils.file_rename import paths_are_same_file

logger = logging.getLogger(__name__)


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
        except ValueError:
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

    def _rename_path(self, source_path: Path, target_path: Path) -> None:
        if source_path == target_path:
            return
        if paths_are_same_file(source_path, target_path):
            temp_path = self._unique_temp_path(source_path)
            source_path.rename(temp_path)
            try:
                temp_path.rename(target_path)
            except Exception:
                try:
                    temp_path.rename(source_path)
                except Exception:
                    pass
                raise
            return
        source_path.rename(target_path)

    @Slot()
    def run(self) -> None:
        renamed = 0
        lrc_renamed = 0
        failed = 0
        failures: list[str] = []
        total = len(self.candidates)

        try:
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
                    self.progress.emit(index, total, detail_label)
                    continue

                if not self._is_path_within_target(source_path) or not self._is_path_within_target(target_path):
                    failed += 1
                    failures.append(f"{relative_file}: path is outside the scanned target")
                    self.progress.emit(index, total, detail_label)
                    continue

                if not source_path.exists():
                    failed += 1
                    failures.append(f"{relative_file}: source file no longer exists")
                    self.progress.emit(index, total, detail_label)
                    continue

                if target_path.exists() and not paths_are_same_file(source_path, target_path):
                    failed += 1
                    failures.append(f"{relative_file}: target name already exists")
                    self.progress.emit(index, total, detail_label)
                    continue

                source_lrc_path = source_path.with_suffix(".lrc")
                target_lrc_path = target_path.with_suffix(".lrc")
                lrc_needed = source_lrc_path.exists() and target_lrc_path != source_lrc_path

                if lrc_needed and target_lrc_path.exists() and not paths_are_same_file(source_lrc_path, target_lrc_path):
                    failed += 1
                    failures.append(f"{relative_file}: matching .lrc target already exists")
                    self.progress.emit(index, total, detail_label)
                    continue

                try:
                    self._rename_path(source_path, target_path)
                except Exception as exc:
                    failed += 1
                    failures.append(f"{relative_file}: {exc}")
                    self.progress.emit(index, total, detail_label)
                    continue

                lrc_was_renamed = False
                if lrc_needed:
                    try:
                        self._rename_path(source_lrc_path, target_lrc_path)
                        lrc_was_renamed = True
                        lrc_renamed += 1
                    except Exception as lrc_exc:
                        rollback_reason = ""
                        try:
                            self._rename_path(target_path, source_path)
                        except Exception as rollback_exc:
                            rollback_reason = f"; rollback failed: {rollback_exc}"
                        failed += 1
                        failures.append(
                            f"{relative_file}: renamed audio but failed to rename matching .lrc: {lrc_exc}{rollback_reason}"
                        )
                        self.progress.emit(index, total, detail_label)
                        continue

                renamed += 1
                processed = {
                    "old_path": str(source_path),
                    "new_path": str(target_path),
                }
                if lrc_was_renamed:
                    processed["old_lrc"] = str(source_lrc_path)
                    processed["new_lrc"] = str(target_lrc_path)
                self._processed_paths.append(processed)
                self.progress.emit(index, total, detail_label)

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
