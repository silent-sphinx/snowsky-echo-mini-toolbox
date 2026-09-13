"""
Utility to safely write metadata to audio files.
"""

from __future__ import annotations

from typing import Mapping

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, Encoding, Frames

# EasyID3 / Vorbis keys and the raw frames the scanner may have stored.
TAG_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "tit2"),
    "artist": ("artist", "tpe1"),
    "album": ("album", "talb"),
    "albumartist": ("albumartist", "album artist", "album_artist", "tpe2"),
    "genre": ("genre", "tcon"),
    "date": ("date", "year", "tdrc", "tyer"),
    "tracknumber": ("tracknumber", "track", "trck"),
    "discnumber": ("discnumber", "disc", "tpos"),
    "composer": ("composer", "tcom"),
}


def aliases_for_tag_key(key: str) -> tuple[str, ...]:
    lowered = key.lower()
    if lowered in TAG_KEY_ALIASES:
        return TAG_KEY_ALIASES[lowered]
    for aliases in TAG_KEY_ALIASES.values():
        if lowered in {alias.lower() for alias in aliases}:
            return aliases
    return (lowered,)


def _easy_key_for_tag(key: str, valid_easy_keys) -> str | None:
    """Map a UI / raw-frame key onto an EasyID3 name, if EasyID3 can store it."""
    lowered = key.lower()
    if lowered in valid_easy_keys:
        return lowered
    for easy_key, aliases in TAG_KEY_ALIASES.items():
        if lowered == easy_key or lowered in {alias.lower() for alias in aliases}:
            if easy_key in valid_easy_keys:
                return easy_key
            return None
    return None


def _delete_tag_from_mapping(tags, key: str) -> None:
    aliases = {alias.lower() for alias in aliases_for_tag_key(key)}
    for existing in list(tags.keys()):
        if str(existing).lower() in aliases:
            try:
                del tags[existing]
            except KeyError:
                pass


def _set_raw_id3_tag(id3: ID3, key: str, value: str) -> None:
    lowered = key.lower()
    if lowered.startswith("txxx:"):
        desc = key.split(":", 1)[1]
        _delete_tag_from_mapping(id3, key)
        id3.add(TXXX(encoding=Encoding.UTF16, desc=desc, text=value))
        return

    frame_id = key.upper()
    if (
        len(frame_id) == 4
        and frame_id.startswith("T")
        and frame_id != "TXXX"
        and frame_id in Frames
    ):
        _delete_tag_from_mapping(id3, frame_id)
        try:
            id3.add(Frames[frame_id](encoding=Encoding.UTF16, text=value))
            return
        except (TypeError, ValueError):
            pass

    _delete_tag_from_mapping(id3, f"TXXX:{key}")
    id3.add(TXXX(encoding=Encoding.UTF16, desc=key, text=value))


def _apply_raw_id3_tags(filepath: str, tags: Mapping[str, str | None]) -> None:
    try:
        id3 = ID3(filepath)
    except ID3NoHeaderError:
        if all(val is None for val in tags.values()):
            return
        id3 = ID3()

    written_aliases: set[str] = set()
    for key, val in tags.items():
        if val is None:
            continue
        _set_raw_id3_tag(id3, key, val)
        written_aliases.update(alias.lower() for alias in aliases_for_tag_key(key))
        written_aliases.add(f"txxx:{key.lower()}")

    for key, val in tags.items():
        if val is not None:
            continue
        aliases = {alias.lower() for alias in aliases_for_tag_key(key)}
        aliases.add(f"txxx:{key.lower()}")
        if aliases & written_aliases:
            continue
        _delete_tag_from_mapping(id3, key)
        _delete_tag_from_mapping(id3, f"TXXX:{key}")

    id3.save(filepath, v2_version=3)


def _apply_easy_id3(easy: EasyID3, tags: Mapping[str, str | None]) -> list[tuple[str, str | None]]:
    """Apply EasyID3-known tags. Return pairs EasyID3 cannot store."""
    valid = easy.valid_keys.keys()
    unmapped: list[tuple[str, str | None]] = []
    written: set[str] = set()

    for key, val in tags.items():
        easy_key = _easy_key_for_tag(key, valid)
        if easy_key is None:
            unmapped.append((key, val))
            continue
        if val is None:
            continue
        easy[easy_key] = [val]
        written.add(easy_key)

    for key, val in tags.items():
        if val is not None:
            continue
        easy_key = _easy_key_for_tag(key, valid)
        if easy_key is None:
            unmapped.append((key, val))
            continue
        if easy_key in written:
            continue
        try:
            del easy[easy_key]
        except KeyError:
            pass

    return unmapped


def _ensure_tag_structure(audio) -> bool:
    if audio.tags is not None:
        return True
    if not hasattr(audio, "add_tags"):
        return False
    try:
        audio.add_tags()
    except Exception:
        return audio.tags is not None
    return audio.tags is not None


def save_metadata(filepath: str, tags: Mapping[str, str | None]) -> tuple[bool, str]:
    """
    Save tag values to an audio file.

    A value of None deletes that tag. Supports MP3 via EasyID3 and
    FLAC/OGG-style dictionaries via mutagen.

    Returns:
        (success: bool, error_message: str)
    """
    try:
        audio = mutagen.File(filepath, easy=False)
        if audio is None:
            return False, "Unsupported or corrupt audio file."

        only_deletes = all(val is None for val in tags.values())
        is_mp3 = filepath.lower().endswith(".mp3")

        if is_mp3:
            easy = None
            try:
                easy = EasyID3(filepath)
            except ID3NoHeaderError:
                if only_deletes:
                    return True, ""
                ID3().save(filepath, v2_version=3)
                easy = EasyID3(filepath)
            except Exception:
                easy = None

            if easy is not None:
                unmapped = _apply_easy_id3(easy, tags)
                easy.save(filepath, v2_version=3)
                if unmapped:
                    _apply_raw_id3_tags(filepath, dict(unmapped))
                return True, ""

        if audio.tags is None:
            if only_deletes:
                return True, ""
            if not _ensure_tag_structure(audio):
                return False, "No tag structure found in file."

        written_aliases: set[str] = set()
        for key, val in tags.items():
            if val is None:
                continue
            audio.tags[key] = [val]
            written_aliases.update(alias.lower() for alias in aliases_for_tag_key(key))

        for key, val in tags.items():
            if val is not None:
                continue
            aliases = {alias.lower() for alias in aliases_for_tag_key(key)}
            if aliases & written_aliases:
                continue
            _delete_tag_from_mapping(audio.tags, key)

        audio.save()
        return True, ""

    except Exception as e:
        return False, f"Failed to save metadata: {str(e)}"
