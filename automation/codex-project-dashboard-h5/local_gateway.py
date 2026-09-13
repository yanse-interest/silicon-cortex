#!/usr/bin/env python3
"""Read-only LAN gateway for the local project dashboard H5."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
GENERATOR = ROOT.parent / "codex-project-dashboard" / "dashboard_h5_open_snapshot.py"
NO_STORE = "no-store, max-age=0"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
BACKEND_COMMAND_PREFIX = (
    "/opt/homebrew/bin/npm",
    "run",
    "start:app",
    "--",
    "--hostname",
    "127.0.0.1",
    "--port",
)


class FreshSnapshotCache:
    def __init__(self, *, generator: Path = GENERATOR, ttl: float = 3.0, timeout: float = 5.5):
        self.generator = generator
        self.ttl = ttl
        self.timeout = timeout
        self._lock = threading.Lock()
        self._payload: bytes | None = None
        self._stored_at = 0.0
        self._failed_at = 0.0

    def get(self) -> tuple[bytes, str]:
        now = time.monotonic()
        if self._payload is not None and now - self._stored_at <= self.ttl:
            return self._payload, "cached"
        if self._failed_at and now - self._failed_at <= self.ttl:
            raise RuntimeError("fresh snapshot unavailable")
        with self._lock:
            now = time.monotonic()
            if self._payload is not None and now - self._stored_at <= self.ttl:
                return self._payload, "cached"
            if self._failed_at and now - self._failed_at <= self.ttl:
                raise RuntimeError("fresh snapshot unavailable")
            try:
                completed = subprocess.run(
                    ["/opt/homebrew/bin/python3", str(self.generator), "--compact"],
                    cwd=self.generator.parent,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                if completed.returncode != 0:
                    raise RuntimeError("fresh snapshot unavailable")
                candidate = json.loads(completed.stdout)
                if candidate.get("schema_version") != 3:
                    raise RuntimeError("fresh snapshot unavailable")
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                self._failed_at = time.monotonic()
                raise
            candidate["freshness"] = {"mode": "fresh"}
            payload = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._payload = payload
            self._stored_at = time.monotonic()
            self._failed_at = 0.0
            return payload, "fresh"


class ReadOnlyGateway(BaseHTTPRequestHandler):
    server_version = "DashboardH5ReadOnly/1"
    protocol_version = "HTTP/1.1"

    @property
    def cache(self) -> FreshSnapshotCache:
        return self.server.snapshot_cache  # type: ignore[attr-defined]

    @property
    def backend(self) -> str:
        return self.server.backend  # type: ignore[attr-defined]

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", NO_STORE)
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def _json_error(self, status: int) -> None:
        body = b'{"ok":false,"error":"fresh_snapshot_unavailable"}'
        self._headers(status, "application/json; charset=utf-8", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fresh_snapshot(self) -> None:
        try:
            payload, mode = self.cache.get()
            if mode == "cached":
                candidate: dict[str, Any] = json.loads(payload)
                candidate["freshness"] = {"mode": "cached"}
                payload = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            self._json_error(503)
            return
        self._headers(200, "application/json; charset=utf-8", len(payload))
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _proxy(self) -> None:
        url = f"{self.backend}{self.path}"
        request = urllib.request.Request(url, method=self.command, headers={"Accept": self.headers.get("Accept", "*/*")})
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                body = response.read()
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() not in HOP_HEADERS and key.lower() not in {"content-length", "cache-control", "pragma", "expires"}:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", NO_STORE)
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self._headers(exc.code, exc.headers.get_content_type(), len(body))
            if self.command != "HEAD":
                self.wfile.write(body)
        except (OSError, urllib.error.URLError):
            body = b"H5 service unavailable"
            self._headers(503, "text/plain; charset=utf-8", len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/fresh-snapshot":
            self._fresh_snapshot()
        elif self.path.split("?", 1)[0].startswith("/api/"):
            self._json_error(404)
        else:
            self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _reject_write(self) -> None:
        body = b'{"ok":false,"error":"read_only"}'
        self._headers(405, "application/json; charset=utf-8", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    do_POST = _reject_write
    do_PUT = _reject_write
    do_PATCH = _reject_write
    do_DELETE = _reject_write
    do_OPTIONS = _reject_write

    def log_message(self, format: str, *args: object) -> None:
        return


def wait_for_backend(url: str, process: subprocess.Popen[bytes], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("H5 backend exited")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError("H5 backend unavailable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--backend-port", type=int, default=8793)
    args = parser.parse_args()
    backend = f"http://127.0.0.1:{args.backend_port}"
    process = subprocess.Popen(
        [*BACKEND_COMMAND_PREFIX, str(args.backend_port)],
        cwd=ROOT,
        env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    server: ThreadingHTTPServer | None = None
    try:
        wait_for_backend(backend, process)
        server = ThreadingHTTPServer((args.host, args.port), ReadOnlyGateway)
        server.daemon_threads = True
        server.backend = backend  # type: ignore[attr-defined]
        server.snapshot_cache = FreshSnapshotCache()  # type: ignore[attr-defined]
        # BaseServer.shutdown() must run from a different thread than
        # serve_forever(), including when launchd delivers SIGTERM.
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
        signal.signal(signal.SIGINT, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
        server.serve_forever(poll_interval=0.25)
        return 0
    finally:
        if server is not None:
            server.server_close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
