"""
Utility to safely write metadata to audio files.
"""

from __future__ import annotations

from typing import Mapping

import mutagen
from mutagen._vorbis import VCommentDict
from mutagen.apev2 import APEv2
from mutagen.asf import ASF
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4, EasyMP4Tags
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, Encoding, Frames
from mutagen.mp4 import MP4, MP4FreeForm

# EasyID3 / Vorbis keys and the raw ID3 frames / MP4 atoms / ASF names the scanner may have stored.
TAG_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "tit2", "\xa9nam"),
    "artist": ("artist", "tpe1", "\xa9art", "author"),
    "album": ("album", "talb", "\xa9alb", "wm/albumtitle"),
    "albumartist": ("albumartist", "album artist", "album_artist", "tpe2", "aart", "wm/albumartist"),
    "genre": ("genre", "tcon", "\xa9gen", "wm/genre"),
    "date": ("date", "year", "tdrc", "tyer", "\xa9day", "wm/year"),
    "tracknumber": ("tracknumber", "track", "trck", "trkn", "wm/tracknumber"),
    "discnumber": ("discnumber", "disc", "tpos", "disk", "wm/partofset"),
    "composer": ("composer", "tcom", "\xa9wrt", "wm/composer"),
}

_ID3_FRAME_FOR_KEY = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "genre": "TCON",
    "date": "TDRC",
    "tracknumber": "TRCK",
    "discnumber": "TPOS",
    "composer": "TCOM",
}

_ASF_NAME_FOR_KEY = {
    "title": "Title",
    "artist": "Author",
    "album": "WM/AlbumTitle",
    "albumartist": "WM/AlbumArtist",
    "genre": "WM/Genre",
    "date": "WM/Year",
    "tracknumber": "WM/TrackNumber",
    "discnumber": "WM/PartOfSet",
    "composer": "WM/Composer",
}

_APE_NAME_FOR_KEY = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "albumartist": "Album Artist",
    "genre": "Genre",
    "date": "Year",
    "tracknumber": "Track",
    "discnumber": "Disc",
    "composer": "Composer",
}

_MP4_FREEFORM_PREFIX ="----:com.apple.iTunes:"


def _canonical_tag_key(key: str) -> str | None:
    lowered = key.lower()
    if lowered in TAG_KEY_ALIASES:
        return lowered
    for canonical, aliases in TAG_KEY_ALIASES.items():
        if lowered in aliases:
            return canonical
    return None


def aliases_for_tag_key(key: str) -> tuple[str, ...]:
    canonical = _canonical_tag_key(key)
    if canonical is not None:
        return TAG_KEY_ALIASES[canonical]
    return (key.lower(),)


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

    _apply_id3_frames(id3, tags)
    id3.save(filepath, v2_version=3)


def _apply_id3_frames(id3: ID3, tags: Mapping[str, str | None]) -> None:
    written_aliases: set[str] = set()
    for key, val in tags.items():
        if val is None:
            continue
        frame_key = _ID3_FRAME_FOR_KEY.get(_canonical_tag_key(key) or "", key)
        _set_raw_id3_tag(id3, frame_key, val)
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


def _apply_mp4_tags(
    easy: EasyMP4, filepath: str, tags: Mapping[str, str | None]
) -> None:
    """Write MP4 atoms via EasyMP4; keys it cannot map become iTunes freeform atoms."""
    if easy.tags is None:
        easy.add_tags()
    valid = EasyMP4Tags.Get.keys()
    unmapped: dict[str, str | None] = {}
    written: set[str] = set()

    for key, val in tags.items():
        easy_key = _easy_key_for_tag(key, valid)
        if easy_key is None:
            unmapped[key] = val
        elif val is not None:
            easy[easy_key] = [val]
            written.add(easy_key)

    for key, val in tags.items():
        easy_key = _easy_key_for_tag(key, valid)
        if val is None and easy_key is not None and easy_key not in written:
            try:
                del easy[easy_key]
            except KeyError:
                pass
    easy.save()

    if not unmapped:
        return
    mp4 = MP4(filepath)
    if mp4.tags is None:
        mp4.add_tags()
    for key, val in unmapped.items():
        atom = f"{_MP4_FREEFORM_PREFIX}{key}"
        if val is None:
            mp4.tags.pop(atom, None)
        else:
            mp4.tags[atom] = [MP4FreeForm(val.encode("utf-8"))]
    mp4.save()


def _apply_asf_tags(audio: ASF, tags: Mapping[str, str | None]) -> None:
    """Write WMA attributes under the names Windows Media players read."""
    written_aliases: set[str] = set()
    for key, val in tags.items():
        if val is None:
            continue
        name = _ASF_NAME_FOR_KEY.get(_canonical_tag_key(key) or "", key)
        _delete_tag_from_mapping(audio.tags, key)
        audio.tags[name] = [val]
        written_aliases.update(alias.lower() for alias in aliases_for_tag_key(key))

    for key, val in tags.items():
        if val is not None:
            continue
        aliases = {alias.lower() for alias in aliases_for_tag_key(key)}
        if aliases & written_aliases:
            continue
        _delete_tag_from_mapping(audio.tags, key)
    audio.save()


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
        # Easy mode only swaps MP3, TrueAudio and MP4 classes, so one open suffices.
        audio = mutagen.File(filepath, easy=True)
        if audio is None:
            return False, "Unsupported or corrupt audio file."

        only_deletes = all(val is None for val in tags.values())

        if getattr(audio, "ID3", None) is EasyID3:
            if audio.tags is None:
                if only_deletes:
                    return True, ""
                audio.add_tags()
            unmapped = _apply_easy_id3(audio.tags, tags)
            audio.save(v2_version=3)
            if unmapped:
                _apply_raw_id3_tags(filepath, dict(unmapped))
            return True, ""

        if isinstance(audio, EasyMP4):
            _apply_mp4_tags(audio, filepath, tags)
            return True, ""

        if isinstance(audio, ASF):
            if audio.tags is None:
                if only_deletes:
                    return True, ""
                audio.add_tags()
            _apply_asf_tags(audio, tags)
            return True, ""

        if audio.tags is None:
            if only_deletes:
                return True, ""
            if not _ensure_tag_structure(audio):
                return False, "No tag structure found in file."

        # WAV, AIFF, DSF and similar containers carry an embedded ID3 tag.
        if isinstance(audio.tags, ID3):
            _apply_id3_frames(audio.tags, tags)
            audio.save(v2_version=3)
            return True, ""

        # Vorbis and APEv2 have standard field names, so drop other spellings
        # of the same field rather than leaving a stale duplicate behind.
        standard_names = None
        if isinstance(audio.tags, VCommentDict):
            standard_names = {key: key for key in TAG_KEY_ALIASES}
        elif isinstance(audio.tags, APEv2):
            standard_names = _APE_NAME_FOR_KEY

        written_aliases: set[str] = set()
        for key, val in tags.items():
            if val is None:
                continue
            name = key
            if standard_names is not None:
                _delete_tag_from_mapping(audio.tags, key)
                name = standard_names.get(_canonical_tag_key(key) or "", key)
            audio.tags[name] = [val]
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
