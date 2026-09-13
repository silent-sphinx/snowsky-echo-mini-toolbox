"""Background workers for zip backup and copy/move of the scanned target."""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..constants import SYSTEM_FOLDERS

logger = logging.getLogger(__name__)


def destination_is_within_source(source: Path, destination: Path) -> bool:
    try:
        source_resolved = source.resolve()
        destination_resolved = destination.resolve(strict=False)
    except Exception:
        source_resolved = source
        destination_resolved = destination
    if destination_resolved == source_resolved:
        return True
    return source_resolved in destination_resolved.parents


def collect_backup_files(source: Path, cancel_requested) -> tuple[list[Path], int]:
    """Return (files, skipped symlink count). Raises InterruptedError if cancelled."""
    source_files: list[Path] = []
    skipped = 0
    for root_dir, dir_names, file_names in os.walk(str(source)):
        dir_names[:] = [d for d in dir_names if d.lower() not in SYSTEM_FOLDERS]
        if cancel_requested():
            raise InterruptedError("cancelled")
        root_path = Path(root_dir)
        for file_name in file_names:
            file_path = root_path / file_name
            if file_path.is_symlink():
                skipped += 1
                continue
            if file_path.is_file():
                source_files.append(file_path)
    return source_files, skipped


class ZipBackupWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, source_path: Path, zip_path: Path):
        super().__init__()
        self.source_path = source_path
        self.zip_path = zip_path
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _cancel_requested_fn(self) -> bool:
        return self._cancel_requested

    @Slot()
    def run(self) -> None:
        processed = 0
        total_files = 0
        skipped = 0
        try:
            source = self.source_path
            zip_path = self.zip_path

            if not source.exists() or not source.is_dir():
                raise RuntimeError("Source folder is not available.")

            source_files, skipped = collect_backup_files(source, self._cancel_requested_fn)
            total_files = len(source_files)

            empty_dirs: list[Path] = []
            for root_dir, dir_names, file_names in os.walk(str(source)):
                dir_names[:] = [d for d in dir_names if d.lower() not in SYSTEM_FOLDERS]
                if self._cancel_requested:
                    raise InterruptedError("cancelled")
                root_path = Path(root_dir)
                rel_dir = root_path.relative_to(source)
                if rel_dir != Path(".") and not dir_names and not file_names:
                    empty_dirs.append(rel_dir)

            zip_path.parent.mkdir(parents=True, exist_ok=True)
            root_name = source.name.strip() or "backup"

            with zipfile.ZipFile(
                str(zip_path),
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                archive.writestr(f"{root_name}/", "")
                for empty_dir in empty_dirs:
                    arc_dir = (Path(root_name) / empty_dir).as_posix()
                    archive.writestr(f"{arc_dir}/", "")

                for file_path in source_files:
                    if self._cancel_requested:
                        raise InterruptedError("cancelled")

                    rel_file = file_path.relative_to(source).as_posix()
                    arc_name = (Path(root_name) / rel_file).as_posix()
                    archive.write(str(file_path), arcname=arc_name)
                    processed += 1
                    self.progress.emit(processed, max(total_files, 1), rel_file)

            self.finished.emit(
                {
                    "zip_path": str(zip_path),
                    "processed": processed,
                    "total": total_files,
                    "skipped": skipped,
                }
            )
        except InterruptedError:
            try:
                if self.zip_path.exists():
                    self.zip_path.unlink()
            except Exception:
                pass
            self.cancelled.emit(
                {
                    "processed": processed,
                    "total": total_files,
                    "partial_zip": str(self.zip_path),
                }
            )
        except Exception as exc:
            logger.exception("Zip backup failed")
            self.failed.emit(str(exc))


class FileTransferWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, source_path: Path, destination_path: Path, mode: str):
        super().__init__()
        self.source_path = source_path
        self.destination_path = destination_path
        self.mode = "move" if mode == "move" else "copy"
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _cancel_requested_fn(self) -> bool:
        return self._cancel_requested

    def _cancel_payload(self, processed: int, total_files: int) -> dict[str, object]:
        return {
            "processed": processed,
            "total": total_files,
            "destination": str(self.destination_path),
            "mode": self.mode,
        }

    @Slot()
    def run(self) -> None:
        processed = 0
        total_files = 0
        skipped = 0
        try:
            source = self.source_path
            destination = self.destination_path

            if not source.exists() or not source.is_dir():
                raise RuntimeError("Source folder is not available.")

            source_files, skipped = collect_backup_files(source, self._cancel_requested_fn)
            total_files = len(source_files)

            destination.mkdir(parents=True, exist_ok=True)
            for current_dir, dir_names, _file_names in os.walk(str(source)):
                dir_names[:] = [d for d in dir_names if d.lower() not in SYSTEM_FOLDERS]
                if self._cancel_requested:
                    self.cancelled.emit(self._cancel_payload(processed, total_files))
                    return

                current_dir_path = Path(current_dir)
                rel_dir = current_dir_path.relative_to(source)
                for dir_name in dir_names:
                    (destination / rel_dir / dir_name).mkdir(parents=True, exist_ok=True)

            for source_file in source_files:
                if self._cancel_requested:
                    self.cancelled.emit(self._cancel_payload(processed, total_files))
                    return

                rel_file = source_file.relative_to(source)
                target_file = destination / rel_file
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source_file), str(target_file))
                processed += 1
                self.progress.emit(processed, max(total_files, 1), rel_file.as_posix())

            if self.mode == "move":
                for source_file in source_files:
                    try:
                        source_file.unlink()
                    except Exception:
                        pass

                for current_dir, _dir_names, _file_names in os.walk(str(source), topdown=False):
                    try:
                        Path(current_dir).rmdir()
                    except Exception:
                        continue

            self.finished.emit(
                {
                    "destination": str(destination),
                    "processed": processed,
                    "total": total_files,
                    "skipped": skipped,
                    "mode": self.mode,
                }
            )
        except InterruptedError:
            self.cancelled.emit(self._cancel_payload(processed, total_files))
        except Exception as exc:
            logger.exception("File transfer failed")
            self.failed.emit(str(exc))
