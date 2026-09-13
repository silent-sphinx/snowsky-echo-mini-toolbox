import logging
from pathlib import Path

import mutagen
from mutagen.flac import FLAC

logger = logging.getLogger(__name__)

# Firmware copies Vorbis comment values into fixed 128-char SRAM arrays.
MAX_TAG_VALUE_LENGTH = 128

# Core tags the firmware extracts. ALBUM must appear before any oversized
# comment (e.g. LYRICS); otherwise the parser overflows and the device reboots.
CORE_TAG_WRITE_ORDER = (
    "title",
    "artist",
    "album",
    "albumartist",
    "tracknumber",
    "discnumber",
    "genre",
)

# ID3v2 frames the firmware actually reads. Extra frames can push these
# outside the ~2-4KB SRAM window. USLT is kept on sanitize (same as Vorbis LYRICS).
CORE_ID3_FRAMES = {"TIT2", "TPE1", "TALB", "TPE2", "TCON", "TRCK", "TPOS", "APIC"}
ID3_PRESERVE_FRAMES = CORE_ID3_FRAMES | {"USLT"}
# mutagen: 0=Latin-1, 1=UTF-16 with BOM, 2=UTF-16BE, 3=UTF-8
ID3_UTF16_ENCODING = 1
ID3_UTF8_ENCODING = 3
ID3_MP3_EXTENSIONS = {".mp3", ".mp1", ".mp2"}


def _vorbis_comment_pairs(tags) -> list[tuple[str, str]]:
    """Return Vorbis comments in file order as (key, value) pairs."""
    if tags is None:
        return []
    try:
        # mutagen.VComment is a list of (key, value); iterate the list API so
        # dict-style __iter__ (keys only) cannot hide later duplicates.
        raw = list(list.__iter__(tags)) if isinstance(tags, list) else list(tags)
    except Exception:
        return []
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, tuple) and len(item) >= 2:
            pairs.append((str(item[0]), str(item[1])))
        else:
            return []
    return pairs


def oversized_comment_before_album(
    pairs: list[tuple[str, str]],
    max_length: int = MAX_TAG_VALUE_LENGTH,
) -> tuple[str, int] | None:
    """Return (key, length) if an oversized comment appears before ALBUM.

    Device testing: lucida FLACs with ALBUM first and LYRICS later play.
    The same file with LYRICS before ALBUM hard-reboots the player.
    TITLE after LYRICS is safe, so only ALBUM is required to precede
    oversized comments.
    """
    has_album = any(key.lower() == "album" for key, _ in pairs)
    if not has_album:
        return None

    seen_album = False
    for key, value in pairs:
        if key.lower() == "album":
            seen_album = True
            continue
        if seen_album:
            continue
        if len(value) > max_length:
            return key, len(value)
    return None


class MetadataSanitizer:
    def __init__(self):
        self.known_tags = set(CORE_TAG_WRITE_ORDER)
        self.bloat_prefixes = ("musicbrainz_", "tidal_")
        self.limit = 20

    def _is_vorbis_tagged(self, audio) -> bool:
        if isinstance(audio, FLAC):
            return True
        try:
            from mutagen.oggvorbis import OggVorbis

            if isinstance(audio, OggVorbis):
                return True
        except Exception:
            pass
        return False

    def _is_id3_tagged(self, audio) -> bool:
        tags = getattr(audio, "tags", None)
        return tags is not None and hasattr(tags, "getall")

    def _fix_id3_utf8_encoding(self, audio) -> bool:
        """Rewrite ID3 text frames from UTF-8 (0x03) to UTF-16 (0x01).

        The firmware has no UTF-8 decoder, so encoding 3 renders as garbage.
        """
        tags = audio.tags
        if tags is None:
            return False
        changed = False
        for frame in tags.values():
            frames = frame if isinstance(frame, list) else [frame]
            for item in frames:
                if getattr(item, "encoding", None) == ID3_UTF8_ENCODING:
                    item.encoding = ID3_UTF16_ENCODING
                    changed = True
        return changed

    def _save(self, audio, file_path: str | Path) -> None:
        # ID3v2.3 has no UTF-8; mutagen will persist UTF-16 instead of
        # rewriting frames back to encoding 3 on a v2.4 save.
        if Path(file_path).suffix.lower() in ID3_MP3_EXTENSIONS:
            audio.save(v2_version=3)
        else:
            audio.save()

    def check_metadata(self, file_path: str | Path) -> tuple[bool, str]:
        """
        Check if the metadata contains too many unknown tags that would
        exceed the device's parser limits, or Vorbis comments in an order
        that overflows the firmware's 128-char SRAM copy buffer.

        For FLAC/OGG: checks Vorbis Comments against the 20-tag unknown limit
        and requires ALBUM to appear before any comment longer than 128 chars.
        For ID3v2 formats (MP3, WAV, DSF): checks for excessive non-standard
        frames that push core tags outside the firmware's ~2-4KB read window.

        Returns a tuple: (is_compatible, reason_if_not)
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None or audio.tags is None:
                return True, ""

            if self._is_vorbis_tagged(audio):
                unknown_count = 0
                for key in audio.tags.keys():
                    if key.lower() not in self.known_tags:
                        unknown_count += 1

                if unknown_count > self.limit:
                    return False, (
                        f"Snowsky Hardware Limit Exceeded: File contains {unknown_count} "
                        f"unknown tags, exceeding the device's hardcoded safety limit of "
                        f"{self.limit}. The device's parser will abort and crash. Click "
                        f"'Convert Selected Incompatible Media' to strip useless tracking "
                        f"tags and safely reduce the count."
                    )

                offending = oversized_comment_before_album(_vorbis_comment_pairs(audio.tags))
                if offending is not None:
                    key, length = offending
                    return False, (
                        f"Oversized '{key}' tag ({length} chars) appears before ALBUM. "
                        f"The firmware copies Vorbis comments into a {MAX_TAG_VALUE_LENGTH}-char "
                        f"SRAM buffer and will crash or reject the file. Click "
                        f"'Convert Selected Incompatible Media' to move ALBUM/TITLE ahead "
                        f"of oversized comments."
                    )

                return True, ""

            # ID3v2 formats: the firmware reads a fixed ~2-4KB window of the
            # ID3v2 header. Non-standard frames (TXXX, COMM, USLT, etc.) at
            # the start push core tags (TIT2, TPE1) outside this window.
            if hasattr(audio.tags, "getall"):
                unknown_count = 0
                for frame_id in audio.tags.keys():
                    # Extract base frame ID (e.g. "TXXX:foo" -> "TXXX")
                    base_id = str(frame_id).split(":")[0].upper()
                    if base_id not in CORE_ID3_FRAMES:
                        unknown_count += 1

                if unknown_count > self.limit:
                    return False, (
                        f"Snowsky Hardware Limit Exceeded: File contains {unknown_count} "
                        f"non-standard ID3 frames, exceeding the device's safety limit of "
                        f"{self.limit}. Excess frames push core tags outside the firmware's "
                        f"read window, causing missing metadata on the device."
                    )

            return True, ""

        except Exception as e:
            logger.debug(f"Failed to check metadata for {file_path}: {e}")
            return True, ""

    def _reorder_vorbis_comments(self, audio) -> bool:
        """Move core tags first, then short tags, then oversized values (e.g. LYRICS)."""
        tags = audio.tags
        if tags is None or not hasattr(tags, "clear") or not hasattr(tags, "append"):
            return False

        pairs = _vorbis_comment_pairs(tags)
        if not pairs:
            return False

        core: list[tuple[str, str]] = []
        normal: list[tuple[str, str]] = []
        oversized: list[tuple[str, str]] = []
        for key, value in pairs:
            if key.lower() in self.known_tags:
                core.append((key, value))
            elif len(value) > MAX_TAG_VALUE_LENGTH:
                oversized.append((key, value))
            else:
                normal.append((key, value))

        rank = {name: index for index, name in enumerate(CORE_TAG_WRITE_ORDER)}
        core.sort(key=lambda item: rank.get(item[0].lower(), 99))
        reordered = core + normal + oversized
        if reordered == pairs:
            return False

        tags.clear()
        for key, value in reordered:
            tags.append((key, value))
        return True

    def sanitize(self, file_path: str | Path, preserve_third_party_tags: bool = False) -> bool:
        """
        Strip bloat tags until the unknown tag count is below the limit,
        reorder Vorbis comments so ALBUM/TITLE precede oversized values, and
        rewrite ID3 UTF-8 text frames to UTF-16.
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None or audio.tags is None:
                return False

            changed = False

            if self._is_vorbis_tagged(audio):
                unknown_tags = []
                for key in audio.tags.keys():
                    if key.lower() not in self.known_tags:
                        unknown_tags.append(key)

                if len(unknown_tags) > self.limit:
                    if preserve_third_party_tags:
                        # Keep as many custom tags as possible by targeting known bloat first.
                        tags_to_delete = []
                        for key in unknown_tags:
                            key_lower = key.lower()
                            if key_lower.startswith(self.bloat_prefixes):
                                tags_to_delete.append(key)

                        needed_deletions = len(unknown_tags) - self.limit
                        if needed_deletions > len(tags_to_delete):
                            for key in unknown_tags:
                                if key not in tags_to_delete and key.lower() != "lyrics":
                                    tags_to_delete.append(key)
                                    if len(tags_to_delete) >= needed_deletions:
                                        break

                        for key in tags_to_delete:
                            del audio[key]
                    else:
                        # Delete all unknown tags except 'lyrics'. Lyrics stay but are
                        # moved after core tags on save so they cannot abort ALBUM parsing.
                        for key in unknown_tags:
                            if key.lower() != "lyrics":
                                del audio[key]
                    changed = True

                if self._reorder_vorbis_comments(audio):
                    changed = True
            elif self._is_id3_tagged(audio):
                if self._fix_id3_utf8_encoding(audio):
                    changed = True

                unknown_tags = []
                for key in audio.tags.keys():
                    base_id = str(key).split(":")[0].upper()
                    if base_id not in ID3_PRESERVE_FRAMES:
                        unknown_tags.append(key)

                if len(unknown_tags) > self.limit:
                    if preserve_third_party_tags:
                        needed_deletions = len(unknown_tags) - self.limit
                        for key in unknown_tags[:needed_deletions]:
                            del audio[key]
                    else:
                        for key in unknown_tags:
                            del audio[key]
                    changed = True

            if changed:
                self._save(audio, file_path)
            return True

        except Exception as e:
            logger.debug(f"Failed to sanitize metadata for {file_path}: {e}")
            return False
