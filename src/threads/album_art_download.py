"""Background workers for looking up and applying downloaded album covers."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from ..utils.file_cleanup import path_is_within_target

logger = logging.getLogger(__name__)

# Keeps progress labels from stretching the progress dialog.
MAX_LABEL_CHARS = 70

# Albums searched at once; MusicBrainz calls stay throttled across all of them.
ALBUM_LOOKUP_WORKERS = 4


def _elide(text: str, limit: int = MAX_LABEL_CHARS) -> str:
    if len(text) <= limit:
        return text
    keep = (limit - 3) // 2
    return f"{text[:keep]}...{text[-keep:]}"


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
        done = 0
        updates: list[dict[str, object]] = []
        progress_lock = threading.Lock()

        def report(group: AlbumGroup, step: str) -> None:
            name = _elide(f"{group.display_artist} - {group.album}")
            with progress_lock:
                self.progress.emit(done, total, f"Searched {done}/{total} album(s)\n{name}: {step}")

        try:
            self.progress.emit(0, total, f"Searching {total} album(s)...")
            # Leaving the with-block waits for in-flight searches, so no pool
            # thread can emit through this worker after it is deleted.
            with ThreadPoolExecutor(max_workers=max(1, min(ALBUM_LOOKUP_WORKERS, total))) as pool:
                futures = [
                    pool.submit(self._lookup, index, group, report)
                    for index, group in enumerate(self.groups)
                ]
                for future in as_completed(futures):
                    try:
                        update = future.result()
                    except LookupCancelled:
                        break
                    if self._cancel_requested:
                        break
                    with progress_lock:
                        done += 1
                        updates.append(update)
                    if update["is_selected"]:
                        found += 1
                    group = self.groups[update["index"]]
                    outcome = "Cover found" if update["is_selected"] else "No cover found"
                    report(group, outcome)
                pool.shutdown(wait=True, cancel_futures=True)

            if self._cancel_requested:
                self.cancelled.emit(self._payload(done, total, found, updates))
                return
            self.finished.emit(self._payload(total, total, found, updates))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _lookup(self, index: int, group: AlbumGroup, report) -> dict[str, object]:
        """Search one album; runs on a pool thread."""
        if self._cancel_requested:
            raise LookupCancelled()
        try:
            result = self.client.search_album(
                group.artist,
                group.album,
                year=group.year,
                on_status=lambda step: report(group, step),
            )
        except LookupCancelled:
            raise
        except Exception as exc:
            logger.debug("Lookup failed for %s", group.key, exc_info=True)
            return {
                "index": index,
                "candidates": [],
                "error": f"Lookup failed: {exc}",
                "query": "",
                "selected_index": 0,
                "is_selected": False,
            }
        return {
            "index": index,
            "candidates": result.candidates,
            "error": result.error,
            "query": result.query,
            "selected_index": 0,
            "is_selected": result.has_candidates,
        }

    def _payload(
        self,
        processed: int,
        total: int,
        found: int,
        updates: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "groups": self.groups,
            "updates": list(updates or []),
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

    def _backup_original_file(self, source_path: Path) -> tuple[bool, str]:
        if self.backup_root is None:
            return True, ""

        # Derive from the resolved path so the backup always lands under backup_root.
        try:
            relative = source_path.resolve().relative_to(self.target_path.resolve())
        except (ValueError, OSError):
            relative = Path(source_path.name)
        backup_target = self.backup_root / relative
        try:
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            backup_target = self._next_available_backup_path(backup_target)
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
        files_done = 0
        failures: list[str] = []
        total_albums = len(self.candidates)
        total_files = sum(len(c.get("filepaths") or []) for c in self.candidates)

        def cancel() -> None:
            self.cancelled.emit(
                self._payload(written, failed, planned, albums_done, total_albums,
                              total_files, failures)
            )

        try:
            if self.backup_root is not None and not self.dry_run:
                self.backup_root.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    cancel()
                    return

                album = str(candidate.get("album") or "")
                artist = str(candidate.get("artist") or "")
                filepaths = list(candidate.get("filepaths") or [])
                relative_files = list(candidate.get("relative_files") or [])
                image_url = str(candidate.get("image_url") or "")
                label = _elide(f"Album {index}/{total_albums}: {artist} - {album}")

                if self.dry_run:
                    planned += len(filepaths)
                    files_done += len(filepaths)
                    albums_done += 1
                    self.progress.emit(
                        files_done, total_files,
                        f"{label}\nWould apply to {len(filepaths)} file(s)",
                    )
                    continue

                if not image_url:
                    failed += len(filepaths)
                    failures.append(f"{album}: no cover selected")
                    files_done += len(filepaths)
                    albums_done += 1
                    self.progress.emit(files_done, total_files, f"{label}\nNo cover selected")
                    continue

                self.progress.emit(files_done, total_files, f"{label}\nDownloading cover")

                # One download per album, reused across every member track.
                try:
                    raw_image = self.client.fetch_image(image_url)
                except LookupCancelled:
                    cancel()
                    return
                except Exception as exc:
                    failed += len(filepaths)
                    failures.append(f"{album}: download failed ({exc})")
                    files_done += len(filepaths)
                    albums_done += 1
                    self.progress.emit(files_done, total_files, f"{label}\nDownload failed")
                    continue

                jpeg_data = to_non_progressive_jpeg(raw_image, self.quality)
                if not jpeg_data:
                    failed += len(filepaths)
                    failures.append(f"{album}: downloaded cover could not be decoded")
                    files_done += len(filepaths)
                    albums_done += 1
                    self.progress.emit(files_done, total_files, f"{label}\nCover could not be decoded")
                    continue

                for position, filepath in enumerate(filepaths):
                    if self._cancel_requested:
                        cancel()
                        return

                    source_path = Path(filepath)
                    relative_file = (
                        relative_files[position]
                        if position < len(relative_files)
                        else source_path.name
                    )
                    self.progress.emit(
                        files_done, total_files,
                        f"{label}\n" + _elide(f"Writing {position + 1}/{len(filepaths)}: {relative_file}"),
                    )
                    error = self._apply_to_file(source_path, jpeg_data)
                    if error:
                        failed += 1
                        failures.append(f"{relative_file}: {error}")
                    else:
                        written += 1
                    files_done += 1

                albums_done += 1
                self.progress.emit(files_done, total_files, f"{label}\nDone")

            self.finished.emit(
                self._payload(written, failed, planned, albums_done, total_albums,
                              total_files, failures, include_backup=True)
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    def _apply_to_file(self, source_path: Path, jpeg_data: bytes) -> str:
        """Embed the cover into one file; return an error message or ''."""
        if source_path.is_symlink() or not path_is_within_target(source_path, self.target_path):
            return "path is outside the scanned target"
        if not source_path.exists():
            return "file no longer exists"

        ok, backup_error = self._backup_original_file(source_path)
        if not ok:
            return backup_error

        ok, write_message = write_embedded_album_art(source_path, jpeg_data)
        if not ok:
            return write_message

        # The file changed on disk even if verification below fails.
        self._processed_paths.append({"filepath": str(source_path)})

        verified_art, verified_mime = read_embedded_album_art(source_path)
        if not verified_art:
            return "artwork missing after write"
        if verified_mime.lower() != "image/jpeg":
            return f"artwork saved as {verified_mime}"
        if jpeg_scan_type(verified_art) == "Progressive":
            return "artwork is still a progressive JPEG"
        return ""

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
