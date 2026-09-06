"""Background worker for LRCLIB lyrics lookups."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from ..models.drive_data import TrackMetadata
from ..utils.lyrics_lookup import LookupCancelled, LyricsLookupClient, lookup_query_for_track
from ..utils.lyrics_planner import result_from_lookup

logger = logging.getLogger(__name__)


class LyricsLookupWorker(QObject):
    """Look up one LRCLIB result per selected track."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        tracks: list[TrackMetadata],
        root_path: str,
        client: LyricsLookupClient | None = None,
    ):
        super().__init__()
        self.tracks = tracks
        self.root_path = root_path
        self._cancel_requested = False
        self.client = client or LyricsLookupClient()
        self.client.set_cancel_check(lambda: self._cancel_requested)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _payload(self, processed: int, total: int, results: list, found: int) -> dict[str, object]:
        return {
            "results": results,
            "processed": processed,
            "total": total,
            "found": found,
        }

    @Slot()
    def run(self) -> None:
        total = len(self.tracks)
        results = []
        found = 0

        try:
            for index, track in enumerate(self.tracks, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(index - 1, total, results, found))
                    return

                query = lookup_query_for_track(track)
                label = f"Searching {index}/{total}: {query.artist} — {query.title}"
                self.progress.emit(index - 1, total, label)

                if query.error:
                    results.append(
                        result_from_lookup(
                            track,
                            self.root_path,
                            query,
                            "Missing metadata",
                            query.error,
                            "",
                        )
                    )
                    self.progress.emit(index, total, label)
                    continue

                try:
                    status, source, lyrics_text = self.client.lookup(
                        query.title, query.artist, query.album, query.duration
                    )
                except LookupCancelled:
                    self.cancelled.emit(self._payload(index - 1, total, results, found))
                    return
                except Exception as exc:
                    logger.debug("Lookup failed for %s", track.filepath, exc_info=True)
                    results.append(
                        result_from_lookup(
                            track,
                            self.root_path,
                            query,
                            "Error",
                            f"Lookup failed: {exc}",
                            "",
                        )
                    )
                    self.progress.emit(index, total, label)
                    continue

                result = result_from_lookup(
                    track, self.root_path, query, status, source, lyrics_text
                )
                if result.can_apply:
                    found += 1
                results.append(result)
                self.progress.emit(index, total, label)

            self.finished.emit(self._payload(total, total, results, found))
        except Exception as exc:
            self.failed.emit(str(exc))
