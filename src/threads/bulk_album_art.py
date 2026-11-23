"""Background worker for embedding one cover image into, or stripping art from, many files."""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..models.drive_data import TrackMetadata
from ..utils.album_art import remove_embedded_album_art, write_embedded_album_art
from ..utils.album_art_validation import evaluate_album_art

# Writes are latency-bound on network shares, so overlap a few at a time.
MAX_PARALLEL_WRITES = 4


class BulkAlbumArtWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)

    def __init__(self, tracks: list[TrackMetadata], jpeg_data: bytes | None):
        """Embed jpeg_data in every track, or remove embedded art when it is None."""
        super().__init__()
        self.tracks = tracks
        self.jpeg_data = jpeg_data
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _apply(self, track: TrackMetadata) -> tuple[bool, str, dict | None, int]:
        path = Path(track.filepath)
        if self.jpeg_data is None:
            ok, message = remove_embedded_album_art(path)
        else:
            ok, message = write_embedded_album_art(path, self.jpeg_data)
        if not ok:
            return False, message, None, 0
        try:
            size = os.path.getsize(path)
        except OSError:
            size = track.size_bytes
        return True, message, evaluate_album_art(path), size

    @Slot()
    def run(self) -> None:
        results: dict[str, tuple[dict, int]] = {}
        errors: list[str] = []
        total = len(self.tracks)
        done = 0

        try:
            self.progress.emit(0, total, f"Updating {total} songs...")
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WRITES) as pool:
                futures = {pool.submit(self._apply, track): track for track in self.tracks}
                remaining = set(futures)
                while remaining:
                    completed, remaining = wait(remaining, return_when=FIRST_COMPLETED)
                    for future in completed:
                        track = futures[future]
                        ok, message, art_result, size = future.result()
                        if ok:
                            results[track.filepath] = (art_result, size)
                        else:
                            errors.append(f"{track.filename}: {message}")
                        done += 1
                        self.progress.emit(done, total, f"Updated {done} of {total}: {track.filename}")

                    if self._cancel_requested and remaining:
                        for future in remaining:
                            future.cancel()
                        # Writes already running finish; keep their results.
                        for future in wait(remaining).done:
                            if future.cancelled():
                                continue
                            ok, message, art_result, size = future.result()
                            track = futures[future]
                            if ok:
                                results[track.filepath] = (art_result, size)
                            else:
                                errors.append(f"{track.filename}: {message}")
                        self.cancelled.emit(self._payload(results, errors, total))
                        return

            self.finished.emit(self._payload(results, errors, total))
        except Exception as exc:
            self.failed.emit(str(exc))

    @staticmethod
    def _payload(results: dict, errors: list[str], total: int) -> dict[str, object]:
        return {"results": dict(results), "errors": list(errors), "total": total}
