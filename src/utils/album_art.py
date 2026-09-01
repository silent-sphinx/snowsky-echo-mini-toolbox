import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC
from mutagen.mp4 import MP4, MP4Cover
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageWriter

from .album_art_validation import (
    MAX_ART_DIMENSION,
    NON_ARTWORK_EXTENSIONS,
    image_size_from_bytes,
    is_progressive_jpeg,
    jpeg_scan_type,
    read_embedded_album_art,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AlbumArtInfo",
    "MAX_ART_DIMENSION",
    "extract_album_art",
    "image_size_from_bytes",
    "is_progressive_jpeg",
    "jpeg_scan_type",
    "read_embedded_album_art",
    "supports_artwork_write",
    "to_non_progressive_jpeg",
    "write_embedded_album_art",
]


@dataclass
class AlbumArtInfo:
    image_data: bytes
    mime_type: str
    size_bytes: int
    width: int
    height: int
    is_progressive: bool


def extract_album_art(filepath: str) -> Optional[AlbumArtInfo]:
    """Dynamically extract album art data and dimensions from a media file."""
    try:
        audio = mutagen.File(filepath, easy=False)
        if audio is None:
            return None
    except Exception:
        return None
        
    image_data = None
    mime_type = "Unknown"
    
    # Check FLAC pictures
    if isinstance(audio, FLAC) and audio.pictures:
        pic = audio.pictures[0]
        image_data = pic.data
        mime_type = pic.mime
    # Check ID3 APIC
    elif audio.tags and hasattr(audio.tags, "getall"):
        apics = audio.tags.getall("APIC")
        if apics:
            pic = apics[0]
            image_data = pic.data
            mime_type = pic.mime
            
    if not image_data:
        return None
        
    # Determine dimensions via PySide6 QImage (doesn't require full GUI window to just parse headers/data)
    qimg = QImage.fromData(image_data)
    width = qimg.width()
    height = qimg.height()
    
    # Default to generic byte analysis for format if mime is weird or missing
    is_prog = False
    if "jpeg" in mime_type.lower() or "jpg" in mime_type.lower() or image_data.startswith(b'\xff\xd8'):
        is_prog = is_progressive_jpeg(image_data)
        if mime_type == "Unknown" or not mime_type:
            mime_type = "image/jpeg"
    elif image_data.startswith(b'\x89PNG'):
        if mime_type == "Unknown" or not mime_type:
            mime_type = "image/png"
            
    return AlbumArtInfo(
        image_data=image_data,
        mime_type=mime_type,
        size_bytes=len(image_data),
        width=width,
        height=height,
        is_progressive=is_prog
    )


def to_non_progressive_jpeg(data: bytes, quality: int = 90) -> bytes | None:
    if not data:
        return None

    ba = data if isinstance(data, QByteArray) else QByteArray(data)
    image = QImage.fromData(ba)
    if image.isNull():
        return None

    if image.width() > MAX_ART_DIMENSION or image.height() > MAX_ART_DIMENSION:
        image = image.scaled(
            MAX_ART_DIMENSION,
            MAX_ART_DIMENSION,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.WriteOnly):
        return None

    writer = QImageWriter(buffer, b"jpeg")
    writer.setQuality(quality)
    if hasattr(writer, "setProgressiveScanWrite"):
        writer.setProgressiveScanWrite(False)

    if not writer.write(image):
        return None

    buffer.close()
    out_data = encoded.data()
    return out_data if isinstance(out_data, bytes) else bytes(out_data)


def supports_artwork_write(path: Path) -> bool:
    """Report whether embedded artwork can be written to this file."""
    if Path(path).suffix.lower() in NON_ARTWORK_EXTENSIONS:
        return False

    try:
        audio = mutagen.File(path)
    except Exception:
        return False

    if audio is None:
        return False

    if isinstance(audio, (FLAC, MP4)):
        return True

    tags = getattr(audio, "tags", None)
    if tags is not None and hasattr(tags, "add") and hasattr(tags, "delall"):
        return True

    # An empty ID3-capable container can still gain tags on write.
    return callable(getattr(audio, "add_tags", None))


def write_embedded_album_art(path: Path, jpeg_data: bytes) -> tuple[bool, str]:
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        return False, f"Tag read failed: {exc}"

    if not audio:
        return False, "Unsupported or unreadable audio file"

    dims = image_size_from_bytes(jpeg_data)
    width = dims[0] if dims else 0
    height = dims[1] if dims else 0

    if isinstance(audio, FLAC):
        pictures = getattr(audio, "pictures", [])
        source_picture = pictures[0] if pictures else None
        picture = Picture()
        picture.data = jpeg_data
        picture.mime = "image/jpeg"
        picture.type = int(getattr(source_picture, "type", 3) if source_picture is not None else 3)
        picture.desc = str(getattr(source_picture, "desc", "") if source_picture is not None else "")
        picture.width = width
        picture.height = height
        picture.depth = int((getattr(source_picture, "depth", 24) if source_picture is not None else 24) or 24)
        picture.colors = int((getattr(source_picture, "colors", 0) if source_picture is not None else 0) or 0)
        try:
            audio.clear_pictures()
            audio.add_picture(picture)
            audio.save()
            return True, "Added FLAC artwork" if not pictures else "Updated FLAC artwork"
        except Exception as exc:
            return False, f"Failed to write FLAC artwork: {exc}"

    tags = getattr(audio, "tags", None)
    if tags is None and not isinstance(audio, MP4):
        # Files that never had artwork often have no tag container yet.
        try:
            add_tags = getattr(audio, "add_tags", None)
            if callable(add_tags):
                add_tags()
            tags = getattr(audio, "tags", None)
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            logger.debug("Failed adding tags for %s: %s", path, exc)
            tags = None

    if tags is not None and hasattr(tags, "add") and hasattr(tags, "delall"):
        try:
            apic_frames = tags.getall("APIC") if hasattr(tags, "getall") else []
        except (AttributeError, TypeError, ValueError):
            apic_frames = []

        source_frame = apic_frames[0] if apic_frames else None
        replacement = APIC(
            encoding=1,
            mime="image/jpeg",
            type=int(getattr(source_frame, "type", 3) if source_frame is not None else 3),
            desc=str(getattr(source_frame, "desc", "") if source_frame is not None else ""),
            data=jpeg_data,
        )
        try:
            tags.delall("APIC")
            tags.add(replacement)
            save_kwargs = {"v2_version": 3} if path.suffix.lower() == ".mp3" else {}
            audio.save(**save_kwargs)
            return True, "Added ID3 artwork" if not apic_frames else "Updated ID3 artwork"
        except Exception as exc:
            return False, f"Failed to write ID3 artwork: {exc}"

    if isinstance(audio, MP4):
        try:
            if getattr(audio, "tags", None) is None:
                add_tags = getattr(audio, "add_tags", None)
                if callable(add_tags):
                    add_tags()
            covr = audio.tags.get("covr") if getattr(audio, "tags", None) else None
            audio.tags["covr"] = [MP4Cover(jpeg_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
            return True, "Added MP4 artwork" if not covr else "Updated MP4 artwork"
        except Exception as exc:
            return False, f"Failed to write MP4 artwork: {exc}"

    return False, "Unsupported format for artwork rewrite"
