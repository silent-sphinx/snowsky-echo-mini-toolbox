import io
import json
import unittest
import urllib.error
from unittest import mock

from src.utils import update_check
from src.utils.update_check import (
    RELEASES_PAGE_URL,
    check_for_update,
    is_newer,
    parse_version,
    update_from_release,
)


def fake_response(payload) -> io.BytesIO:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response = io.BytesIO(body)
    response.__enter__ = lambda self=response: self
    response.__exit__ = lambda *args: None
    return response


class VersionComparisonTests(unittest.TestCase):
    def test_parse_accepts_v_prefix(self):
        self.assertEqual(parse_version("v2.1.1"), (2, 1, 1))
        self.assertEqual(parse_version("2.1"), (2, 1))

    def test_parse_rejects_non_release_versions(self):
        for text in ("beta", "0.0.0-dev", "", "v1.2.x"):
            self.assertIsNone(parse_version(text), text)

    def test_numeric_not_lexical_ordering(self):
        self.assertTrue(is_newer("v2.10.0", "2.9.0"))
        self.assertFalse(is_newer("v2.1.1", "2.1.1"))
        self.assertFalse(is_newer("v2.0.3", "2.1.1"))

    def test_missing_trailing_parts_are_zero(self):
        self.assertFalse(is_newer("2.1", "2.1.0"))
        self.assertTrue(is_newer("2.1.1", "2.1"))

    def test_unparseable_is_never_newer(self):
        self.assertFalse(is_newer("nightly", "2.1.1"))
        self.assertFalse(is_newer("v9.0.0", "beta"))


class ReleasePayloadTests(unittest.TestCase):
    def test_newer_release_returns_info(self):
        info = update_from_release(
            {"tag_name": "v2.2.0", "html_url": "https://github.com/o/r/releases/tag/v2.2.0"},
            "2.1.1",
        )
        self.assertEqual(info.version, "2.2.0")
        self.assertEqual(info.url, "https://github.com/o/r/releases/tag/v2.2.0")

    def test_untrusted_url_falls_back_to_releases_page(self):
        info = update_from_release({"tag_name": "v2.2.0", "html_url": "file:///etc/passwd"}, "2.1.1")
        self.assertEqual(info.url, RELEASES_PAGE_URL)

    def test_same_or_malformed_payload_returns_none(self):
        self.assertIsNone(update_from_release({"tag_name": "v2.1.1"}, "2.1.1"))
        self.assertIsNone(update_from_release({"tag_name": 5}, "2.1.1"))
        self.assertIsNone(update_from_release(["nope"], "2.1.1"))


class CheckForUpdateTests(unittest.TestCase):
    def test_dev_build_skips_network(self):
        with mock.patch.object(update_check.urllib.request, "urlopen") as urlopen:
            self.assertIsNone(check_for_update("beta"))
        urlopen.assert_not_called()

    def test_returns_update(self):
        payload = {"tag_name": "v3.0.0", "html_url": "https://github.com/o/r/releases/tag/v3.0.0"}
        with mock.patch.object(update_check.urllib.request, "urlopen", return_value=fake_response(payload)):
            info = check_for_update("2.1.1")
        self.assertEqual(info.version, "3.0.0")

    def test_network_and_parse_failures_return_none(self):
        failures = (
            mock.Mock(side_effect=urllib.error.URLError("offline")),
            mock.Mock(side_effect=TimeoutError()),
            mock.Mock(return_value=fake_response(b"not json")),
        )
        for urlopen in failures:
            with mock.patch.object(update_check.urllib.request, "urlopen", urlopen):
                self.assertIsNone(check_for_update("2.1.1"))


if __name__ == "__main__":
    unittest.main()
