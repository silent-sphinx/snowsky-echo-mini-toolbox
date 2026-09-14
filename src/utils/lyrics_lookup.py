"""LRCLIB lookup client for missing lyrics.

Kept free of Qt imports. Rate-limits and caches results for one session.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..constants import APP_VERSION
from ..models.drive_data import TrackMetadata
from .tag_normalization import tag_or_empty

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net"
USER_AGENT = (
    f"SnowskyEchoMiniToolbox/{APP_VERSION} "
    "( https://github.com/Snowsky-Echo-Mini-Toolbox )"
)
REQUEST_TIMEOUT = 12.0
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class LookupCancelled(Exception):
    """Raised when the caller cancels an in-flight lookup."""


@dataclass
class LyricsLookupQuery:
    title: str
    artist: str
    album: str
    duration: int
    error: str = ""

    @property
    def cache_key(self) -> tuple[str, str, str, int]:
        return (self.title, self.artist, self.album, self.duration)


@dataclass
class LyricsLookupResult:
    filepath: str
    relative_path: str
    title: str
    artist: str
    album: str
    status: str
    source: str
    preview: str = ""
    lyrics_text: str = ""
    apply_status: str = "-"
    is_selected: bool = False
    error: str = ""

    @property
    def can_apply(self) -> bool:
        return self.status == "Found" and bool(self.lyrics_text.strip())


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def lyrics_text_from_record(record: dict[str, object]) -> str:
    synced = str(record.get("syncedLyrics") or "").strip()
    if synced:
        return synced
    return str(record.get("plainLyrics") or "").strip()


def lookup_query_for_track(track: TrackMetadata) -> LyricsLookupQuery:
    """Build an LRCLIB query from already-scanned metadata."""
    title = tag_or_empty(track.title)
    artist = tag_or_empty(track.artist) or tag_or_empty(track.album_artist)
    album = tag_or_empty(track.album)

    if not title:
        title = Path(track.filepath).stem.strip()

    for separator in (";", "/", ","):
        if separator in artist:
            artist = artist.split(separator, 1)[0].strip()

    duration = 0
    try:
        duration = max(0, int(round(float(track.duration_seconds or 0.0))))
    except (TypeError, ValueError):
        duration = 0

    error = ""
    if not title:
        error = "Track title is missing"
    elif not artist:
        error = "Artist is missing"

    return LyricsLookupQuery(
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        error=error,
    )


def select_best_search_result(
    records: list[object],
    title: str,
    artist: str,
    album: str,
    duration: int,
) -> dict[str, object] | None:
    normalized_title = title.strip().lower()
    normalized_artist = artist.strip().lower()
    normalized_album = album.strip().lower()

    def score(record_obj: object) -> int:
        if not isinstance(record_obj, dict):
            return -9999

        track_name = str(record_obj.get("trackName") or "").strip().lower()
        artist_name = str(record_obj.get("artistName") or "").strip().lower()
        album_name = str(record_obj.get("albumName") or "").strip().lower()

        total = 0
        if normalized_title and track_name:
            if track_name == normalized_title:
                total += 50
            elif normalized_title in track_name or track_name in normalized_title:
                total += 25

        if normalized_artist and artist_name:
            if artist_name == normalized_artist:
                total += 45
            elif normalized_artist in artist_name or artist_name in normalized_artist:
                total += 20

        if normalized_album and album_name:
            if album_name == normalized_album:
                total += 20
            elif normalized_album in album_name or album_name in normalized_album:
                total += 10

        try:
            record_duration = int(record_obj.get("duration") or 0)
        except (TypeError, ValueError):
            record_duration = 0

        if duration > 0 and record_duration > 0:
            delta = abs(record_duration - duration)
            if delta <= 2:
                total += 35
            elif delta <= 10:
                total += max(0, 20 - delta)

        return total

    ranked = sorted(records, key=score, reverse=True)
    for record in ranked:
        if isinstance(record, dict):
            return record
    return None


class LyricsLookupClient:
    """Rate-limited LRCLIB client with an in-session cache."""

    def __init__(self, is_cancelled=None):
        self._is_cancelled = is_cancelled or (lambda: False)
        self._ssl_context = _ssl_context()
        self._cache: dict[tuple[str, str, str, int], tuple[str, str, str]] = {}
        self._lock = threading.Lock()
        self._rate_limit_until = 0.0
        self._rate_limit_backoff = INITIAL_BACKOFF

    def set_cancel_check(self, is_cancelled) -> None:
        self._is_cancelled = is_cancelled or (lambda: False)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise LookupCancelled()

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _request_json(
        self,
        endpoint: str,
        params: dict[str, str | int],
    ) -> tuple[object | None, str | None, int]:
        self._check_cancelled()

        now = time.monotonic()
        with self._lock:
            wait_until = self._rate_limit_until
        if now < wait_until:
            self._sleep(wait_until - now)

        filtered: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            filtered[key] = text

        url = f"{LRCLIB_BASE}{endpoint}"
        query = urllib.parse.urlencode(filtered)
        if query:
            url = f"{url}?{query}"

        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT, context=self._ssl_context
            ) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, None, 404
            if exc.code == 429:
                with self._lock:
                    self._rate_limit_until = time.monotonic() + self._rate_limit_backoff
                    self._rate_limit_backoff = min(self._rate_limit_backoff * 1.5, 60.0)
                return None, "RateLimited", 429
            try:
                error_payload = exc.read().decode("utf-8", "ignore").strip()
            except Exception:
                error_payload = ""
            return None, (error_payload or str(exc)), int(exc.code)
        except LookupCancelled:
            raise
        except Exception as exc:
            return None, str(exc), 0

        if len(payload) > MAX_RESPONSE_BYTES:
            return None, "Response too large", status

        try:
            data = json.loads(payload.decode("utf-8", "ignore"))
            with self._lock:
                self._rate_limit_backoff = INITIAL_BACKOFF
        except Exception as exc:
            return None, f"Invalid API response: {exc}", status

        return data, None, status

    def lookup(
        self,
        title: str,
        artist: str,
        album: str,
        duration: int,
    ) -> tuple[str, str, str]:
        """Return (status, source, lyrics_text) for one track."""
        if not title:
            return "Missing metadata", "Track title is missing", ""
        if not artist:
            return "Missing metadata", "Artist is missing", ""

        key = (title, artist, album, duration)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._lookup_uncached(title, artist, album, duration)
        with self._lock:
            self._cache[key] = result
        return result

    def _lookup_uncached(
        self,
        title: str,
        artist: str,
        album: str,
        duration: int,
    ) -> tuple[str, str, str]:
        if album and duration > 0:
            signature_params: dict[str, str | int] = {
                "track_name": title,
                "artist_name": artist,
                "album_name": album,
                "duration": duration,
            }
            for endpoint, source_label in (
                ("/api/get-cached", "get-cached"),
                ("/api/get", "get"),
            ):
                response, error, status = self._request_with_retry(endpoint, signature_params)
                if error and status != 404:
                    return "Error", f"{source_label}: {error}", ""
                if not isinstance(response, dict):
                    continue

                lyrics_text = lyrics_text_from_record(response)
                if lyrics_text:
                    return "Found", source_label, lyrics_text
                if bool(response.get("instrumental")):
                    return "Instrumental", source_label, ""

        search_params: dict[str, str | int] = {
            "track_name": title,
            "artist_name": artist,
        }
        if album:
            search_params["album_name"] = album

        search_response, search_error, search_status = self._request_with_retry(
            "/api/search", search_params
        )
        if search_error and search_status != 404:
            return "Error", f"search: {search_error}", ""

        if isinstance(search_response, list) and search_response:
            record = select_best_search_result(
                search_response,
                title=title,
                artist=artist,
                album=album,
                duration=duration,
            )
            if isinstance(record, dict):
                lyrics_text = lyrics_text_from_record(record)
                if lyrics_text:
                    return "Found", "search", lyrics_text
                if bool(record.get("instrumental")):
                    return "Instrumental", "search", ""

        return "Not found", "No match in LRCLIB", ""

    def _request_with_retry(
        self,
        endpoint: str,
        params: dict[str, str | int],
    ) -> tuple[object | None, str | None, int]:
        last_error = None
        last_status = 0
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES):
            self._check_cancelled()
            response, error, status = self._request_json(endpoint, params)
            if status == 429:
                last_error, last_status = error, status
                self._sleep(backoff)
                backoff = min(backoff * 1.5, 60.0)
                continue
            if status == 0 and attempt < MAX_RETRIES - 1:
                last_error, last_status = error, status
                self._sleep(1.0)
                continue
            return response, error, status

        return None, last_error, last_status
