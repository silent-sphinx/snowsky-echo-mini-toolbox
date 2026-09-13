"""Run saved workflows against the current target using existing v2 workers."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PySide6.QtCore import QMutex, QObject, QWaitCondition, Signal, Slot

from ..constants import SYSTEM_FOLDERS
from ..models.drive_data import DriveDataModel, TrackMetadata
from ..models.workflow import Workflow, WorkflowStep, action_label, normalize_tag_key, resolve_rename_preset_id
from ..threads.album_art_fix import AlbumArtFixWorker
from ..threads.backup_restore import ZipBackupWorker, destination_is_within_source
from ..threads.bulk_metadata import BulkMetadataWorker
from ..threads.file_rename import FileRenameWorker
from ..threads.lyrics_write import LyricsWriteWorker
from ..threads.music_conversion import MusicConversionWorker
from ..utils.album_art_planner import actionable_candidates as art_candidates
from ..utils.album_art_planner import plan_art_fixes_for_tracks
from ..utils.file_rename import apply_rename_evaluations, is_rename_track, rename_candidate_dict
from ..utils.lyrics import write_lrc_sidecar
from ..utils.lyrics_lookup import LookupCancelled, LyricsLookupClient, lookup_query_for_track
from ..utils.lyrics_planner import actionable_convert_candidates, plan_converts_for_tracks
from ..utils.music_conversion_planner import actionable_candidates as conversion_candidates
from ..utils.music_conversion_planner import plan_conversions_for_tracks

logger = logging.getLogger(__name__)

_SKIP_EXTENSIONS = {".lrc", ".cue"}
_HIDDEN_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_ART_QUALITY = 90
_CONVERSION_LEVEL = 8


class WorkflowExecutionWorker(QObject):
    progress = Signal(int, int, str)
    step_progress = Signal(int, int, str)
    request_lyrics_review = Signal(str, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        workflow: Workflow,
        target_path: Path,
        data_model: DriveDataModel | None,
    ):
        super().__init__()
        self.workflow = workflow
        self.target_path = target_path
        self.data_model = data_model
        self._cancel_requested = False
        self._active_worker = None
        self.review_mutex = QMutex()
        self.review_cond = QWaitCondition()
        self.review_result = False

    def request_cancel(self) -> None:
        self._cancel_requested = True
        worker = self._active_worker
        if worker is not None and hasattr(worker, "request_cancel"):
            worker.request_cancel()
        self.review_mutex.lock()
        self.review_cond.wakeAll()
        self.review_mutex.unlock()

    def submit_review_result(self, result: bool) -> None:
        self.review_mutex.lock()
        self.review_result = result
        self.review_cond.wakeAll()
        self.review_mutex.unlock()

    def _payload(self, success: bool, message: str) -> dict[str, object]:
        return {
            "success": success,
            "message": message,
            "needs_rescan": self.workflow.needs_rescan(),
            "workflow_name": self.workflow.name,
        }

    @Slot()
    def run(self) -> None:
        try:
            total_steps = len(self.workflow.steps)
            if total_steps == 0:
                self.failed.emit("This workflow has no steps.")
                return

            for index, step in enumerate(self.workflow.steps, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(False, "Workflow cancelled."))
                    return

                label = action_label(step.type)
                self.progress.emit(index, total_steps, f"Step {index} of {total_steps}: {label}")
                ok, message = self._run_step(step)
                if self._cancel_requested:
                    self.cancelled.emit(self._payload(False, "Workflow cancelled."))
                    return
                if not ok:
                    self.failed.emit(f"Failed at step {index} ({label}): {message}")
                    return

            self.finished.emit(self._payload(True, "Workflow completed successfully."))
        except Exception as exc:
            logger.exception("Workflow execution failed")
            self.failed.emit(f"Workflow error: {exc}")

    def _run_step(self, step: WorkflowStep) -> tuple[bool, str]:
        handlers = {
            "backup": self._step_backup,
            "file_cleanup": self._step_file_cleanup,
            "file_rename": self._step_file_rename,
            "metadata_manager": self._step_metadata,
            "lyrics_manager": self._step_lyrics,
            "fix_album_art": self._step_fix_album_art,
            "make_music_compatible": lambda _step: self._step_convert(eq_compatible=False),
            "make_music_eq_compatible": lambda _step: self._step_convert(eq_compatible=True),
        }
        handler = handlers.get(step.type)
        if handler is None:
            return False, f"Unknown step type: {step.type}"
        return handler(step)

    def _library_tracks(self) -> tuple[list[TrackMetadata] | None, str]:
        if self.data_model is None:
            return None, "Wait for the library scan to finish before running this step."
        tracks = [
            track
            for track in self.data_model.tracks.values()
            if track.extension.lower() not in _SKIP_EXTENSIONS
        ]
        return tracks, ""

    def _run_nested_worker(self, worker) -> tuple[bool, str]:
        box = {"failed": "", "cancelled": False}

        def on_failed(message: str) -> None:
            box["failed"] = message

        def on_cancelled(_payload) -> None:
            box["cancelled"] = True

        worker.progress.connect(self.step_progress)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(on_cancelled)
        self._active_worker = worker
        try:
            worker.run()
        finally:
            self._active_worker = None

        if self._cancel_requested or box["cancelled"]:
            return False, "Cancelled"
        if box["failed"]:
            return False, box["failed"]
        return True, ""

    def _step_backup(self, step: WorkflowStep) -> tuple[bool, str]:
        backup_path_str = str(step.config.get("backup_path") or "").strip()
        if not backup_path_str:
            return False, "Backup folder not configured"

        backup_dir = Path(backup_path_str).expanduser()
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return False, f"Failed to create backup folder: {exc}"

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_path = backup_dir / f"backup_{self.target_path.name}_{timestamp}.zip"
        if destination_is_within_source(self.target_path, zip_path):
            return False, "Backup zip must be saved outside the source folder"

        worker = ZipBackupWorker(self.target_path, zip_path)
        return self._run_nested_worker(worker)

    def _step_file_cleanup(self, step: WorkflowStep) -> tuple[bool, str]:
        clean_hidden = bool(step.config.get("clean_hidden", True))
        clean_forks = bool(step.config.get("clean_forks", True))
        clean_empty = bool(step.config.get("clean_empty", True))
        to_delete: list[Path] = []

        for root, dir_names, file_names in os.walk(str(self.target_path), topdown=False):
            if self._cancel_requested:
                return False, "Cancelled"
            dir_names[:] = [name for name in dir_names if name.lower() not in SYSTEM_FOLDERS]
            root_path = Path(root)
            for file_name in file_names:
                file_path = root_path / file_name
                lower_name = file_name.lower()
                is_hidden = clean_hidden and lower_name in _HIDDEN_NAMES
                is_fork = clean_forks and file_name.startswith("._")
                if is_hidden or is_fork:
                    to_delete.append(file_path)

        total = len(to_delete)
        for index, path in enumerate(to_delete, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
            self.step_progress.emit(index, max(total, 1), f"Deleting {path.name}")
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

        if clean_empty:
            for root, dir_names, file_names in os.walk(str(self.target_path), topdown=False):
                if self._cancel_requested:
                    return False, "Cancelled"
                dir_names[:] = [name for name in dir_names if name.lower() not in SYSTEM_FOLDERS]
                dir_path = Path(root)
                if dir_path == self.target_path:
                    continue
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                except Exception:
                    pass

        return True, ""

    def _step_file_rename(self, _step: WorkflowStep) -> tuple[bool, str]:
        tracks, error = self._library_tracks()
        if tracks is None:
            return False, error

        preset_id = resolve_rename_preset_id(str(_step.config.get("preset") or ""))
        apply_rename_evaluations(tracks, preset_id=preset_id, reset_checks=True)
        candidates = [
            rename_candidate_dict(track, str(self.target_path))
            for track in tracks
            if is_rename_track(track) and track.rename_status == "RENAME" and track.rename_suggested
        ]
        if not candidates:
            return True, "No files needed renaming"

        worker = FileRenameWorker(self.target_path, candidates)
        return self._run_nested_worker(worker)

    def _step_metadata(self, step: WorkflowStep) -> tuple[bool, str]:
        tracks, error = self._library_tracks()
        if tracks is None:
            return False, error

        tag_name = normalize_tag_key(str(step.config.get("tag_name") or ""))
        if not tag_name:
            return False, "No tag configured"

        action = str(step.config.get("action") or "Set").strip().lower()
        tag_value = str(step.config.get("tag_value") or "")
        tags = {tag_name: None if action == "remove" else tag_value}
        worker = BulkMetadataWorker(tracks, tags)
        return self._run_nested_worker(worker)

    def _step_lyrics(self, step: WorkflowStep) -> tuple[bool, str]:
        tracks, error = self._library_tracks()
        if tracks is None:
            return False, error

        convert_tracks = [
            track for track in tracks if (track.lyrics_status or "").upper() == "INCOMPATIBLE"
        ]
        convert_plans = plan_converts_for_tracks(convert_tracks, str(self.target_path))
        convert_candidates = actionable_convert_candidates(convert_plans)
        if convert_candidates:
            worker = LyricsWriteWorker(self.target_path, convert_candidates, False, None)
            ok, message = self._run_nested_worker(worker)
            if not ok:
                return False, message

        missing = [track for track in tracks if (track.lyrics_status or "").upper() == "MISSING"]
        if not missing:
            return True, ""

        auto_apply = bool(step.config.get("auto_apply"))
        client = LyricsLookupClient()
        client.set_cancel_check(lambda: self._cancel_requested)
        write_candidates: list[dict[str, object]] = []
        total = len(missing)

        try:
            for index, track in enumerate(missing, start=1):
                if self._cancel_requested:
                    return False, "Cancelled"

                query = lookup_query_for_track(track)
                self.step_progress.emit(index, total, f"Lyrics for {track.filename}")
                if query.error:
                    continue

                status, _source, lyrics_text = client.lookup(
                    query.title, query.artist, query.album, query.duration
                )
                if status != "Found" or not lyrics_text.strip():
                    continue

                apply_it = auto_apply
                if not auto_apply:
                    self.review_mutex.lock()
                    self.request_lyrics_review.emit(track.filename, lyrics_text)
                    self.review_cond.wait(self.review_mutex)
                    apply_it = self.review_result
                    self.review_mutex.unlock()
                    if self._cancel_requested:
                        return False, "Cancelled"

                if apply_it:
                    try:
                        relative = os.path.relpath(track.filepath, str(self.target_path))
                    except Exception:
                        relative = track.filepath
                    write_candidates.append(
                        {
                            "filepath": track.filepath,
                            "relative_file": relative,
                            "lyrics_text": lyrics_text,
                        }
                    )
        except LookupCancelled:
            return False, "Cancelled"

        if not write_candidates:
            return True, ""

        # Write immediately for reviewed tracks so a later cancel still keeps accepted matches.
        for candidate in write_candidates:
            if self._cancel_requested:
                return False, "Cancelled"
            try:
                write_lrc_sidecar(
                    Path(str(candidate["filepath"])),
                    str(candidate["lyrics_text"]),
                    relative_file=str(candidate["relative_file"]),
                )
            except Exception:
                pass
        return True, ""

    def _step_fix_album_art(self, _step: WorkflowStep) -> tuple[bool, str]:
        tracks, error = self._library_tracks()
        if tracks is None:
            return False, error

        plans = plan_art_fixes_for_tracks(tracks, str(self.target_path))
        candidates = art_candidates(plans)
        if not candidates:
            return True, "No artwork needed fixing"

        worker = AlbumArtFixWorker(self.target_path, candidates, _ART_QUALITY, False, None)
        return self._run_nested_worker(worker)

    def _step_convert(self, *, eq_compatible: bool) -> tuple[bool, str]:
        tracks, error = self._library_tracks()
        if tracks is None:
            return False, error

        plans = plan_conversions_for_tracks(
            tracks,
            str(self.target_path),
            make_eq_compatible=eq_compatible,
        )
        candidates = conversion_candidates(plans)
        if not candidates:
            return True, "No files needed conversion"

        worker = MusicConversionWorker(
            target_path=self.target_path,
            candidates=candidates,
            make_eq_compatible=eq_compatible,
            compression_level=_CONVERSION_LEVEL,
            dry_run=False,
            backup_root=None,
            preserve_tags=False,
        )
        return self._run_nested_worker(worker)
