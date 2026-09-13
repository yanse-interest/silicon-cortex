from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_gateway import BACKEND_COMMAND_PREFIX, FreshSnapshotCache, ReadOnlyGateway


class FakeCompleted:
    returncode = 0
    stdout = b'{"schema_version":3,"generated_at":"2026-08-23T16:00:00+08:00","daily_updated_through":"2026-08-22"}'


class GatewayTests(unittest.TestCase):
    def test_backend_is_bound_to_loopback(self):
        self.assertEqual(
            BACKEND_COMMAND_PREFIX,
            (
                "/opt/homebrew/bin/npm",
                "run",
                "start:app",
                "--",
                "--hostname",
                "127.0.0.1",
                "--port",
            ),
        )

    @patch("local_gateway.subprocess.run", return_value=FakeCompleted())
    def test_snapshot_generation_is_cached_and_concurrent(self, run):
        cache = FreshSnapshotCache(generator=Path("generator.py"), ttl=10, timeout=1)
        rows = []
        threads = [threading.Thread(target=lambda: rows.append(cache.get())) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(json.loads(payload)["schema_version"] == 3 for payload, _ in rows))
        self.assertEqual(sum(mode == "fresh" for _, mode in rows), 1)
        self.assertEqual(sum(mode == "cached" for _, mode in rows), 7)

    def test_only_get_and_head_are_read_methods(self):
        self.assertTrue(callable(ReadOnlyGateway.do_GET))
        self.assertTrue(callable(ReadOnlyGateway.do_HEAD))
        for method in ("do_POST", "do_PUT", "do_PATCH", "do_DELETE", "do_OPTIONS"):
            self.assertIs(getattr(ReadOnlyGateway, method), ReadOnlyGateway._reject_write)

    @patch("local_gateway.subprocess.run", side_effect=TimeoutError())
    def test_generation_failure_does_not_return_stale_dynamic_payload(self, _run):
        cache = FreshSnapshotCache(generator=Path("generator.py"), ttl=0, timeout=0.01)
        with self.assertRaises(TimeoutError):
            cache.get()

    @patch("local_gateway.subprocess.run", side_effect=TimeoutError())
    def test_concurrent_generation_failure_is_cooled_down(self, run):
        cache = FreshSnapshotCache(generator=Path("generator.py"), ttl=10, timeout=0.01)
        failures = []
        threads = [threading.Thread(target=lambda: self._record_failure(cache, failures)) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(failures), 8)

    @staticmethod
    def _record_failure(cache, failures):
        try:
            cache.get()
        except (OSError, RuntimeError):
            failures.append(True)

    @patch("local_gateway.subprocess.run", side_effect=[FakeCompleted(), TimeoutError()])
    def test_failure_never_overwrites_last_good_cache(self, _run):
        cache = FreshSnapshotCache(generator=Path("generator.py"), ttl=0, timeout=0.01)
        good_payload, _ = cache.get()
        with self.assertRaises(TimeoutError):
            cache.get()
        self.assertEqual(cache._payload, good_payload)

    @patch("local_gateway.subprocess.run", return_value=FakeCompleted())
    def test_http_surface_is_no_store_private_and_read_only(self, _run):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReadOnlyGateway)
        server.snapshot_cache = FreshSnapshotCache(generator=Path("generator.py"), ttl=10, timeout=1)
        server.backend = "http://127.0.0.1:1"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(f"{base}/api/fresh-snapshot", timeout=2) as response:
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertEqual(payload["freshness"], {"mode": "fresh"})
                serialized = json.dumps(payload)
                for marker in ("access_token", "case_id", "thread_id", "/Users/", "chatgpt.com/c/"):
                    self.assertNotIn(marker, serialized)
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                request = urllib.request.Request(f"{base}/api/fresh-snapshot", method=method, data=b"{}")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=2)
                try:
                    self.assertEqual(raised.exception.code, 405)
                    self.assertEqual(raised.exception.headers["Cache-Control"], "no-store, max-age=0")
                    self.assertEqual(json.loads(raised.exception.read()), {"ok": False, "error": "read_only"})
                finally:
                    raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
