"""
Utility to safely write metadata to audio files.
"""

from __future__ import annotations

from typing import Mapping

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, ID3NoHeaderError

# EasyID3 / Vorbis keys and the raw frames the scanner may have stored.
TAG_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "tit2"),
    "artist": ("artist", "tpe1"),
    "album": ("album", "talb"),
    "albumartist": ("albumartist", "album artist", "album_artist", "tpe2"),
    "genre": ("genre", "tcon"),
    "date": ("date", "year", "tdrc", "tyer"),
}


def aliases_for_tag_key(key: str) -> tuple[str, ...]:
    lowered = key.lower()
    return TAG_KEY_ALIASES.get(lowered, (lowered,))


def _delete_tag_from_mapping(tags, key: str) -> None:
    aliases = {alias.lower() for alias in aliases_for_tag_key(key)}
    for existing in list(tags.keys()):
        if str(existing).lower() in aliases:
            try:
                del tags[existing]
            except KeyError:
                pass


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
                valid_easy_keys = easy.valid_keys.keys()
                for key, val in tags.items():
                    k_lower = key.lower()
                    if k_lower not in valid_easy_keys:
                        continue
                    if val is None:
                        try:
                            del easy[k_lower]
                        except KeyError:
                            pass
                    else:
                        easy[k_lower] = [val]
                easy.save(filepath, v2_version=3)
                return True, ""

        if audio.tags is None:
            if only_deletes:
                return True, ""
            return False, "No tag structure found in file."

        for key, val in tags.items():
            if val is None:
                _delete_tag_from_mapping(audio.tags, key)
            else:
                audio.tags[key] = [val]
        audio.save()
        return True, ""

    except Exception as e:
        return False, f"Failed to save metadata: {str(e)}"
