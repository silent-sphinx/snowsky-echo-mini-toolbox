import os
import logging
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal, Slot
from PySide6.QtGui import QImage, QImageWriter

from .constants import AUDIO_FILE_EXTENSIONS

try:
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    mutagen = None
    FLAC = None
    Picture = None
    APIC = None
    MP4 = None
    MP4Cover = None


logger = logging.getLogger(__name__)


def image_size_from_bytes(data: bytes) -> tuple[int, int] | None:
    if not data or len(data) < 10:
        return None

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")

    if data.startswith(b"BM") and len(data) >= 26:
        return int.from_bytes(data[18:22], "little"), int.from_bytes(data[22:26], "little")

    # Parse JPEG SOF markers for dimensions.
    if data.startswith(b"\xFF\xD8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue

            marker = data[index + 1]
            index += 2

            while marker == 0xFF and index < len(data):
                marker = data[index]
                index += 1

            if marker in (0xD8, 0xD9):
                continue

            if index + 2 > len(data):
                break
            segment_len = int.from_bytes(data[index : index + 2], "big")
            if segment_len < 2:
                break

            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if index + 7 > len(data):
                    break
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                return width, height

            index += segment_len

    return None


def jpeg_scan_type(data: bytes) -> str:
    if not data.startswith(b"\xFF\xD8"):
        return "Not JPEG"

    progressive_markers = {0xC2, 0xC6, 0xCA, 0xCE}
    non_progressive_markers = {
        0xC0,
        0xC1,
        0xC3,
        0xC5,
        0xC7,
        0xC9,
        0xCB,
        0xCD,
        0xCF,
    }

    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue

        marker = data[index + 1]
        index += 2

        while marker == 0xFF and index < len(data):
            marker = data[index]
            index += 1

        if marker in (0xD8, 0xD9):
            continue

        if index + 2 > len(data):
            break
        segment_len = int.from_bytes(data[index : index + 2], "big")
        if segment_len < 2:
            break

        if marker in progressive_markers:
            return "Progressive"
        if marker in non_progressive_markers:
            return "Non-progressive"

        index += segment_len

    return "Unknown"


def read_embedded_album_art(path: Path) -> tuple[bytes | None, str]:
    if mutagen is None:
        return None, "Mutagen unavailable"

    try:
        audio = mutagen.File(path)
    except Exception as exc:
        return None, f"Tag read failed: {exc}"

    if not audio or not getattr(audio, "tags", None):
        return None, "No embedded art"

    if FLAC is not None:
        try:
            if isinstance(audio, FLAC):
                pictures = getattr(audio, "pictures", [])
                if pictures:
                    picture = pictures[0]
                    mime = getattr(picture, "mime", "image/unknown") or "image/unknown"
                    return bytes(picture.data), mime
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            logger.debug("Failed reading FLAC album art from %s", path, exc_info=True)

    try:
        apic_frames = audio.tags.getall("APIC")
        if apic_frames:
            frame = apic_frames[0]
            mime = getattr(frame, "mime", "image/unknown") or "image/unknown"
            return bytes(frame.data), mime
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        logger.debug("Failed reading ID3 APIC album art from %s", path, exc_info=True)

    if MP4 is not None:
        try:
            if isinstance(audio, MP4):
                covr = audio.tags.get("covr")
                if covr:
                    cover = covr[0]
                    mime = "image/unknown"
                    if MP4Cover is not None and isinstance(cover, MP4Cover):
                        if cover.imageformat == MP4Cover.FORMAT_JPEG:
                            mime = "image/jpeg"
                        elif cover.imageformat == MP4Cover.FORMAT_PNG:
                            mime = "image/png"
                    return bytes(cover), mime
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            logger.debug("Failed reading MP4 cover art from %s", path, exc_info=True)

    return None, "No embedded art"


def to_non_progressive_jpeg(data: bytes, quality: int = 90) -> bytes | None:
    if not data:
        return None

    image = QImage.fromData(data)
    if image.isNull():
        return None

    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.WriteOnly):
        return None

    writer = QImageWriter(buffer, b"jpeg")
    writer.setQuality(quality)
    if hasattr(writer, "setProgressiveScanWrite"):
        writer.setProgressiveScanWrite(False)

    ok = writer.write(image)
    buffer.close()
    if not ok:
        return None

    return bytes(encoded)


def write_embedded_album_art(path: Path, jpeg_data: bytes) -> tuple[bool, str]:
    if mutagen is None:
        return False, "Mutagen unavailable"

    try:
        audio = mutagen.File(path)
    except Exception as exc:
        return False, f"Tag read failed: {exc}"

    if not audio:
        return False, "Unsupported or unreadable audio file"

    dims = image_size_from_bytes(jpeg_data)
    width = dims[0] if dims else 0
    height = dims[1] if dims else 0

    if FLAC is not None and Picture is not None and isinstance(audio, FLAC):
        pictures = getattr(audio, "pictures", [])
        source_picture = pictures[0] if pictures else None
        picture = Picture()
        picture.data = jpeg_data
        picture.mime = "image/jpeg"
        picture.type = int(getattr(source_picture, "type", 3) if source_picture is not None else 3)
        picture.desc = str(getattr(source_picture, "desc", "") if source_picture is not None else "")
        picture.width = width
        picture.height = height
        picture.depth = int(
            (getattr(source_picture, "depth", 24) if source_picture is not None else 24) or 24
        )
        picture.colors = int(
            (getattr(source_picture, "colors", 0) if source_picture is not None else 0) or 0
        )

        try:
            audio.clear_pictures()
            audio.add_picture(picture)
            audio.save()
            return True, "Added FLAC artwork" if not pictures else "Updated FLAC artwork"
        except Exception as exc:
            return False, f"Failed to write FLAC artwork: {exc}"

    if APIC is not None:
        tags = getattr(audio, "tags", None)
        if tags is None:
            try:
                add_tags = getattr(audio, "add_tags", None)
                if callable(add_tags):
                    add_tags()
                tags = getattr(audio, "tags", None)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                logger.debug("Failed adding tags for %s: %s", path, exc)
                tags = None

        if tags is None:
            tags = getattr(audio, "tags", None)

        if tags is not None and hasattr(tags, "add") and hasattr(tags, "delall"):
            try:
                apic_frames = tags.getall("APIC") if hasattr(tags, "getall") else []
            except (AttributeError, TypeError, ValueError) as exc:
                logger.debug("Failed reading APIC frames for %s: %s", path, exc)
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

    if MP4 is not None and MP4Cover is not None and isinstance(audio, MP4):
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


def iter_audio_files(target_path: Path):
    for root, dir_names, file_names in os.walk(target_path):
        # Skip hidden directories to reduce traversal and metadata reads.
        dir_names[:] = [name for name in dir_names if not name.startswith(".")]
        for file_name in file_names:
            if file_name.startswith("."):
                continue
            if Path(file_name).suffix.lower() not in AUDIO_FILE_EXTENSIONS:
                continue
            yield Path(root) / file_name


def album_art_scan_result(
    file_path: Path,
    target_path: Path,
) -> tuple[tuple[str, str, str, str, str, str], str]:
    try:
        relative_path = file_path.relative_to(target_path).as_posix()
    except ValueError:
        relative_path = str(file_path)

    metadata_status = ""
    if mutagen is not None:
        try:
            audio = mutagen.File(file_path, easy=True)
            if audio:
                artist = audio.get("artist", [""])[0]
                album = audio.get("album", [""])[0]
                if not artist and not album:
                    metadata_status = "Missing Artist/Album"
                elif not artist:
                    metadata_status = "Missing Artist"
                elif not album:
                    metadata_status = "Missing Album"
        except Exception:
            metadata_status = "Tag Read Error"

    try:
        art_bytes, art_mime = read_embedded_album_art(file_path)
    except Exception as exc:
        logger.warning("Album art read failed for %s: %s", file_path, exc)
        art_bytes, art_mime = None, f"Tag read failed: {exc}"

    if not art_bytes:
        return (
            relative_path,
            "Missing Artwork",
            "",
            "",
            "",
            metadata_status,
        ), "missing"

    if art_mime.lower() != "image/jpeg":
        dims = image_size_from_bytes(art_bytes)
        resolution = f"{dims[0]} x {dims[1]}" if dims else ""
        file_type = art_mime.split("/")[-1].upper() if "/" in art_mime else art_mime.upper()
        return (relative_path, "Incompatible", "False", file_type, resolution, metadata_status), "incompatible"

    jpeg_type = jpeg_scan_type(art_bytes)
    dims = image_size_from_bytes(art_bytes)
    resolution = f"{dims[0]} x {dims[1]}" if dims else ""
    progressive = "True" if jpeg_type == "Progressive" else "False"
    is_compatible = jpeg_type == "Non-progressive"
    status = "Compatible" if is_compatible else "Incompatible"
    classification = "compatible" if is_compatible else "incompatible"
    return (relative_path, status, progressive, "JPEG", resolution, metadata_status), classification


class AlbumArtScanWorker(QObject):
    progress = Signal(int, int, int, int, int, str)
    finished = Signal(list, int, int, int, int)
    cancelled = Signal(int, int, int, int, int)
    failed = Signal(str)

    def __init__(self, target_path: Path):
        super().__init__()
        self.target_path = target_path
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            audio_files = list(iter_audio_files(self.target_path))
            total_audio = len(audio_files)
            rows: list[tuple[str, str, str, str, str, str]] = []
            compatible = 0
            incompatible = 0
            missing_artwork = 0
            scanned_audio = 0

            self.progress.emit(0, total_audio, compatible, incompatible, missing_artwork, "")

            for file_path in audio_files:
                if self._cancel_requested:
                    self.cancelled.emit(scanned_audio, total_audio, compatible, incompatible, missing_artwork)
                    return

                row, classification = album_art_scan_result(file_path, self.target_path)
                rows.append(row)

                scanned_audio += 1
                if classification == "compatible":
                    compatible += 1
                elif classification == "incompatible":
                    incompatible += 1
                else:
                    missing_artwork += 1

                if scanned_audio == total_audio or scanned_audio % 10 == 0:
                    self.progress.emit(
                        scanned_audio,
                        total_audio,
                        compatible,
                        incompatible,
                        missing_artwork,
                        file_path.name
                    )

            self.finished.emit(rows, scanned_audio, compatible, incompatible, missing_artwork)
        except Exception as exc:
            self.failed.emit(str(exc))
