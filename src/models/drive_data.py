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
    album_artist: str = ""
    genre: str = ""
    year: str = ""
    track_num: str = ""
    
    # Embedded resources
    has_album_art: bool = False
    has_lyrics: bool = False
    lyrics_text: Optional[str] = None
    
    # All raw tags extracted
    all_tags: Dict[str, str] = field(default_factory=dict)
    
    # Compatibility Metrics
    comp_status: str = "UNKNOWN"
    comp_category: str = "unknown"
    comp_reason: str = ""
    
    # UI State
    is_checked: bool = False
    comp_eq: str = "-"
    comp_codec: str = "-"
    comp_sample_rate: str = "-"
    comp_bit_depth: str = "-"
    comp_block_size: str = "-"
    comp_dsd_profile: str = "-"
    comp_channels: str = "-"
    comp_streams: str = "-"
    comp_filename: str = "-"
    comp_filename_reason: str = "-"
    comp_metadata: str = "-"
    comp_metadata_reason: str = "-"
    comp_channel_compat: str = "-"
    comp_channel_compat_reason: str = "-"
    comp_wav_codec: str = "-"
    comp_wav_codec_reason: str = "-"
    comp_dsd_bitdepth: str = "-"
    comp_dsd_bitdepth_reason: str = "-"
    comp_tag_encoding: str = "-"
    comp_tag_encoding_reason: str = "-"
    comp_tag_length: str = "-"
    comp_tag_length_reason: str = "-"

    # Album Art Metrics
    art_status: str = "UNKNOWN"
    art_reason: str = ""
    art_format: str = "-"
    art_scan_type: str = "-"
    art_resolution: str = "-"
    art_width: int = 0
    art_height: int = 0
    art_size: str = "-"
    art_source: str = "-"
    art_format_compat: str = "-"
    art_format_compat_reason: str = "-"
    art_scan_compat: str = "-"
    art_scan_compat_reason: str = "-"
    art_resolution_compat: str = "-"
    art_resolution_compat_reason: str = "-"
    art_metadata_status: str = "-"

    # Album Art UI State (separate from is_checked used by Music Compatibility)
    art_is_checked: bool = False

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
        
    def update_metadata(self, filepath: str, new_tags: dict) -> None:
        """Update the in-memory metadata for a specific track after it's saved."""
        from ..utils.metadata_writer import aliases_for_tag_key

        track = self.get_track(filepath)
        if not track:
            return

        for k, v in new_tags.items():
            if v is None:
                aliases = {alias.lower() for alias in aliases_for_tag_key(k)}
                for existing in [key for key in track.all_tags if key.lower() in aliases]:
                    del track.all_tags[existing]
            else:
                track.all_tags[k] = str(v)

        def _get_case_insensitive(tags_dict: dict, keys: list) -> str:
            for k in tags_dict:
                if k.lower() in keys:
                    return tags_dict[k]
            return ""

        track.title = _get_case_insensitive(track.all_tags, ["title", "tit2"]) or "Unknown Title"
        track.artist = _get_case_insensitive(track.all_tags, ["artist", "tpe1"]) or "Unknown Artist"
        track.album = _get_case_insensitive(track.all_tags, ["album", "talb"]) or "Unknown Album"
        track.album_artist = _get_case_insensitive(
            track.all_tags, ["albumartist", "album artist", "album_artist", "tpe2"]
        )
        track.genre = _get_case_insensitive(track.all_tags, ["genre", "tcon"])
        track.year = _get_case_insensitive(track.all_tags, ["year", "date", "tdrc"])
        track_num = _get_case_insensitive(track.all_tags, ["tracknumber", "track", "trck"])
        if track_num:
            track.track_num = track_num
