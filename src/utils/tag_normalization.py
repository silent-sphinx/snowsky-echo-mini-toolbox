"""Shared tag cleanup used for album grouping and artwork lookups.

Kept free of Qt imports so the drive scanner's process workers can use it.
"""

from __future__ import annotations

import re
import unicodedata

# Scanner defaults that mean "no value" rather than a real tag.
PLACEHOLDER_VALUES = {
    "unknown",
    "unknown title",
    "unknown artist",
    "unknown album",
    "unknown album artist",
    "va",
    "n/a",
    "none",
    "null",
    "<unknown>",
    "[unknown]",
}

# Directory names that sit between the album folder and its tracks.
_DISC_FOLDER_PATTERN = re.compile(
    r"^(?:disc|disk|cd|dvd|vol|volume|part|pt)[\s._-]*\d+[a-z]?$",
    re.IGNORECASE,
)

# Edition/format qualifiers worth dropping from a search query.
_EDITION_PATTERN = re.compile(
    r"[\(\[\{]\s*(?:"
    r"disc|disk|cd|vol|volume|part|pt|"
    r"deluxe|expanded|special|limited|collector|anniversary|"
    r"remaster|remastered|reissue|edition|version|bonus|"
    r"explicit|clean|mono|stereo|hd|hi-?res|"
    r"\d{4}\s*remaster"
    r")\b[^\)\]\}]*[\)\]\}]",
    re.IGNORECASE,
)

_FEATURING_PATTERN = re.compile(
    r"\s*[\(\[]?\s*(?:feat\.?|featuring|ft\.?|with)\s+[^\)\]]*[\)\]]?\s*$",
    re.IGNORECASE,
)

_DISC_SUFFIX_PATTERN = re.compile(
    r"[\s,;:-]+(?:disc|disk|cd|vol|volume|part|pt)[\s._-]*\d+[a-z]?\s*$",
    re.IGNORECASE,
)

_WHITESPACE_PATTERN = re.compile(r"\s+")

_APOSTROPHE_PATTERN = re.compile(r"[\u2019\u02bc']")


def coerce_tag_text(value, fallback: str = "") -> str:
    """Force a mutagen/tag value into a NUL-free string Qt can display."""
    if value is None:
        return fallback
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
    else:
        text = str(value)
    text = text.replace("\x00", " ")
    return text if text else fallback


def clean_tag_value(value: str | None) -> str:
    """Collapse whitespace and strip stray separators from a raw tag."""
    if not value:
        return ""

    text = unicodedata.normalize("NFC", coerce_tag_text(value))
    text = _WHITESPACE_PATTERN.sub(" ", text).strip()
    return text.strip(" -_/\\|")


def is_placeholder(value: str | None) -> bool:
    """Report whether a tag carries no usable identity."""
    cleaned = clean_tag_value(value)
    if not cleaned:
        return True
    return cleaned.casefold() in PLACEHOLDER_VALUES


def tag_or_empty(value: str | None) -> str:
    """Return a cleaned tag, or an empty string when it is a placeholder."""
    if is_placeholder(value):
        return ""
    return clean_tag_value(value)


def normalize_key(value: str | None) -> str:
    """Build a case- and punctuation-insensitive key for grouping."""
    cleaned = tag_or_empty(value)
    if not cleaned:
        return ""

    folded = unicodedata.normalize("NFKD", cleaned).casefold()
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    # Apostrophes join words ("Pepper's" == "Peppers"), other punctuation splits them.
    folded = _APOSTROPHE_PATTERN.sub("", folded)
    folded = re.sub(r"[^\w\s]", " ", folded)
    return _WHITESPACE_PATTERN.sub(" ", folded).strip()


def normalize_album_key(value: str | None) -> str:
    """Group key for an album, ignoring disc numbering and edition suffixes."""
    cleaned = tag_or_empty(value)
    if not cleaned:
        return ""

    stripped = _EDITION_PATTERN.sub(" ", cleaned)
    stripped = _DISC_SUFFIX_PATTERN.sub("", stripped)
    return normalize_key(stripped) or normalize_key(cleaned)


def normalize_artist_key(value: str | None) -> str:
    """Group key for an artist, ignoring featured-guest suffixes."""
    cleaned = tag_or_empty(value)
    if not cleaned:
        return ""

    stripped = _FEATURING_PATTERN.sub("", cleaned)
    return normalize_key(stripped) or normalize_key(cleaned)


def search_album_title(value: str | None) -> str:
    """Album text for an external search, with edition noise removed."""
    cleaned = tag_or_empty(value)
    if not cleaned:
        return ""

    stripped = _EDITION_PATTERN.sub(" ", cleaned)
    stripped = _DISC_SUFFIX_PATTERN.sub("", stripped)
    stripped = _WHITESPACE_PATTERN.sub(" ", stripped).strip(" -_,;:")
    return stripped or cleaned


def search_artist_name(value: str | None) -> str:
    """Artist text for an external search, with featured guests removed."""
    cleaned = tag_or_empty(value)
    if not cleaned:
        return ""

    stripped = _FEATURING_PATTERN.sub("", cleaned)
    stripped = _WHITESPACE_PATTERN.sub(" ", stripped).strip(" -_,;:")
    return stripped or cleaned


def is_disc_folder_name(name: str) -> bool:
    """Report whether a directory name is a disc subfolder of an album."""
    return bool(_DISC_FOLDER_PATTERN.match(clean_tag_value(name)))


def extract_year(value: str | None) -> str:
    """Pull a four-digit year out of a date tag."""
    cleaned = tag_or_empty(value)
    if not cleaned:
        return ""
    match = re.search(r"(\d{4})", cleaned)
    return match.group(1) if match else ""


def first_tag(tags: dict[str, str] | None, *names: str) -> str:
    """Look up the first populated tag among case-insensitive aliases."""
    if not tags:
        return ""

    lowered = {str(key).casefold(): value for key, value in tags.items()}
    for name in names:
        value = lowered.get(name.casefold())
        cleaned = tag_or_empty(value)
        if cleaned:
            return cleaned
    return ""


_ID3_EASY_KEYS = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "genre": "TCON",
}
_MP4_EASY_KEYS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "albumartist": "aART",
    "genre": "\xa9gen",
}


def _values_as_strings(values) -> list[str]:
    if not values:
        return []
    if not isinstance(values, list):
        values = [values]
    texts: list[str] = []
    for value in values:
        if isinstance(value, bytes):
            texts.append(value.decode("utf-8", "replace"))
        else:
            texts.append(str(value))
    return [text for text in texts if text]


def easy_tag_values(audio, key: str) -> list[str]:
    """Return text values for an easy tag name from a mutagen File."""
    if audio is None:
        return []

    try:
        texts = _values_as_strings(audio.get(key))
        if texts:
            return texts
    except Exception:
        pass

    tags = getattr(audio, "tags", None)
    if tags is None:
        return []

    frame_id = _ID3_EASY_KEYS.get(key)
    if frame_id and hasattr(tags, "getall"):
        try:
            frames = tags.getall(frame_id)
        except Exception:
            frames = None
        if frames:
            texts: list[str] = []
            for frame in frames:
                text = getattr(frame, "text", None)
                if text:
                    texts.extend(_values_as_strings(list(text)))
                else:
                    texts.append(str(frame))
            if texts:
                return texts

    atom = _MP4_EASY_KEYS.get(key)
    if atom:
        try:
            texts = _values_as_strings(tags.get(atom))
            if texts:
                return texts
        except Exception:
            pass
    return []


def first_easy_tag(audio, *keys: str) -> str:
    """Return the first populated easy tag from a mutagen File."""
    for key in keys:
        for value in easy_tag_values(audio, key):
            cleaned = tag_or_empty(value)
            if cleaned:
                return cleaned
    return ""
