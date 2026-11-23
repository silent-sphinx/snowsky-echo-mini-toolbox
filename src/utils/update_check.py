"""Check GitHub for a newer release of the toolbox."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..constants import APP_VERSION
from .album_art_download import _ssl_context

logger = logging.getLogger(__name__)

LATEST_RELEASE_API_URL = (
    "https://api.github.com/repos/silent-sphinx/snowsky-echo-mini-toolbox/releases/latest"
)
RELEASES_PAGE_URL = "https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/releases/latest"
REQUEST_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024

_VERSION_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str


def parse_version(text: str) -> tuple[int, ...] | None:
    """Parse "v1.2.3" or "1.2.3" into a comparable tuple; None if not a release version."""
    match = _VERSION_PATTERN.match(str(text).strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    candidate_version = parse_version(candidate)
    current_version = parse_version(current)
    if candidate_version is None or current_version is None:
        return False
    length = max(len(candidate_version), len(current_version))
    pad = lambda parts: parts + (0,) * (length - len(parts))
    return pad(candidate_version) > pad(current_version)


def update_from_release(release: object, current_version: str) -> UpdateInfo | None:
    """Return update details if a GitHub release payload is newer than current_version."""
    if not isinstance(release, dict):
        return None
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not is_newer(tag, current_version):
        return None
    url = release.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        url = RELEASES_PAGE_URL
    return UpdateInfo(version=tag.lstrip("v"), url=url)


def check_for_update(current_version: str = APP_VERSION) -> UpdateInfo | None:
    """Return the newer release, or None if up to date or the check could not complete.

    Dev builds (non-numeric versions) skip the network call entirely.
    """
    if parse_version(current_version) is None:
        return None
    request = urllib.request.Request(
        LATEST_RELEASE_API_URL,
        headers={
            "User-Agent": f"SnowskyEchoMiniToolbox/{current_version}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT, context=_ssl_context()
        ) as response:
            release = json.loads(response.read(MAX_RESPONSE_BYTES))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.info("Update check failed: %s", exc)
        return None
    return update_from_release(release, current_version)
