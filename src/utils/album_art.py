import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mutagen
from mutagen._vorbis import VComment
from mutagen.apev2 import APEBinaryValue, APEv2
from mutagen.asf import ASFByteArrayAttribute, ASFTags
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC
from mutagen.mp4 import MP4, MP4Cover
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageWriter

from .album_art_validation import (
    APE_COVER_KEY,
    ASF_PICTURE_KEY,
    MAX_ART_DIMENSION,
    NON_ARTWORK_EXTENSIONS,
    VORBIS_LEGACY_KEYS,
    VORBIS_PICTURE_KEY,
    build_asf_picture,
    image_size_from_bytes,
    is_progressive_jpeg,
    jpeg_scan_type,
    read_embedded_album_art,
    sniff_image_mime,
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
    "remove_embedded_album_art",
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
    """Extract album art data and dimensions from a media file."""
    image_data, mime_type = read_embedded_album_art(Path(filepath))
    if not image_data:
        return None

    if not mime_type or mime_type == "image/unknown":
        mime_type = sniff_image_mime(image_data)

    qimg = QImage.fromData(image_data)
    return AlbumArtInfo(
        image_data=image_data,
        mime_type=mime_type,
        size_bytes=len(image_data),
        width=qimg.width(),
        height=qimg.height(),
        is_progressive=is_progressive_jpeg(image_data),
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
    if isinstance(tags, (VComment, APEv2, ASFTags)):
        return True
    if tags is not None:
        return False

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

    tags = getattr(audio, "tags", None)
    try:
        if isinstance(tags, VComment):
            picture = Picture()
            picture.data = jpeg_data
            picture.mime = "image/jpeg"
            picture.type = 3
            picture.width = width
            picture.height = height
            picture.depth = 24
            for key in VORBIS_LEGACY_KEYS:
                if key in tags:
                    del tags[key]
            tags[VORBIS_PICTURE_KEY] = [base64.b64encode(picture.write()).decode("ascii")]
            audio.save()
            return True, "Updated Vorbis artwork"

        if isinstance(tags, APEv2):
            tags[APE_COVER_KEY] = APEBinaryValue(b"cover.jpg\x00" + jpeg_data)
            audio.save()
            return True, "Updated APE artwork"

        if isinstance(tags, ASFTags):
            tags[ASF_PICTURE_KEY] = [ASFByteArrayAttribute(build_asf_picture(jpeg_data))]
            audio.save()
            return True, "Updated WMA artwork"
    except Exception as exc:
        return False, f"Failed to write artwork: {exc}"

    return False, "Unsupported format for artwork rewrite"


def remove_embedded_album_art(path: Path) -> tuple[bool, str]:
    """Strip every embedded picture from a file, whatever tag holds it."""
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        return False, f"Tag read failed: {exc}"

    if not audio:
        return False, "Unsupported or unreadable audio file"

    removed = 0
    if isinstance(audio, FLAC) and audio.pictures:
        removed += len(audio.pictures)
        audio.clear_pictures()

    tags = getattr(audio, "tags", None)
    if tags is not None:
        if hasattr(tags, "getall") and hasattr(tags, "delall"):
            removed += len(tags.getall("APIC"))
            tags.delall("APIC")
        if isinstance(audio, MP4) and "covr" in tags:
            removed += 1
            del tags["covr"]
        if isinstance(tags, VComment):
            for key in (VORBIS_PICTURE_KEY, *VORBIS_LEGACY_KEYS):
                if key in tags:
                    removed += 1
                    del tags[key]
        if isinstance(tags, APEv2):
            for key in [k for k in tags.keys() if k.lower().startswith("cover art")]:
                removed += 1
                del tags[key]
        if isinstance(tags, ASFTags) and ASF_PICTURE_KEY in tags:
            removed += 1
            del tags[ASF_PICTURE_KEY]

    if not removed:
        return False, "No embedded artwork found"

    try:
        save_kwargs = {"v2_version": 3} if path.suffix.lower() == ".mp3" else {}
        audio.save(**save_kwargs)
    except Exception as exc:
        return False, f"Failed to save file: {exc}"
    return True, "Removed embedded artwork"
