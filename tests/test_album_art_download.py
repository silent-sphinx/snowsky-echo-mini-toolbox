import json
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from src.threads.album_art_download import AlbumArtApplyWorker
from src.utils.album_art_download import AlbumArtLookupClient, ImagePrefetcher


def release(release_id: str, title: str = "Album") -> dict:
    return {"id": release_id, "title": title, "score": 100, "artist-credit": [{"name": "Artist"}]}


def cover_payload(release_id: str) -> bytes:
    image = {"front": True, "image": f"https://x/{release_id}.jpg", "thumbnails": {}}
    return json.dumps({"images": [image]}).encode()


class CoverLookupBatchingTests(unittest.TestCase):
    def search(self, releases, covered):
        client = AlbumArtLookupClient()
        requested = []

        def fake_request(url, *, throttle, max_bytes):
            if throttle:
                return json.dumps({"releases": releases}).encode()
            release_id = url.rsplit("/", 1)[-1]
            requested.append(release_id)
            if release_id in covered:
                return cover_payload(release_id)
            raise_not_found(url)

        with mock.patch.object(client, "_request", side_effect=fake_request):
            result = client.search_album("Artist", "Album")
        return result, requested

    def test_stops_after_first_batch_when_cover_found(self):
        releases = [release(f"r{i}") for i in range(6)]
        result, requested = self.search(releases, covered={"r0"})
        self.assertTrue(result.has_candidates)
        self.assertEqual(len(requested), 3)

    def test_falls_back_to_remaining_releases(self):
        releases = [release(f"r{i}") for i in range(6)]
        result, requested = self.search(releases, covered={"r5"})
        self.assertEqual([c.release_id for c in result.candidates], ["r5"])
        self.assertEqual(len(requested), 6)


def raise_not_found(url):
    import urllib.error

    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


class ApplyWorkerTests(unittest.TestCase):
    def make_worker(self, candidates, tmp):
        client = mock.Mock()
        client.fetch_image.return_value = b"raw"
        worker = AlbumArtApplyWorker(Path(tmp), candidates, 90, False, None, client=client)
        return worker, client

    def run_worker(self, worker):
        done = []
        worker.finished.connect(done.append)
        with mock.patch("src.threads.album_art_download.to_non_progressive_jpeg", return_value=b"jpg"):
            worker.run()
        return done[0]

    def test_writes_every_file_and_downloads_once_per_album(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            candidates = [
                {"album": f"A{i}", "artist": "X", "image_url": f"u{i}",
                 "filepaths": [f"{tmp}/{i}-{n}.mp3" for n in range(5)]}
                for i in range(3)
            ]
            worker, client = self.make_worker(candidates, tmp)
            with mock.patch.object(worker, "_apply_to_file", return_value=""):
                payload = self.run_worker(worker)
        self.assertEqual(payload["written"], 15)
        self.assertEqual(payload["failed"], 0)
        self.assertEqual(client.fetch_image.call_count, 3)

    def test_download_failure_fails_only_that_album(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            candidates = [
                {"album": "Bad", "artist": "X", "image_url": "bad", "filepaths": [f"{tmp}/a.mp3"]},
                {"album": "Good", "artist": "X", "image_url": "good", "filepaths": [f"{tmp}/b.mp3"]},
            ]
            worker, client = self.make_worker(candidates, tmp)
            client.fetch_image.side_effect = lambda url: (_ for _ in ()).throw(OSError("boom")) if url == "bad" else b"raw"
            with mock.patch.object(worker, "_apply_to_file", return_value=""):
                payload = self.run_worker(worker)
        self.assertEqual(payload["written"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertIn("Bad: download failed", payload["failures"][0])


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(307)
            self.send_header("Location", "/img.png")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/missing":
            body = b"nope"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)


class _CountingServer(ThreadingHTTPServer):
    daemon_threads = True
    connections = 0

    def get_request(self):
        request = super().get_request()
        self.connections += 1
        return request


class PooledRequestTests(unittest.TestCase):
    def setUp(self):
        self.server = _CountingServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.client = AlbumArtLookupClient()

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()

    def test_reuses_one_connection_across_requests(self):
        for _ in range(4):
            self.assertEqual(self.client._request(f"{self.base}/img.png", throttle=False, max_bytes=1024), PNG)
        self.assertEqual(self.server.connections, 1)

    def test_follows_redirects(self):
        self.assertEqual(self.client._request(f"{self.base}/redirect", throttle=False, max_bytes=1024), PNG)

    def test_404_raises_http_error_and_keeps_connection(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.client._request(f"{self.base}/missing", throttle=False, max_bytes=1024)
        self.assertEqual(ctx.exception.code, 404)
        self.client._request(f"{self.base}/img.png", throttle=False, max_bytes=1024)
        self.assertEqual(self.server.connections, 1)

    def test_oversized_response_rejected(self):
        with self.assertRaises(ValueError):
            self.client._request(f"{self.base}/img.png", throttle=False, max_bytes=8)

    def test_recovers_from_server_closed_idle_connection(self):
        self.client._request(f"{self.base}/img.png", throttle=False, max_bytes=1024)
        for conns in self.client._pool._idle.values():
            for conn in conns:
                conn.sock.close()
        self.assertEqual(self.client._request(f"{self.base}/img.png", throttle=False, max_bytes=1024), PNG)


class ImagePrefetcherTests(unittest.TestCase):
    def test_fetches_each_unique_url_and_survives_failures(self):
        client = mock.Mock()
        client.fetch_image.side_effect = lambda url: (_ for _ in ()).throw(OSError()) if url == "bad" else b"x"
        prefetcher = ImagePrefetcher(client, ["a", "bad", "a", "", "b"])
        prefetcher._pool.shutdown(wait=True)
        self.assertEqual(sorted(c.args[0] for c in client.fetch_image.call_args_list), ["a", "b", "bad"])


if __name__ == "__main__":
    unittest.main()
