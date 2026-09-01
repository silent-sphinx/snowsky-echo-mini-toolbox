import os
import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, SYLT, USLT, TXXX
import multiprocessing
import concurrent.futures
from PySide6.QtCore import QThread, Signal
from typing import Optional

from ..constants import SUPPORTED_MEDIA_EXTENSIONS
from ..models.drive_data import DriveDataModel, TrackMetadata


def extract_metadata_worker(filepath: str, root_path: str) -> TrackMetadata:
    """Extract metadata from a single file. (Runs in separate processes)"""
    filename = os.path.basename(filepath)
    _, ext = os.path.splitext(filename)
    size = os.path.getsize(filepath)
    
    meta = TrackMetadata(
        filepath=filepath,
        filename=filename,
        extension=ext.lower(),
        size_bytes=size
    )
    
    if filepath.lower().endswith(".lrc"):
        meta.format_name = "LRC Lyrics File"
        return meta
        
    try:
        audio = mutagen.File(filepath, easy=False)
        if audio is None:
            return meta
    except:
        return meta
        
    meta.format_name = type(audio).__name__
    if audio.info:
        meta.duration_seconds = getattr(audio.info, "length", 0.0)
        meta.bitrate_kbps = getattr(audio.info, "bitrate", 0) // 1000 if getattr(audio.info, "bitrate", 0) else 0
        meta.sample_rate_hz = getattr(audio.info, "sample_rate", 0)
        meta.channels = getattr(audio.info, "channels", 0)
        
    # Extract tags
    if audio.tags:
        # Capture all raw tags for display (skip binary/lyrics)
        for key, val in audio.tags.items():
            k_lower = str(key).lower()
            if "apic" in k_lower or "pic" in k_lower or "covr" in k_lower or "lyrics" in k_lower or "sylt" in k_lower or "uslt" in k_lower:
                continue
            
            # Mutagen often returns lists for values. Unpack them if possible.
            if isinstance(val, list) and len(val) == 1:
                clean_val = str(val[0])
            elif isinstance(val, list):
                clean_val = ", ".join(str(v) for v in val)
            else:
                clean_val = str(val)
                
            meta.all_tags[str(key)] = clean_val
            
        # Common tags (mutagen makes this slightly painful depending on format)
        if isinstance(audio, FLAC):
            meta.title = audio.get("title", [meta.title])[0]
            meta.artist = audio.get("artist", [meta.artist])[0]
            meta.album = audio.get("album", [meta.album])[0]
            meta.genre = audio.get("genre", [""])[0]
            meta.year = audio.get("date", [""])[0]
            meta.track_num = audio.get("tracknumber", [""])[0]
            
            # Check for lyrics
            if "lyrics" in audio or "unsyncedlyrics" in audio:
                meta.has_lyrics = True
                meta.lyrics_text = audio.get("lyrics", audio.get("unsyncedlyrics", [None]))[0]
                
            # Check for pictures
            if audio.pictures:
                meta.has_album_art = True
                
        elif audio.tags:
            # Try generic dict access
            try:
                meta.title = str(audio.tags.get("TIT2", audio.get("title", [meta.title])[0]))
            except: pass
            
            try:
                meta.artist = str(audio.tags.get("TPE1", audio.get("artist", [meta.artist])[0]))
            except: pass
            
            try:
                meta.album = str(audio.tags.get("TALB", audio.get("album", [meta.album])[0]))
            except: pass
            
            # ID3 checks for art and lyrics
            if hasattr(audio.tags, "getall"):
                if audio.tags.getall("APIC"):
                    meta.has_album_art = True
                if audio.tags.getall("USLT") or audio.tags.getall("SYLT"):
                    meta.has_lyrics = True
                    uslts = audio.tags.getall("USLT")
                    if uslts:
                        meta.lyrics_text = uslts[0].text

    # Run ffprobe compatibility check
    if not filepath.lower().endswith(".lrc"):
        try:
            from pathlib import Path
            from ..utils.music_compatibility import evaluate_music_file
            
            comp_result = evaluate_music_file(Path(filepath), Path(root_path))
            meta.comp_status = comp_result.get("status", "UNKNOWN")
            meta.comp_category = comp_result.get("category", "unknown")
            meta.comp_reason = comp_result.get("reason", "")
            meta.comp_eq = comp_result.get("eq_compatibility", "-")
            meta.comp_codec = comp_result.get("codec", "-")
            meta.comp_sample_rate = comp_result.get("sample_rate", "-")
            meta.comp_bit_depth = comp_result.get("bit_depth", "-")
            meta.comp_block_size = comp_result.get("block_size", "-")
            meta.comp_dsd_profile = comp_result.get("dsd_profile", "-")
            meta.comp_channels = comp_result.get("channels", "-")
            meta.comp_streams = comp_result.get("stream_count", "-")
            meta.comp_filename = comp_result.get("filename_compatibility", "-")
            meta.comp_filename_reason = comp_result.get("filename_compatibility_reason", "-")
            meta.comp_metadata = comp_result.get("metadata_compatibility", "-")
            meta.comp_metadata_reason = comp_result.get("metadata_compatibility_reason", "-")
            meta.comp_channel_compat = comp_result.get("channel_compatibility", "-")
            meta.comp_channel_compat_reason = comp_result.get("channel_compatibility_reason", "-")
            meta.comp_wav_codec = comp_result.get("wav_codec_compatibility", "-")
            meta.comp_wav_codec_reason = comp_result.get("wav_codec_compatibility_reason", "-")
            meta.comp_dsd_bitdepth = comp_result.get("dsd_bitdepth_compatibility", "-")
            meta.comp_dsd_bitdepth_reason = comp_result.get("dsd_bitdepth_compatibility_reason", "-")
            meta.comp_tag_encoding = comp_result.get("tag_encoding_compatibility", "-")
            meta.comp_tag_encoding_reason = comp_result.get("tag_encoding_compatibility_reason", "-")
            meta.comp_tag_length = comp_result.get("tag_length_compatibility", "-")
            meta.comp_tag_length_reason = comp_result.get("tag_length_compatibility_reason", "-")
        except Exception as e:
            print(f"Compatibility scan failed for {filepath}: {e}")

        # Album art validation (also authoritative for has_album_art, since it
        # covers MP4 covr atoms that the tag pass above does not read).
        try:
            from pathlib import Path
            from ..utils.album_art_planner import apply_album_art_result
            from ..utils.album_art_validation import evaluate_album_art

            apply_album_art_result(meta, evaluate_album_art(Path(filepath)))
        except Exception as e:
            print(f"Album art scan failed for {filepath}: {e}")

    return meta


class DriveScannerThread(QThread):
    """
    Background thread that scans a drive for all supported media files and
    extracts deep metadata via mutagen.
    
    Signals:
        progress_updated (int, int, str): current_track, total_tracks, current_file
        scan_finished (DriveDataModel): Emitted when scan is complete.
    """
    progress_updated = Signal(int, int, str)
    scan_finished = Signal(object)  # Passes the DriveDataModel

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        data_model = DriveDataModel(self.path)
        
        # Phase 1: Fast walk to count files and collect paths
        supported_files = []
        for root, dirs, files in os.walk(self.path):
            if self._is_cancelled:
                return
                
            # Exclude hidden directories from being traversed
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                if file.startswith('.'):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in SUPPORTED_MEDIA_EXTENSIONS or ext == ".lrc":
                    supported_files.append(os.path.join(root, file))
                    
        total_files = len(supported_files)
        
        # Phase 2: Deep metadata extraction using multiprocessing
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_path = {executor.submit(extract_metadata_worker, path, self.path): path for path in supported_files}
            
            # Process as they complete
            for i, future in enumerate(concurrent.futures.as_completed(future_to_path)):
                if self._is_cancelled:
                    executor.shutdown(wait=False, cancel_futures=True)
                    return
                    
                filepath = future_to_path[future]
                self.progress_updated.emit(i + 1, total_files, filepath)
                
                try:
                    meta = future.result()
                    data_model.add_track(meta)
                except Exception as e:
                    # Fallback to basic metadata if mutagen fails
                    print(f"Error parsing {filepath}: {e}")
                    filename = os.path.basename(filepath)
                    _, ext = os.path.splitext(filename)
                    size = os.path.getsize(filepath)
                    meta = TrackMetadata(
                        filepath=filepath,
                        filename=filename,
                        extension=ext.lower(),
                        size_bytes=size
                    )
                    data_model.add_track(meta)

        self.scan_finished.emit(data_model)
