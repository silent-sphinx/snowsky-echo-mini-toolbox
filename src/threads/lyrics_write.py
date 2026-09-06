"""Background worker for writing .lrc sidecar files."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..utils.lyrics import write_lrc_sidecar

logger = logging.getLogger(__name__)


class LyricsWriteWorker(QObject):
    """Write one .lrc sidecar per candidate (convert or lookup apply)."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        target_path: Path,
        candidates: list[dict[str, object]],
        dry_run: bool,
        backup_root: Path | None,
    ):
        super().__init__()
        self.target_path = target_path
        self.candidates = candidates
        self.dry_run = dry_run
        self.backup_root = backup_root
        self._cancel_requested = False
        self._processed_paths: list[dict[str, str]] = []

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _resolve_source_path(self, candidate: dict[str, object]) -> tuple[Path, str]:
        filepath = str(candidate.get("filepath") or "")
        relative_file = str(candidate.get("relative_file") or "")
        if filepath:
            return Path(filepath), relative_file or filepath
        return self.target_path / Path(relative_file), relative_file

    def _payload(
        self,
        written: int,
        failed: int,
        planned: int,
        total: int,
        failures: list[str],
    ) -> dict[str, object]:
        return {
            "written": written,
            "failed": failed,
            "planned": planned,
            "total": total,
            "dry_run": self.dry_run,
            "backup_root": str(self.backup_root or ""),
            "failures": failures,
            "processed_paths": list(self._processed_paths),
        }

    @Slot()
    def run(self) -> None:
        written = 0
        failed = 0
        planned = 0
        failures: list[str] = []
        total = len(self.candidates)
        claimed: dict[Path, Path] = {}

        try:
            if self.backup_root is not None and not self.dry_run:
                self.backup_root.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(written, failed, planned, total, failures))
                    return

                source_path, relative_file = self._resolve_source_path(candidate)
                lyrics_text = str(candidate.get("lyrics_text") or "")
                label = f"{'Previewing' if self.dry_run else 'Writing'} {index}/{total}: {source_path.name}"
                self.progress.emit(index - 1, total, label)

                if not lyrics_text.strip():
                    failed += 1
                    failures.append(f"{relative_file}: no lyrics text")
                    self.progress.emit(index, total, label)
                    continue

                lrc_path = source_path.with_suffix(".lrc")
                existing = claimed.get(lrc_path)
                if existing is not None and existing != source_path:
                    failed += 1
                    failures.append(
                        f"{relative_file}: LRC name collision with {existing.name} -> {lrc_path.name}"
                    )
                    self.progress.emit(index, total, label)
                    continue
                claimed[lrc_path] = source_path

                planned += 1
                try:
                    write_lrc_sidecar(
                        source_path,
                        lyrics_text,
                        dry_run=self.dry_run,
                        backup_root=self.backup_root,
                        relative_file=relative_file,
                    )
                except Exception as exc:
                    failed += 1
                    planned -= 1
                    failures.append(f"{relative_file}: {exc}")
                    logger.debug("Failed to write LRC for %s", source_path, exc_info=True)
                    self.progress.emit(index, total, label)
                    continue

                written += 1
                self._processed_paths.append(
                    {
                        "filepath": str(source_path),
                        "relative_file": relative_file,
                        "lrc_path": str(lrc_path),
                    }
                )
                self.progress.emit(index, total, label)

            self.finished.emit(self._payload(written, failed, planned, total, failures))
        except Exception as exc:
            self.failed.emit(str(exc))
