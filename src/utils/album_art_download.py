"""MusicBrainz and Cover Art Archive lookup for album covers.

Kept free of Qt imports: only the apply stage needs Qt to re-encode the image.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ..constants import APP_VERSION
from .tag_normalization import normalize_key

logger = logging.getLogger(__name__)

MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/release/"
COVERART_URL = "https://coverartarchive.org/release/{mbid}"

USER_AGENT = (
    f"SnowskyEchoMiniToolbox/{APP_VERSION} "
    "( https://github.com/Snowsky-Echo-Mini-Toolbox )"
)

# MusicBrainz asks for no more than one request per second.
MUSICBRAINZ_MIN_INTERVAL = 1.0
REQUEST_TIMEOUT = 15.0
MAX_RELEASES_INSPECTED = 6
# Cover Art Archive has no published rate limit but is slow per request.
MAX_PARALLEL_COVER_REQUESTS = 8
MAX_SEARCH_RESULTS = 25
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


class LookupCancelled(Exception):
    """Raised when the caller cancels an in-flight lookup."""


@dataclass
class CoverCandidate:
    """One front cover offered for an album."""
    release_id: str
    release_title: str
    artist_credit: str
    date: str
    country: str
    status: str
    release_group_type: str
    track_count: int
    image_url: str
    thumbnail_url: str
    score: int = 0

    @property
    def year(self) -> str:
        return self.date[:4] if len(self.date) >= 4 else ""

    @property
    def display_label(self) -> str:
        bits = [self.release_title or "Untitled release"]
        detail = ", ".join(b for b in (self.year, self.country, self.status) if b)
        if detail:
            bits.append(f"({detail})")
        return " ".join(bits)


@dataclass
class LookupResult:
    """Outcome of searching for one album's cover."""
    candidates: list[CoverCandidate] = field(default_factory=list)
    error: str = ""
    query: str = ""

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


def escape_lucene(value: str) -> str:
    """Escape characters that would otherwise break a MusicBrainz query."""
    return _LUCENE_SPECIAL.sub(r"\\\1", value)


def build_query(artist: str, album: str) -> str:
    """Build a MusicBrainz release query from cleaned tag text."""
    parts = []
    if album:
        parts.append(f'release:"{escape_lucene(album)}"')
    if artist:
        parts.append(f'artist:"{escape_lucene(artist)}"')
    return " AND ".join(parts)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class AlbumArtLookupClient:
    """Rate-limited MusicBrainz + Cover Art Archive client with session cache."""

    def __init__(self, is_cancelled=None):
        self._is_cancelled = is_cancelled or (lambda: False)
        self._ssl_context = _ssl_context()
        self._last_musicbrainz_call = 0.0
        self._throttle_lock = threading.Lock()
        self._cover_slots = threading.BoundedSemaphore(MAX_PARALLEL_COVER_REQUESTS)
        self._cache: dict[str, LookupResult] = {}
        self._image_cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    # ── cancellation ────────────────────────────────────────────

    def set_cancel_check(self, is_cancelled) -> None:
        """Point the client at the current owner's cancellation flag.

        The client outlives individual workers so its session cache survives
        the search, review and apply stages of one download.
        """
        self._is_cancelled = is_cancelled or (lambda: False)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise LookupCancelled()

    def _sleep(self, seconds: float) -> None:
        """Sleep in short slices so cancellation stays responsive."""
        deadline = time.monotonic() + seconds
        while True:
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    # ── HTTP ────────────────────────────────────────────────────

    def _request(self, url: str, *, throttle: bool, max_bytes: int) -> bytes:
        attempt = 0
        backoff = INITIAL_BACKOFF

        while True:
            self._check_cancelled()

            if throttle:
                # Space request starts across every thread sharing this client.
                with self._throttle_lock:
                    elapsed = time.monotonic() - self._last_musicbrainz_call
                    if elapsed < MUSICBRAINZ_MIN_INTERVAL:
                        self._sleep(MUSICBRAINZ_MIN_INTERVAL - elapsed)
                    self._last_musicbrainz_call = time.monotonic()

            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(
                    request, timeout=REQUEST_TIMEOUT, context=self._ssl_context
                ) as response:
                    data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"response exceeds {max_bytes} bytes")
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise
                retryable = exc.code in (429, 500, 502, 503, 504)
                if not retryable or attempt >= MAX_RETRIES - 1:
                    raise
                wait = backoff
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except (TypeError, ValueError):
                        pass
                self._sleep(wait)
                backoff *= 2
                attempt += 1
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt >= MAX_RETRIES - 1:
                    raise
                self._sleep(backoff)
                backoff *= 2
                attempt += 1

    # ── search ──────────────────────────────────────────────────

    def search_album(
        self,
        artist: str,
        album: str,
        *,
        year: str = "",
        raw_query: str = "",
        on_status=None,
    ) -> LookupResult:
        """Find front covers for one album. Successful results are cached.

        Failed lookups are not cached so a retry can recover from transient
        network or server errors. ``on_status`` receives step descriptions.
        """
        query = raw_query.strip() or build_query(artist, album)
        if not query:
            return LookupResult(error="No artist or album tag to search with")

        cache_key = f"{query}|{year}"
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._search_uncached(artist, album, year, query, on_status or (lambda _: None))
        if result.has_candidates:
            with self._lock:
                self._cache[cache_key] = result
        return result

    def _search_uncached(
        self, artist: str, album: str, year: str, query: str, on_status
    ) -> LookupResult:
        on_status("Querying MusicBrainz")
        url = (
            f"{MUSICBRAINZ_URL}?query={urllib.parse.quote(query)}"
            f"&limit={MAX_SEARCH_RESULTS}&fmt=json"
        )

        try:
            payload = json.loads(self._request(url, throttle=True, max_bytes=4 * 1024 * 1024))
        except LookupCancelled:
            raise
        except urllib.error.HTTPError as exc:
            return LookupResult(error=f"MusicBrainz returned HTTP {exc.code}", query=query)
        except json.JSONDecodeError:
            return LookupResult(error="MusicBrainz returned malformed data", query=query)
        except Exception as exc:
            return LookupResult(error=f"MusicBrainz request failed: {exc}", query=query)

        releases = payload.get("releases") or []
        if not releases:
            return LookupResult(error="No matching release found", query=query)

        ranked = sorted(
            releases,
            key=lambda rel: self._rank(rel, artist, album, year),
            reverse=True,
        )

        inspected = [rel for rel in ranked[:MAX_RELEASES_INSPECTED] if rel.get("id")]
        images_by_release: dict[str, list[dict]] = {}
        last_error = ""

        on_status(f"Checking cover art 0/{len(inspected)}")
        pool = ThreadPoolExecutor(max_workers=max(1, len(inspected)))
        try:
            futures = {
                pool.submit(self._front_images, rel["id"]): rel["id"] for rel in inspected
            }
            for done, future in enumerate(as_completed(futures), start=1):
                try:
                    images_by_release[futures[future]] = future.result()
                except LookupCancelled:
                    raise
                except Exception as exc:
                    last_error = str(exc)
                on_status(f"Checking cover art {done}/{len(inspected)}")
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        candidates: list[CoverCandidate] = []
        seen_images: set[str] = set()

        # Rebuild in rank order; requests above finish in any order.
        for release in inspected:
            release_id = release["id"]
            images = images_by_release.get(release_id) or []
            for image in images:
                original_url = image.get("image") or ""
                thumbnails = image.get("thumbnails") or {}
                thumbnail_url = (
                    thumbnails.get("250") or thumbnails.get("small") or original_url
                )
                if not original_url or original_url in seen_images:
                    continue
                seen_images.add(original_url)
                # Embedded art is capped well below 1200px, so the full-size
                # original (often tens of MB) is only a fallback.
                image_url = thumbnails.get("1200") or original_url
                candidates.append(
                    CoverCandidate(
                        release_id=release_id,
                        release_title=release.get("title") or "",
                        artist_credit=_artist_credit(release),
                        date=release.get("date") or "",
                        country=release.get("country") or "",
                        status=release.get("status") or "",
                        release_group_type=(release.get("release-group") or {}).get(
                            "primary-type", ""
                        )
                        or "",
                        track_count=int(release.get("track-count") or 0),
                        image_url=image_url,
                        thumbnail_url=thumbnail_url,
                        score=int(release.get("score") or 0),
                    )
                )
                break  # one front cover per release is enough

        if not candidates:
            message = "No cover art available for the matching releases"
            if last_error:
                message = f"{message} ({last_error})"
            return LookupResult(error=message, query=query)

        return LookupResult(candidates=candidates, query=query)

    def _front_images(self, release_id: str) -> list[dict]:
        url = COVERART_URL.format(mbid=release_id)
        try:
            with self._cover_slots:
                raw = self._request(url, throttle=False, max_bytes=2 * 1024 * 1024)
            payload = json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
        except json.JSONDecodeError:
            return []

        return [img for img in (payload.get("images") or []) if img.get("front")]

    @staticmethod
    def _rank(release: dict, artist: str, album: str, year: str) -> tuple:
        """Prefer official, exactly-matching, era-appropriate releases."""
        title_match = normalize_key(release.get("title")) == normalize_key(album)
        artist_match = (
            normalize_key(_artist_credit(release)) == normalize_key(artist)
            if artist
            else False
        )
        status = (release.get("status") or "").casefold()
        primary_type = (
            (release.get("release-group") or {}).get("primary-type") or ""
        ).casefold()

        year_bonus = 0
        release_year = (release.get("date") or "")[:4]
        if year and release_year:
            try:
                delta = abs(int(year) - int(release_year))
                year_bonus = 2 if delta == 0 else (1 if delta <= 1 else 0)
            except ValueError:
                year_bonus = 0

        return (
            int(title_match),
            int(artist_match),
            int(status == "official"),
            int(primary_type == "album"),
            year_bonus,
            int(release.get("score") or 0),
        )

    # ── image download ──────────────────────────────────────────

    def fetch_image(self, url: str) -> bytes:
        """Download one cover image, cached per session by URL."""
        with self._lock:
            cached = self._image_cache.get(url)
        if cached is not None:
            return cached

        data = self._request(url, throttle=False, max_bytes=MAX_IMAGE_BYTES)
        if not _looks_like_image(data):
            raise ValueError("downloaded file is not a recognized image")

        with self._lock:
            self._image_cache[url] = data
        return data


def _artist_credit(release: dict) -> str:
    credits = release.get("artist-credit") or []
    parts: list[str] = []
    for entry in credits:
        if isinstance(entry, str):
            parts.append(entry)
            continue
        name = entry.get("name") or (entry.get("artist") or {}).get("name") or ""
        parts.append(name)
        joinphrase = entry.get("joinphrase") or ""
        if joinphrase:
            parts.append(joinphrase)
    return "".join(parts).strip()


def _looks_like_image(data: bytes) -> bool:
    return bool(data) and (
        data.startswith(b"\xFF\xD8")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or data[:6] in (b"GIF87a", b"GIF89a")
        or data.startswith(b"BM")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )
