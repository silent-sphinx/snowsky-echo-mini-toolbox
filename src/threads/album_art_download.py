"""Background workers for looking up and applying downloaded album covers."""

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
from ..utils.album_art_download import AlbumArtLookupClient, LookupCancelled
from ..utils.album_art_download_planner import AlbumGroup

logger = logging.getLogger(__name__)


class AlbumArtLookupWorker(QObject):
    """Searches one cover set per album group, never per track."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, groups: list[AlbumGroup], client: AlbumArtLookupClient | None = None):
        super().__init__()
        self.groups = groups
        self._cancel_requested = False
        self.client = client or AlbumArtLookupClient()
        self.client.set_cancel_check(lambda: self._cancel_requested)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        total = len(self.groups)
        found = 0

        try:
            for index, group in enumerate(self.groups, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(index - 1, total, found))
                    return

                label = f"Searching {index}/{total}: {group.display_artist} — {group.album}"
                self.progress.emit(index - 1, total, label)

                try:
                    result = self.client.search_album(
                        group.artist, group.album, year=group.year
                    )
                except LookupCancelled:
                    self.cancelled.emit(self._payload(index - 1, total, found))
                    return
                except Exception as exc:
                    logger.debug("Lookup failed for %s", group.key, exc_info=True)
                    group.candidates = []
                    group.error = f"Lookup failed: {exc}"
                    group.is_selected = False
                    self.progress.emit(index, total, label)
                    continue

                group.candidates = result.candidates
                group.error = result.error
                group.query = result.query
                group.selected_index = 0
                group.is_selected = result.has_candidates
                if result.has_candidates:
                    found += 1

                self.progress.emit(index, total, label)

            self.finished.emit(self._payload(total, total, found))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _payload(self, processed: int, total: int, found: int) -> dict[str, object]:
        return {
            "groups": self.groups,
            "processed": processed,
            "total": total,
            "found": found,
        }


class AlbumArtApplyWorker(QObject):
    """Downloads one image per album group and embeds it into every member."""

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
        client: AlbumArtLookupClient | None = None,
    ):
        super().__init__()
        self.target_path = target_path
        self.candidates = candidates
        self.quality = quality
        self.dry_run = dry_run
        self.backup_root = backup_root
        self._cancel_requested = False
        self.client = client or AlbumArtLookupClient()
        self.client.set_cancel_check(lambda: self._cancel_requested)
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

    @Slot()
    def run(self) -> None:
        written = 0
        failed = 0
        planned = 0
        albums_done = 0
        failures: list[str] = []
        total_albums = len(self.candidates)
        total_files = sum(len(c.get("filepaths") or []) for c in self.candidates)

        try:
            if self.backup_root is not None and not self.dry_run:
                self.backup_root.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(
                        self._payload(written, failed, planned, albums_done, total_albums,
                                      total_files, failures)
                    )
                    return

                album = str(candidate.get("album") or "")
                artist = str(candidate.get("artist") or "")
                filepaths = list(candidate.get("filepaths") or [])
                relative_files = list(candidate.get("relative_files") or [])
                image_url = str(candidate.get("image_url") or "")
                label = f"Album {index}/{total_albums}: {artist} — {album}"

                self.progress.emit(albums_done, total_albums, label)

                if self.dry_run:
                    planned += len(filepaths)
                    albums_done += 1
                    self.progress.emit(
                        albums_done, total_albums,
                        f"Would apply to {len(filepaths)} file(s): {album}",
                    )
                    continue

                if not image_url:
                    failed += len(filepaths)
                    failures.append(f"{album}: no cover selected")
                    albums_done += 1
                    continue

                # One download per album, reused across every member track.
                try:
                    raw_image = self.client.fetch_image(image_url)
                except LookupCancelled:
                    self.cancelled.emit(
                        self._payload(written, failed, planned, albums_done, total_albums,
                                      total_files, failures)
                    )
                    return
                except Exception as exc:
                    failed += len(filepaths)
                    failures.append(f"{album}: download failed ({exc})")
                    albums_done += 1
                    continue

                jpeg_data = to_non_progressive_jpeg(raw_image, self.quality)
                if not jpeg_data:
                    failed += len(filepaths)
                    failures.append(f"{album}: downloaded cover could not be decoded")
                    albums_done += 1
                    continue

                for position, filepath in enumerate(filepaths):
                    if self._cancel_requested:
                        self.cancelled.emit(
                            self._payload(written, failed, planned, albums_done, total_albums,
                                          total_files, failures)
                        )
                        return

                    source_path = Path(filepath)
                    relative_file = (
                        relative_files[position]
                        if position < len(relative_files)
                        else source_path.name
                    )

                    if not source_path.exists():
                        failed += 1
                        failures.append(f"{relative_file}: file no longer exists")
                        continue

                    ok, backup_error = self._backup_original_file(source_path, relative_file)
                    if not ok:
                        failed += 1
                        failures.append(f"{relative_file}: {backup_error}")
                        continue

                    ok, write_message = write_embedded_album_art(source_path, jpeg_data)
                    if not ok:
                        failed += 1
                        failures.append(f"{relative_file}: {write_message}")
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
                        written += 1
                        self._processed_paths.append({"filepath": str(source_path)})

                albums_done += 1
                self.progress.emit(albums_done, total_albums, label)

            self.finished.emit(
                self._payload(written, failed, planned, albums_done, total_albums,
                              total_files, failures, include_backup=True)
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    def _payload(
        self,
        written: int,
        failed: int,
        planned: int,
        albums_done: int,
        total_albums: int,
        total_files: int,
        failures: list[str],
        *,
        include_backup: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "written": written,
            "failed": failed,
            "planned": planned,
            "albums_done": albums_done,
            "total_albums": total_albums,
            "total_files": total_files,
            "failures": failures,
            "dry_run": self.dry_run,
            "processed_paths": list(self._processed_paths),
        }
        if include_backup:
            payload["backup_root"] = str(self.backup_root) if self.backup_root is not None else ""
        return payload
