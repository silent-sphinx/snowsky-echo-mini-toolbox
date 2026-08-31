import os
import mutagen
from mutagen.flac import FLAC
from dataclasses import dataclass
from typing import Optional
from PySide6.QtGui import QImage


@dataclass
class AlbumArtInfo:
    image_data: bytes
    mime_type: str
    size_bytes: int
    width: int
    height: int
    is_progressive: bool


def is_progressive_jpeg(data: bytes) -> bool:
    """Detect if a JPEG image uses a progressive scan."""
    if not data.startswith(b'\xff\xd8'):
        return False
        
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            # Reached entropy coded data or invalid marker, break
            break
        marker = data[i+1]
        
        if marker == 0xC0: # SOF0 (Baseline)
            return False
        if marker == 0xC2: # SOF2 (Progressive)
            return True
            
        # Markers with no payload
        if marker in (0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9):
            i += 2
            continue
            
        # Other markers have a 2-byte length directly following them (includes length itself)
        if i + 3 < len(data):
            length = (data[i+2] << 8) + data[i+3]
            i += 2 + length
        else:
            break
            
    return False


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
