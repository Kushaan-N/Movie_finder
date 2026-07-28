"""A tiny local HTTP server that serves the seat-map fixtures.

Used by the real-browser E2E test so the *entire* pipeline runs for real
(Playwright render -> page.content() -> parse -> enrich -> seat check) with only
the target host swapped from a chain's site to localhost. Also serves a
permissive robots.txt so the real robots check is exercised too.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

_PAGES = {
    "/amc": "amc_seatmap.html",
    "/regal": "regal_seatmap.html",
    "/cinemark": "cinemark_seatmap.html",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence per-request logging
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self._send(200, body, "text/plain")
            return
        if path in _PAGES:
            with open(os.path.join(FIXTURES, _PAGES[path]), "rb") as f:
                self._send(200, f.read(), "text/html")
            return
        self._send(404, b"not found", "text/plain")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def serve_fixtures():
    """Yield a base URL like http://127.0.0.1:<port> serving the fixtures."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
