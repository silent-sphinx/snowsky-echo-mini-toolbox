import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TrackMetadata:
    """Represents extracted metadata for a single media file."""
    filepath: str
    filename: str
    extension: str
    size_bytes: int
    
    # Audio properties
    format_name: str = "Unknown"
    duration_seconds: float = 0.0
    bitrate_kbps: int = 0
    sample_rate_hz: int = 0
    channels: int = 0
    
    # ID3 / Tags
    title: str = "Unknown Title"
    artist: str = "Unknown Artist"
    album: str = "Unknown Album"
    genre: str = ""
    year: str = ""
    track_num: str = ""
    
    # Embedded resources
    has_album_art: bool = False
    has_lyrics: bool = False
    lyrics_text: Optional[str] = None
    
    @property
    def display_size(self) -> str:
        if self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"
        
    @property
    def display_duration(self) -> str:
        if self.duration_seconds <= 0:
            return "0:00"
        m, s = divmod(int(self.duration_seconds), 60)
        return f"{m}:{s:02d}"


class DriveDataModel:
    """
    Central repository for all scanned drive data.
    Provides hierarchical access (for tree views) and fast flat access (for analysis).
    """
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.tracks: Dict[str, TrackMetadata] = {}  # filepath -> metadata
        self.total_size_bytes = 0
        
        # A simple tree structure representation:
        # { "FolderA": { "SubFolderB": { "song.mp3": None } } }
        self.tree: Dict = {}
        
    def add_track(self, metadata: TrackMetadata) -> None:
        self.tracks[metadata.filepath] = metadata
        self.total_size_bytes += metadata.size_bytes
        self._add_to_tree(metadata.filepath)
        
    def _add_to_tree(self, filepath: str) -> None:
        # Build tree representation relative to root
        rel_path = os.path.relpath(filepath, self.root_path)
        parts = rel_path.split(os.sep)
        
        curr = self.tree
        for part in parts[:-1]:
            if part not in curr:
                curr[part] = {}
            curr = curr[part]
        
        curr[parts[-1]] = None # Leaf node

    def get_track(self, filepath: str) -> Optional[TrackMetadata]:
        return self.tracks.get(filepath)
