"""Background worker for applying tag changes to many files at once."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from ..models.drive_data import TrackMetadata
from ..utils.metadata_writer import save_metadata


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

        try:
            for index, track in enumerate(self.tracks, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(updated_paths, errors, total))
                    return

                detail = f"Updating {index} of {total}: {track.filename}"
                self.progress.emit(index - 1, total, detail)

                ok, message = save_metadata(track.filepath, self.tags)
                if ok:
                    updated_paths.append(track.filepath)
                else:
                    errors.append(f"{track.filename}: {message}")

                self.progress.emit(index, total, detail)

            self.finished.emit(self._payload(updated_paths, errors, total))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _payload(
        self, updated_paths: list[str], errors: list[str], total: int
    ) -> dict[str, object]:
        return {
            "updated_paths": list(updated_paths),
            "errors": list(errors),
            "total": total,
        }
