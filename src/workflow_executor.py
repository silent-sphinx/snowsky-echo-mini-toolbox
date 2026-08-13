
import os
import time
import zipfile
import mutagen
from PySide6.QtCore import QWaitCondition, QMutex
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot, QThread
from PySide6.QtWidgets import QProgressDialog, QApplication
from PySide6.QtCore import Qt

from .workflows import Workflow, WorkflowStep
from .ui_utils import create_progress_dialog
from .album_art import (
    iter_audio_files,
    read_embedded_album_art,
    image_size_from_bytes,
    jpeg_scan_type,
    to_non_progressive_jpeg,
    write_embedded_album_art
)
from .music_compatibility import _evaluate_music_file_with_cache

# We will need the main window to run _fix_album_art_core, or we can import it.
# Actually, since _fix_album_art_core is a method of ToolboxWindow, we can pass a reference to it.
# Or better, we can just instantiate the workers if they are self-contained.


class LyricsReviewDialog(QDialog):
    def __init__(self, title: str, lyrics_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Review Lyrics - {title}")
        self.resize(600, 700)
        
        layout = QVBoxLayout(self)
        
        self.text_browser = QTextBrowser()
        self.text_browser.setPlainText(lyrics_text)
        layout.addWidget(self.text_browser)
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.button(QDialogButtonBox.Ok).setText("Apply")
        button_box.button(QDialogButtonBox.Cancel).setText("Skip")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

class WorkflowExecutionWorker(QObject):
    progress = Signal(int, int, str)
    step_progress = Signal(int, int, str)
    finished = Signal(bool, str)
    request_lyrics_review = Signal(str, str)
    
    def __init__(self, workflow: Workflow, target_path: Path, main_window):
        super().__init__()
        self.workflow = workflow
        self.target_path = target_path
        self.main_window = main_window
        self._cancel_requested = False
        
        self.review_mutex = QMutex()
        self.review_cond = QWaitCondition()
        self.review_result = False
        
    def submit_review_result(self, result: bool):
        self.review_mutex.lock()
        self.review_result = result
        self.review_cond.wakeAll()
        self.review_mutex.unlock()
        
    def request_cancel(self):
        self._cancel_requested = True
        self.review_mutex.lock()
        self.review_cond.wakeAll()
        self.review_mutex.unlock()
        # If there's an active sub-worker, cancel it too.
        if hasattr(self, '_active_worker') and self._active_worker:
            self._active_worker.request_cancel()

    @Slot()
    def run(self):
        try:
            total_steps = len(self.workflow.steps)
            for i, step in enumerate(self.workflow.steps, start=1):
                if self._cancel_requested:
                    self.finished.emit(False, "Workflow cancelled.")
                    return
                
                self.progress.emit(i, total_steps, f"Step {i}: {step.type}")
                
                success, msg = self._run_step(step)
                if not success:
                    self.finished.emit(False, f"Failed at step {i} ({step.type}): {msg}")
                    return
                    
            self.finished.emit(True, "Workflow completed successfully.")
        except Exception as e:
            self.finished.emit(False, f"Workflow error: {e}")

    def _run_step(self, step: WorkflowStep) -> tuple[bool, str]:
        if step.type == "fix_album_art":
            return self._step_fix_album_art(step)
        elif step.type == "make_music_compatible":
            return self._step_make_music_compatible(step, eq_compatible=False)
        elif step.type == "make_music_eq_compatible":
            return self._step_make_music_compatible(step, eq_compatible=True)
        elif step.type == "backup":
            return self._step_backup(step)
        elif step.type == "file_cleanup":
            return self._step_file_cleanup(step)
        elif step.type == "file_rename":
            return self._step_file_rename(step)
        elif step.type == "metadata_manager":
            return self._step_metadata_manager(step)
        elif step.type == "lyrics_manager":
            return self._step_lyrics_manager(step)
        else:
            return False, f"Unknown step type: {step.type}"

    def _step_fix_album_art(self, step: WorkflowStep) -> tuple[bool, str]:
        audio_files = list(iter_audio_files(self.target_path))
        total = len(audio_files)
        
        for index, file_path in enumerate(audio_files, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
            
            if index == total or index % 10 == 0:
                self.step_progress.emit(index, total, f"Checking {file_path.name}")
            
            try:
                art_bytes, art_mime = read_embedded_album_art(file_path)
                if not art_bytes:
                    continue
                    
                dims = image_size_from_bytes(art_bytes)
                resolution_ok = True
                if dims and (dims[0] > 1000 or dims[1] > 1000):
                    resolution_ok = False
                    
                if art_mime.lower() == "image/jpeg" and jpeg_scan_type(art_bytes) == "Non-progressive" and resolution_ok:
                    continue
                    
                converted_jpeg = to_non_progressive_jpeg(art_bytes)
                if converted_jpeg:
                    write_embedded_album_art(file_path, converted_jpeg)
            except Exception:
                pass
            
        return True, ""


    def _step_backup(self, step: WorkflowStep) -> tuple[bool, str]:
        backup_path_str = step.config.get("backup_path", "")
        if not backup_path_str:
            return False, "Backup path not configured"
            
        backup_dir = Path(backup_path_str)
        if not backup_dir.exists():
            try:
                backup_dir.mkdir(parents=True)
            except Exception as e:
                return False, f"Failed to create backup directory: {e}"
                
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_name = f"backup_{self.target_path.name}_{timestamp}.zip"
        zip_path = backup_dir / zip_name
        
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                if self.target_path.is_file():
                    self.step_progress.emit(1, 1, f"Zipping {self.target_path.name}")
                    zf.write(self.target_path, self.target_path.name)
                else:
                    total_files = sum([len(files) for r, d, files in os.walk(self.target_path)])
                    processed = 0
                    for root, dirs, files in os.walk(self.target_path):
                        for file in files:
                            if self._cancel_requested:
                                return False, "Cancelled"
                            file_path = Path(root) / file
                            arcname = file_path.relative_to(self.target_path)
                            
                            if processed == total_files or processed % 10 == 0:
                                self.step_progress.emit(processed, total_files, f"Zipping {file}")
                            
                            zf.write(file_path, arcname)
                            processed += 1
        except Exception as e:
            return False, str(e)
            
        return True, ""

    def _step_file_cleanup(self, step: WorkflowStep) -> tuple[bool, str]:
        clean_hidden = step.config.get("clean_hidden", True)
        clean_forks = step.config.get("clean_forks", True)
        clean_empty = step.config.get("clean_empty", True)
        
        hidden_names = {".ds_store", "thumbs.db", "desktop.ini"}
        to_delete = []
        
        if self.target_path.is_file():
            lower_name = self.target_path.name.lower()
            is_hidden = clean_hidden and (lower_name in hidden_names)
            is_fork = clean_forks and self.target_path.name.startswith("._")
            if is_hidden or is_fork:
                try:
                    self.target_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return True, ""
            
        for root, dirs, files in os.walk(self.target_path, topdown=False):
            if self._cancel_requested:
                return False, "Cancelled"
                
            for file in files:
                file_path = Path(root) / file
                lower_name = file.lower()
                
                is_hidden = clean_hidden and (lower_name in hidden_names)
                is_fork = clean_forks and file.startswith("._")
                
                if is_hidden or is_fork:
                    to_delete.append(file_path)
                    
        total = len(to_delete)
        for i, path in enumerate(to_delete, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
            
            if i == total or i % 10 == 0:
                self.step_progress.emit(i, total, f"Deleting {path.name}")
                
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
                
        if clean_empty:
            for root, dirs, files in os.walk(self.target_path, topdown=False):
                dir_path = Path(root)
                if dir_path == self.target_path:
                    continue
                if not any(dir_path.iterdir()):
                    try:
                        dir_path.rmdir()
                    except Exception:
                        pass

        return True, ""

    def _step_file_rename(self, step: WorkflowStep) -> tuple[bool, str]:
        preset = step.config.get("preset", "Track No. Title")
        
        from .album_art import iter_audio_files
        from .window import FileRenameScanWorker
        
        audio_files = list(iter_audio_files(self.target_path))
        total = len(audio_files)
        worker = FileRenameScanWorker(str(self.target_path))
        
        for i, file_path in enumerate(audio_files, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
                
            if i == total or i % 10 == 0:
                self.step_progress.emit(i, total, f"Renaming {file_path.name}")
                
            metadata = worker._read_metadata_safe(file_path)
            track_no = metadata.get("track_number")
            track_title = metadata.get("track_title")
            
            if not track_no or not track_title:
                continue
                
            safe_title = worker._safe_filename_component(track_title)
            if not safe_title:
                continue
                
            formatted_track_no = worker._format_track_number(track_no)
            suggested_name = f"{formatted_track_no}. {safe_title}{file_path.suffix}"
            
            if suggested_name != file_path.name:
                new_path = file_path.with_name(suggested_name)
                try:
                    file_path.rename(new_path)
                except Exception:
                    pass
                    
        return True, ""

    def _step_metadata_manager(self, step: WorkflowStep) -> tuple[bool, str]:
        tag_name = step.config.get("tag_name", "").strip()
        tag_value = step.config.get("tag_value", "")
        action = step.config.get("action", "Set")
        
        if not tag_name:
            return False, "No tag configured"
            
        from .album_art import iter_audio_files
        audio_files = list(iter_audio_files(self.target_path))
        total = len(audio_files)
        
        from .album_art import write_metadata_tag
        for i, file_path in enumerate(audio_files, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
                
            if i == total or i % 10 == 0:
                self.step_progress.emit(i, total, f"Updating {file_path.name}")
                
            write_metadata_tag(file_path, tag_name, tag_value, remove_only=(action == "Remove"))
                
        return True, ""

    def _step_lyrics_manager(self, step: WorkflowStep) -> tuple[bool, str]:
        auto_apply = step.config.get("auto_apply", False)
        
        from .album_art import iter_audio_files
        audio_files = list(iter_audio_files(self.target_path))
        total = len(audio_files)
        
        for i, file_path in enumerate(audio_files, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
                
            if i == total or i % 10 == 0:
                self.step_progress.emit(i, total, f"Lyrics for {file_path.name}")
                
            try:
                meta, err = self.main_window._lyrics_lookup_metadata_for_file(file_path)
                if not meta or err:
                    continue
                    
                ranked = self.main_window._lookup_lyrics_from_lrclib(
                    meta.get("title", ""), meta.get("artist", ""), meta.get("album", ""), meta.get("duration", 0)
                )
                
                top_result = None
                for record in (ranked or []):
                    if isinstance(record, dict):
                        top_result = record
                        break
                        
                if not top_result:
                    continue
                    
                lyrics_text = self.main_window._lyrics_text_from_lrclib_record(top_result)
                if not lyrics_text:
                    continue
                    
                if auto_apply:
                    self.main_window._save_embedded_lyrics(file_path, lyrics_text)
                else:
                    self.review_mutex.lock()
                    self.request_lyrics_review.emit(file_path.name, lyrics_text)
                    self.review_cond.wait(self.review_mutex)
                    apply_it = self.review_result
                    self.review_mutex.unlock()
                    
                    if apply_it:
                        self.main_window._save_embedded_lyrics(file_path, lyrics_text)
            except Exception:
                pass
                
        return True, ""

    def _step_make_music_compatible(self, step: WorkflowStep, eq_compatible: bool) -> tuple[bool, str]:
        # 1. Scan for candidates
        candidates = []
        audio_files = list(iter_audio_files(self.target_path))
        total = len(audio_files)
        
        for index, file_path in enumerate(audio_files, start=1):
            if self._cancel_requested:
                return False, "Cancelled"
            if index == total or index % 10 == 0:
                self.step_progress.emit(index, total, f"Scanning {file_path.name}")
            
            result = _evaluate_music_file_with_cache(file_path, self.target_path)
            
            should_convert = False
            status = result.get("status", "").strip().upper()
            eq_text = result.get("eq_compatibility", "").strip().lower()
            
            if status == "UNSUPPORTED":
                should_convert = True
                
            if eq_compatible and eq_text in {"not eq compatible", "unknown"}:
                should_convert = True
                
            if should_convert:
                candidates.append({
                    "relative_file": str(file_path.relative_to(self.target_path)),
                    "sample_rate": str(result.get("sample_rate", "")),
                    "bit_depth": str(result.get("bit_depth", "")),
                })
        
        if not candidates:
            return True, "No files needed conversion"
            
        # 2. Convert candidates
        from .window import MusicConversionWorker
        self._active_worker = MusicConversionWorker(
            target_path=self.target_path,
            candidates=candidates,
            make_eq_compatible=eq_compatible,
            compression_level=8, # balanced
            dry_run=False,
            backup_root=None
        )
        
        # Connect signals to proxy
        def on_conv_progress(idx, tot, msg):
            self.step_progress.emit(idx, tot, msg)
            
        self._active_worker.progress.connect(on_conv_progress)
        
        # Run synchronously
        self._active_worker.run()
        
        self._active_worker = None
        
        if self._cancel_requested:
            return False, "Cancelled"
            
        return True, ""

class WorkflowExecutor:
    def __init__(self, main_window):
        self.main_window = main_window
        self.thread = None
        self.worker = None
        self.progress_dialog = None
        
    def execute(self, workflow: Workflow, target_path: Path):
        self.progress_dialog = create_progress_dialog(
            "Executing Workflow",
            f"Starting {workflow.name}...",
            len(workflow.steps),
            self.main_window
        )
        self.progress_dialog.canceled.connect(self._on_cancel)
        
        self.thread = QThread()
        self.worker = WorkflowExecutionWorker(workflow, target_path, self.main_window)
        self.worker.moveToThread(self.thread)
        
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.step_progress.connect(self._on_step_progress)
        self.worker.request_lyrics_review.connect(self._on_lyrics_review_requested)
        self.worker.finished.connect(self._on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        
        self.thread.start()
        
    def _on_progress(self, current, total, msg):
        if self.progress_dialog:
            self.progress_dialog.setMaximum(total)
            self.progress_dialog.setValue(current - 1)
            self.progress_dialog.setLabelText(msg)
            
    def _on_step_progress(self, current, total, msg):
        # We can update the main window status bar with sub-progress
        self.main_window.statusBar().showMessage(f"{msg} ({current}/{total})")
            
    def _on_cancel(self):
        if self.worker:
            self.worker.request_cancel()
            
    def _on_lyrics_review_requested(self, title: str, lyrics_text: str):
        dialog = LyricsReviewDialog(title, lyrics_text, self.main_window)
        result = dialog.exec() == QDialog.Accepted
        if self.worker:
            self.worker.submit_review_result(result)
            
    def _on_finished(self, success, msg):
        if self.progress_dialog:
            self.progress_dialog.close()
        
        if success:
            self.main_window.statusBar().showMessage(f"Workflow finished: {msg}", 5000)
        else:
            self.main_window.statusBar().showMessage(f"Workflow stopped: {msg}", 5000)
