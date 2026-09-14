"""Classify every file on a target so File Cleanup can group and delete by type.

Kept free of Qt so workers and the table model can share the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..constants import SYSTEM_FOLDERS


CATEGORY_ORDER = (
    "Audio",
    "Image",
    "Video",
    "Document",
    "Archive",
    "Playlist",
    "Subtitle",
    "Executable",
    "Hidden",
    "Other",
)

CATEGORY_INDEX = {category: index for index, category in enumerate(CATEGORY_ORDER)}

# Device-playable audio plus common lossless/lossy extras. .mp4 / .3gp stay
# Video — they are also used as audio on the Echo Mini, but the old cleanup
# tab treated them as video containers and users expect that grouping.
AUDIO_FILE_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".ape",
    ".dff",
    ".dsf",
    ".fla",
    ".flac",
    ".m4a",
    ".mp1",
    ".mp2",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}

IMAGE_FILE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

VIDEO_FILE_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ts",
    ".webm",
    ".wmv",
}

DOCUMENT_FILE_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".md",
    ".ods",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
}

ARCHIVE_FILE_EXTENSIONS = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}

PLAYLIST_FILE_EXTENSIONS = {
    ".asx",
    ".cue",
    ".m3u",
    ".m3u8",
    ".pls",
    ".xspf",
}

SUBTITLE_FILE_EXTENSIONS = {
    ".ass",
    ".lrc",
    ".srt",
    ".ssa",
    ".sub",
    ".vtt",
}

EXECUTABLE_FILE_EXTENSIONS = {
    ".app",
    ".bat",
    ".bin",
    ".command",
    ".exe",
    ".msi",
    ".pkg",
    ".run",
    ".sh",
}

FILE_EXTENSION_DESCRIPTIONS: dict[str, str] = {
    ".aac": "AAC Audio",
    ".aif": "AIFF Audio",
    ".aiff": "AIFF Audio",
    ".alac": "Apple Lossless Audio",
    ".ape": "Monkey's Audio (Lossless)",
    ".dsf": "DSD Stream File",
    ".dff": "DSD Interchange File",
    ".fla": "FLAC Lossless Audio",
    ".flac": "FLAC Lossless Audio",
    ".m4a": "AAC/ALAC Audio (iTunes)",
    ".m4b": "AAC Audiobook",
    ".m4p": "Protected AAC Audio",
    ".m4r": "iPhone Ringtone",
    ".mid": "MIDI Sequence",
    ".midi": "MIDI Sequence",
    ".mp1": "MPEG-1 Audio",
    ".mp2": "MPEG-2 Audio",
    ".mp3": "MP3 Audio",
    ".mpc": "Musepack Audio",
    ".oga": "Ogg Audio",
    ".ogg": "Ogg Vorbis Audio",
    ".opus": "Opus Audio",
    ".ra": "RealAudio",
    ".tak": "TAK Lossless Audio",
    ".tta": "True Audio (Lossless)",
    ".wav": "WAV Uncompressed Audio",
    ".wma": "Windows Media Audio",
    ".wv": "WavPack Audio",
    ".bmp": "Bitmap Image",
    ".gif": "GIF Image",
    ".heic": "HEIC Image (Apple)",
    ".heif": "HEIF Image",
    ".ico": "Icon File",
    ".jpeg": "JPEG Image",
    ".jpg": "JPEG Image",
    ".png": "PNG Image",
    ".svg": "SVG Vector Image",
    ".tif": "TIFF Image",
    ".tiff": "TIFF Image",
    ".webp": "WebP Image",
    ".3gp": "3GP Mobile Video",
    ".avi": "AVI Video",
    ".flv": "Flash Video",
    ".m2ts": "Blu-ray Transport Stream",
    ".m4v": "MPEG-4 Video (iTunes)",
    ".mkv": "Matroska Video",
    ".mov": "QuickTime Video",
    ".mp4": "MP4 Video",
    ".mpeg": "MPEG Video",
    ".mpg": "MPEG Video",
    ".mts": "AVCHD Video",
    ".ts": "Transport Stream",
    ".webm": "WebM Video",
    ".wmv": "Windows Media Video",
    ".csv": "Comma-Separated Values",
    ".doc": "Word Document (Legacy)",
    ".docx": "Word Document",
    ".epub": "EPUB eBook",
    ".md": "Markdown Document",
    ".ods": "OpenDocument Spreadsheet",
    ".odt": "OpenDocument Text",
    ".pdf": "PDF Document",
    ".ppt": "PowerPoint (Legacy)",
    ".pptx": "PowerPoint Presentation",
    ".rtf": "Rich Text Format",
    ".txt": "Plain Text File",
    ".xls": "Excel Spreadsheet (Legacy)",
    ".xlsx": "Excel Spreadsheet",
    ".7z": "7-Zip Archive",
    ".bz2": "Bzip2 Compressed",
    ".gz": "Gzip Compressed",
    ".rar": "RAR Archive",
    ".tar": "Tar Archive",
    ".tgz": "Gzip Tar Archive",
    ".xz": "XZ Compressed",
    ".zip": "ZIP Archive",
    ".asx": "ASX Playlist",
    ".cue": "Cue Sheet",
    ".m3u": "M3U Playlist",
    ".m3u8": "M3U8 Playlist (UTF-8)",
    ".pls": "PLS Playlist",
    ".xspf": "XSPF Playlist",
    ".ass": "Advanced SubStation Subtitles",
    ".lrc": "Synced Lyrics File",
    ".srt": "SubRip Subtitles",
    ".ssa": "SubStation Subtitles",
    ".sub": "MicroDVD Subtitles",
    ".vtt": "WebVTT Subtitles",
    ".app": "macOS Application",
    ".bat": "Windows Batch Script",
    ".bin": "Binary Executable",
    ".command": "macOS Shell Script",
    ".exe": "Windows Executable",
    ".msi": "Windows Installer",
    ".pkg": "macOS Installer Package",
    ".run": "Linux Installer",
    ".sh": "Shell Script",
    ".ds_store": "macOS Folder Metadata",
    ".ini": "Configuration File",
    ".log": "Log File",
    ".db": "Database File",
    ".tmp": "Temporary File",
    ".bak": "Backup File",
    ".nfo": "Info/NFO Text File",
    ".url": "Internet Shortcut",
    ".lnk": "Windows Shortcut",
    ".plist": "macOS Property List",
}

KNOWN_HIDDEN_NAMES = {
    ".ds_store": ".DS_Store",
    "thumbs.db": "Thumbs.db",
    "desktop.ini": "desktop.ini",
}

KNOWN_HIDDEN_DESCRIPTIONS = {
    ".ds_store": "macOS Folder Metadata",
    "thumbs.db": "Windows Thumbnail Cache",
    "desktop.ini": "Windows Folder Settings",
}

HIDDEN_TOOLTIP = (
    "Hidden files are not shown in Finder by default. "
    "Press Cmd+Shift+. to toggle hidden files."
)
MACOS_SIDECAR_TOOLTIP = (
    "These are macOS sidecar metadata files (._*). "
    "Finder usually hides them. Press Cmd+Shift+. to show hidden files."
)


@dataclass
class CleanupTypeStats:
    key: str
    file_type: str
    category: str
    extension: str
    description: str
    count: int = 0
    size_bytes: int = 0
    is_checked: bool = False
    files: list[str] = field(default_factory=list)


def format_bytes(size: int) -> str:
    if size < 1024 ** 2:
        return f"{size / 1024:.0f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB"
    return f"{size / (1024 ** 3):.1f} GB"


def is_system_folder(name: str) -> bool:
    return name.lower() in SYSTEM_FOLDERS


def category_for_file(file_name: str, extension: str) -> str:
    lowered = file_name.lower()
    if lowered.startswith("._") or lowered in KNOWN_HIDDEN_NAMES:
        return "Hidden"
    if extension in AUDIO_FILE_EXTENSIONS:
        return "Audio"
    if extension in IMAGE_FILE_EXTENSIONS:
        return "Image"
    if extension in VIDEO_FILE_EXTENSIONS:
        return "Video"
    if extension in DOCUMENT_FILE_EXTENSIONS:
        return "Document"
    if extension in ARCHIVE_FILE_EXTENSIONS:
        return "Archive"
    if extension in PLAYLIST_FILE_EXTENSIONS:
        return "Playlist"
    if extension in SUBTITLE_FILE_EXTENSIONS:
        return "Subtitle"
    if extension in EXECUTABLE_FILE_EXTENSIONS:
        return "Executable"
    if file_name.startswith("."):
        return "Hidden"
    return "Other"


def file_type_label(file_name: str, extension: str, category: str) -> str:
    if category == "Hidden":
        lowered = file_name.lower()
        if lowered.startswith("._") and extension:
            return f"._*{extension} (macOS sidecar)"
        known_label = KNOWN_HIDDEN_NAMES.get(lowered)
        if known_label:
            return known_label
        if extension:
            return f".*{extension}"
        return file_name
    if extension:
        return extension
    return "(no extension)"


def description_for_file(file_name: str, extension: str, category: str) -> str:
    lowered = file_name.lower()
    if category == "Hidden":
        if lowered.startswith("._"):
            return "macOS sidecar metadata"
        known = KNOWN_HIDDEN_DESCRIPTIONS.get(lowered)
        if known:
            return known
        known = KNOWN_HIDDEN_DESCRIPTIONS.get(extension)
        if known:
            return known
    description = FILE_EXTENSION_DESCRIPTIONS.get(extension, "")
    if description:
        return description
    if extension:
        return f"{extension.lstrip('.').upper()} File"
    return "Unknown"


def row_key(category: str, file_type: str) -> str:
    return f"{category}|{file_type.lower()}"


def classify_file(path: Path) -> tuple[str, str, str, str, str]:
    """Return (key, file_type, category, extension, description)."""
    file_name = path.name
    extension = path.suffix.lower()
    category = category_for_file(file_name, extension)
    label = file_type_label(file_name, extension, category)
    return (
        row_key(category, label),
        label,
        category,
        extension,
        description_for_file(file_name, extension, category),
    )


def tooltip_for_row(category: str, file_type: str) -> str:
    if category != "Hidden":
        return ""
    if "macos sidecar" in file_type.lower():
        return MACOS_SIDECAR_TOOLTIP
    return HIDDEN_TOOLTIP


def sort_cleanup_rows(rows: list[CleanupTypeStats]) -> list[CleanupTypeStats]:
    return sorted(
        rows,
        key=lambda row: (
            CATEGORY_INDEX.get(row.category, 999),
            row.file_type.lower(),
        ),
    )


def path_is_within_target(candidate: Path, target: Path) -> bool:
    try:
        candidate.resolve().relative_to(target.resolve())
        return True
    except (ValueError, OSError):
        return False
