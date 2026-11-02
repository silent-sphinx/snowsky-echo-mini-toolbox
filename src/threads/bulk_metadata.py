"""Background worker for applying tag changes to many files at once."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PySide6.QtCore import QObject, Signal, Slot

from ..models.drive_data import TrackMetadata
from ..utils.metadata_writer import TAG_KEY_ALIASES, aliases_for_tag_key, save_metadata

# Saves are latency-bound on network shares, so overlap a few at a time.
MAX_PARALLEL_SAVES = 4


def tags_already_match(track: TrackMetadata, tags: dict[str, str | None]) -> bool:
    """Report whether the scanned tags already hold every requested value."""
    if not tags or any(key.lower() not in TAG_KEY_ALIASES for key in tags):
        return False

    for key, val in tags.items():
        aliases = {alias.lower() for alias in aliases_for_tag_key(key)}
        aliases.add(f"txxx:{key.lower()}")
        existing = [v for k, v in track.all_tags.items() if k.lower() in aliases]
        if val is None:
            if existing:
                return False
        elif not existing or any(v != val for v in existing):
            return False
    return True


class BulkMetadataWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)

    def __init__(self, tracks: list[TrackMetadata], tags: dict[str, str | None]):
        super().__init__()
        self.tracks = tracks
        self.tags = tags
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        updated_paths: list[str] = []
        errors: list[str] = []
        total = len(self.tracks)
        done = 0
        skipped = 0

        try:
            pending_tracks: list[TrackMetadata] = []
            seen: set[str] = set()
            for track in self.tracks:
                if track.filepath in seen or tags_already_match(track, self.tags):
                    skipped += 1
                else:
                    pending_tracks.append(track)
                seen.add(track.filepath)
            done = skipped
            self.progress.emit(done, total, f"Updating {len(pending_tracks)} songs...")

            with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SAVES) as pool:
                futures = {
                    pool.submit(save_metadata, track.filepath, self.tags): track
                    for track in pending_tracks
                }
                remaining = set(futures)
                while remaining:
                    completed, remaining = wait(remaining, return_when=FIRST_COMPLETED)
                    for future in completed:
                        track = futures[future]
                        ok, message = future.result()
                        if ok:
                            updated_paths.append(track.filepath)
                        else:
                            errors.append(f"{track.filename}: {message}")
                        done += 1
                        self.progress.emit(
                            done, total, f"Updated {done} of {total}: {track.filename}"
                        )

                    if self._cancel_requested:
                        for future in remaining:
                            future.cancel()
                        for future in wait(remaining).done:
                            if not future.cancelled() and future.result()[0]:
                                updated_paths.append(futures[future].filepath)
                        self.cancelled.emit(
                            self._payload(updated_paths, errors, total, skipped)
                        )
                        return

            self.finished.emit(self._payload(updated_paths, errors, total, skipped))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _payload(
        self, updated_paths: list[str], errors: list[str], total: int, skipped: int
    ) -> dict[str, object]:
        return {
            "updated_paths": list(updated_paths),
            "errors": list(errors),
            "total": total,
            "skipped": skipped,
        }
