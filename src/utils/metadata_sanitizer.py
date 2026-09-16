import logging
from pathlib import Path

import mutagen
from mutagen.flac import FLAC

logger = logging.getLogger(__name__)

# Firmware copies Vorbis comment values into fixed 128-char SRAM arrays.
MAX_TAG_VALUE_LENGTH = 128

# Core tags the firmware extracts. These must appear before any oversized
# comment (e.g. LYRICS, TIDAL_DATA); otherwise the parser overflows SRAM and
# later tags never make it into the on-device library (albums fail to group).
CORE_TAG_WRITE_ORDER = (
    "title",
    "artist",
    "album",
    "albumartist",
    "tracknumber",
    "discnumber",
    "genre",
)
CORE_TAG_SET = set(CORE_TAG_WRITE_ORDER)

# Kept on sanitize so Lyrics Manager can still export a sidecar. Moved after
# core tags so it cannot abort ARTIST/ALBUM parsing.
VORBIS_KEEP_ON_SANITIZE = CORE_TAG_SET | {"lyrics"}

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


def oversized_comment_before_core_tags(
    pairs: list[tuple[str, str]],
    max_length: int = MAX_TAG_VALUE_LENGTH,
) -> tuple[str, int, tuple[str, ...]] | None:
    """Return (key, length, pending_core_tags) if an oversized comment
    appears before a firmware-parsed tag that exists later in the file.

    Device testing: lucida FLACs with ALBUM/ARTIST first and LYRICS later play
    and group. The same file with LYRICS before ALBUM hard-reboots. Files with
    ALBUM first but ARTIST/TITLE after LYRICS play, yet fail to group into
    albums because those tags never get copied out of the smashed SRAM buffer.
    """
    present: list[str] = []
    seen: set[str] = set()
    for key, _ in pairs:
        lowered = key.lower()
        if lowered in CORE_TAG_SET and lowered not in seen:
            present.append(lowered)
            seen.add(lowered)
    if not present:
        return None

    pending = set(present)
    for key, value in pairs:
        lowered = key.lower()
        if lowered in pending:
            pending.discard(lowered)
            if not pending:
                return None
            continue
        if pending and len(value) > max_length:
            still = tuple(name for name in CORE_TAG_WRITE_ORDER if name in pending)
            return key, len(value), still
    return None


def oversized_comment_before_album(
    pairs: list[tuple[str, str]],
    max_length: int = MAX_TAG_VALUE_LENGTH,
) -> tuple[str, int] | None:
    """Back-compat wrapper around oversized_comment_before_core_tags."""
    result = oversized_comment_before_core_tags(pairs, max_length)
    if result is None:
        return None
    return result[0], result[1]


class MetadataSanitizer:
    def __init__(self):
        self.known_tags = CORE_TAG_SET
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

    def check_metadata(self, file_path: str | Path, audio=None) -> tuple[bool, str]:
        """
        Check if the metadata contains too many unknown tags that would
        exceed the device's parser limits, or Vorbis comments in an order
        that overflows the firmware's 128-char SRAM copy buffer.

        For FLAC/OGG: checks Vorbis Comments against the 20-tag unknown limit
        and requires every firmware-parsed tag (TITLE, ARTIST, ALBUM, …) to
        appear before any comment longer than 128 chars.
        For ID3v2 formats (MP3, WAV, DSF): checks for excessive non-standard
        frames that push core tags outside the firmware's ~2-4KB read window.

        Returns a tuple: (is_compatible, reason_if_not)
        """
        try:
            opened = audio
            if opened is None:
                opened = mutagen.File(file_path)
            if opened is None:
                return False, "Could not read audio tags"
            if opened.tags is None:
                return True, ""

            if self._is_vorbis_tagged(opened):
                unknown_count = 0
                for key in opened.tags.keys():
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

                offending = oversized_comment_before_core_tags(_vorbis_comment_pairs(opened.tags))
                if offending is not None:
                    key, length, pending = offending
                    pending_label = ", ".join(name.upper() for name in pending)
                    return False, (
                        f"Oversized '{key}' tag ({length} chars) appears before {pending_label}. "
                        f"The firmware copies Vorbis comments into a {MAX_TAG_VALUE_LENGTH}-char "
                        f"SRAM buffer; tags after an oversized comment are often missing on the "
                        f"device (albums fail to group). Click 'Convert Selected Incompatible "
                        f"Media' to move core tags first and strip unsupported tags."
                    )

                return True, ""

            # ID3v2 formats: the firmware reads a fixed ~2-4KB window of the
            # ID3v2 header. Non-standard frames (TXXX, COMM, USLT, etc.) at
            # the start push core tags (TIT2, TPE1) outside this window.
            if hasattr(opened.tags, "getall"):
                unknown_count = 0
                for frame_id in opened.tags.keys():
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
            return False, f"Could not read tags: {e}"

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
        Strip tags the firmware does not parse, reorder Vorbis comments so
        core tags precede oversized values, and rewrite ID3 UTF-8 text frames
        to UTF-16.

        By default every unsupported tag is removed (LYRICS is kept and moved
        after the core tags). Pass preserve_third_party_tags=True to keep
        extras, trimming only when they exceed the 20-tag crash limit.
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None:
                return False
            if audio.tags is None:
                return True

            changed = False

            if self._is_vorbis_tagged(audio):
                unknown_tags = []
                for key in audio.tags.keys():
                    if key.lower() not in VORBIS_KEEP_ON_SANITIZE:
                        unknown_tags.append(key)

                if unknown_tags:
                    if preserve_third_party_tags:
                        extra_count = sum(
                            1 for key in audio.tags.keys()
                            if key.lower() not in self.known_tags
                        )
                        if extra_count > self.limit:
                            tags_to_delete = []
                            for key in unknown_tags:
                                key_lower = key.lower()
                                if key_lower.startswith(self.bloat_prefixes):
                                    tags_to_delete.append(key)

                            needed_deletions = extra_count - self.limit
                            if needed_deletions > len(tags_to_delete):
                                for key in unknown_tags:
                                    if key not in tags_to_delete:
                                        tags_to_delete.append(key)
                                        if len(tags_to_delete) >= needed_deletions:
                                            break

                            for key in tags_to_delete:
                                del audio[key]
                            changed = True
                    else:
                        for key in unknown_tags:
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

                if unknown_tags:
                    if preserve_third_party_tags:
                        if len(unknown_tags) > self.limit:
                            needed_deletions = len(unknown_tags) - self.limit
                            for key in unknown_tags[:needed_deletions]:
                                del audio[key]
                            changed = True
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
