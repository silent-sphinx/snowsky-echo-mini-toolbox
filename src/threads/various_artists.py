"""Background worker that writes normalised artist tags."""

from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PySide6.QtCore import QObject, Signal, Slot

from ..utils.metadata_writer import save_metadata

logger = logging.getLogger(__name__)

# Saves are latency-bound on network shares, so overlap a few at a time.
MAX_PARALLEL_SAVES = 4


class VariousArtistsWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)

    def __init__(self, jobs: list[tuple[str, str, dict[str, str]]]):
        """`jobs` holds (filepath, filename, tags) per track."""
        super().__init__()
        self.jobs = jobs
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        updated_paths: list[str] = []
        errors: list[str] = []
        total = len(self.jobs)
        done = 0

        try:
            self.progress.emit(0, total, f"Updating {total} songs...")
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SAVES) as pool:
                futures = {
                    pool.submit(save_metadata, filepath, tags): (filepath, filename)
                    for filepath, filename, tags in self.jobs
                }
                remaining = set(futures)
                while remaining:
                    completed, remaining = wait(remaining, return_when=FIRST_COMPLETED)
                    for future in completed:
                        filepath, filename = futures[future]
                        ok, message = future.result()
                        if ok:
                            updated_paths.append(filepath)
                        else:
                            errors.append(f"{filename}: {message}")
                        done += 1
                        self.progress.emit(done, total, f"Updated {done} of {total}: {filename}")

                    if self._cancel_requested:
                        for future in remaining:
                            future.cancel()
                        for future in wait(remaining).done:
                            if not future.cancelled() and future.result()[0]:
                                updated_paths.append(futures[future][0])
                        self.cancelled.emit(self._payload(updated_paths, errors, total))
                        return

            self.finished.emit(self._payload(updated_paths, errors, total))
        except Exception as exc:
            logger.exception("Various Artists normalisation failed")
            self.failed.emit(str(exc))

    @staticmethod
    def _payload(updated_paths: list[str], errors: list[str], total: int) -> dict[str, object]:
        return {"updated_paths": list(updated_paths), "errors": list(errors), "total": total}
