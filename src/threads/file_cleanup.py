"""Background workers for File Cleanup scan and delete."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..utils.file_cleanup import (
    CleanupTypeStats,
    classify_file,
    format_bytes,
    is_system_folder,
    path_is_within_target,
)

logger = logging.getLogger(__name__)


class FileCleanupScanWorker(QObject):
    # Byte totals can exceed 32-bit Qt int; keep them as object.
    progress = Signal(int, object, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, target_path: str):
        super().__init__()
        self.target_path = target_path
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            stats_by_key: dict[str, CleanupTypeStats] = {}
            scanned_files = 0
            total_bytes = 0

            for root_dir, dir_names, file_names in os.walk(self.target_path):
                dir_names[:] = [name for name in dir_names if not is_system_folder(name)]
                if self._cancel_requested:
                    self.cancelled.emit()
                    return

                root_path = Path(root_dir)
                for file_name in file_names:
                    if self._cancel_requested:
                        self.cancelled.emit()
                        return

                    file_path = root_path / file_name
                    try:
                        if file_path.is_symlink() or not file_path.is_file():
                            continue
                        file_size = file_path.stat().st_size
                    except OSError:
                        file_size = 0

                    key, label, category, extension, description = classify_file(file_path)
                    row = stats_by_key.get(key)
                    if row is None:
                        row = CleanupTypeStats(
                            key=key,
                            file_type=label,
                            category=category,
                            extension=extension,
                            description=description,
                            is_checked=category == "Hidden",
                        )
                        stats_by_key[key] = row

                    row.count += 1
                    row.size_bytes += int(file_size)
                    row.files.append(str(file_path))

                    scanned_files += 1
                    total_bytes += int(file_size)
                    if scanned_files % 400 == 0:
                        self.progress.emit(
                            scanned_files,
                            total_bytes,
                            f"Scanning… {scanned_files:,} files · {format_bytes(total_bytes)}",
                        )

            self.finished.emit({
                "rows": list(stats_by_key.values()),
                "total_files": scanned_files,
                "total_bytes": total_bytes,
                "found_types": len(stats_by_key),
                "target_path": self.target_path,
            })
        except Exception as exc:
            logger.exception("File cleanup scan failed")
            self.failed.emit(str(exc))


class FileCleanupDeleteWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, target_path: Path, file_paths: list[str]):
        super().__init__()
        self.target_path = target_path
        self.file_paths = file_paths
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        removed = 0
        failed = 0
        failures: list[str] = []
        removed_paths: list[str] = []
        total = len(self.file_paths)

        try:
            for index, raw_path in enumerate(self.file_paths, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(removed, failed, total, failures, removed_paths))
                    return

                file_path = Path(raw_path)
                detail = f"Removing {index}/{total}: {file_path.name}"
                self.progress.emit(index - 1, total, detail)

                if not file_path.name:
                    failed += 1
                    failures.append("missing path")
                    self.progress.emit(index, total, detail)
                    continue

                if not path_is_within_target(file_path, self.target_path):
                    failed += 1
                    failures.append(f"{file_path.name}: path is outside the scanned target")
                    self.progress.emit(index, total, detail)
                    continue

                try:
                    if file_path.is_symlink() or not file_path.is_file():
                        failed += 1
                        failures.append(f"{file_path.name}: not a regular file")
                        self.progress.emit(index, total, detail)
                        continue
                    file_path.unlink()
                    removed += 1
                    removed_paths.append(str(file_path))
                except Exception as exc:
                    failed += 1
                    failures.append(f"{file_path.name}: {exc}")

                self.progress.emit(index, total, detail)

            self.finished.emit(self._payload(removed, failed, total, failures, removed_paths))
        except Exception as exc:
            logger.exception("File cleanup delete failed")
            self.failed.emit(str(exc))

    def _payload(
        self,
        removed: int,
        failed: int,
        total: int,
        failures: list[str],
        removed_paths: list[str],
    ) -> dict[str, object]:
        return {
            "removed": removed,
            "failed": failed,
            "total": total,
            "failures": failures,
            "removed_paths": removed_paths,
        }
