"""Background worker for re-encoding incompatible embedded album artwork."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..utils.album_art import (
    jpeg_scan_type,
    read_embedded_album_art,
    to_non_progressive_jpeg,
    write_embedded_album_art,
)

logger = logging.getLogger(__name__)


class AlbumArtFixWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        target_path: Path,
        candidates: list[dict[str, object]],
        quality: int,
        dry_run: bool,
        backup_root: Path | None,
    ):
        super().__init__()
        self.target_path = target_path
        self.candidates = candidates
        self.quality = quality
        self.dry_run = dry_run
        self.backup_root = backup_root
        self._cancel_requested = False
        self._processed_paths: list[dict[str, str]] = []

    def request_cancel(self) -> None:
        self._cancel_requested = True

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
            if self.backup_root is not None and not self.dry_run:
                self.backup_root.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(converted, failed, planned, total, failures))
                    return

                source_path, relative_file = self._resolve_source_path(candidate)
                detail_label = f"Processing {index}/{total}: {source_path.name}"
                self.progress.emit(index - 1, total, detail_label)

                if not source_path.exists():
                    failed += 1
                    failures.append(f"{relative_file}: file no longer exists")
                    self.progress.emit(index, total, detail_label)
                    continue

                art_bytes, art_mime = read_embedded_album_art(source_path)
                if not art_bytes:
                    failed += 1
                    failures.append(f"{relative_file}: {art_mime}")
                    self.progress.emit(index, total, detail_label)
                    continue

                if self.dry_run:
                    planned += 1
                    self.progress.emit(index, total, f"Would re-encode: {source_path.name}")
                    continue

                ok, backup_error = self._backup_original_file(source_path, relative_file)
                if not ok:
                    failed += 1
                    failures.append(f"{relative_file}: {backup_error}")
                    self.progress.emit(index, total, detail_label)
                    continue

                jpeg_data = to_non_progressive_jpeg(art_bytes, self.quality)
                if not jpeg_data:
                    failed += 1
                    failures.append(f"{relative_file}: could not decode embedded artwork")
                    self.progress.emit(index, total, detail_label)
                    continue

                written, write_message = write_embedded_album_art(source_path, jpeg_data)
                if not written:
                    failed += 1
                    failures.append(f"{relative_file}: {write_message}")
                    self.progress.emit(index, total, detail_label)
                    continue

                verified_art, verified_mime = read_embedded_album_art(source_path)
                if not verified_art:
                    failed += 1
                    failures.append(f"{relative_file}: artwork missing after write")
                elif verified_mime.lower() != "image/jpeg":
                    failed += 1
                    failures.append(f"{relative_file}: artwork saved as {verified_mime}")
                elif jpeg_scan_type(verified_art) == "Progressive":
                    failed += 1
                    failures.append(f"{relative_file}: artwork is still a progressive JPEG")
                else:
                    converted += 1
                    self._processed_paths.append({"filepath": str(source_path)})

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
