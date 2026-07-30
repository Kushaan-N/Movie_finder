"""A tiny local HTTP server that serves the seat-map fixtures.

Used by the real-browser E2E test so the *entire* pipeline runs for real
(Playwright render -> listing -> seat-URL resolution -> parse/extract -> enrich ->
seat check) with only the target host swapped from a chain's site to localhost.
Also serves a permissive robots.txt so the real robots check is exercised too.

Paths mirror the URL shapes the resolvers actually produce, so resolution is
tested rather than bypassed:
  * ``/cinemark-listing``      -> Cinemark theatre page (links to /TicketSeatMap/)
  * ``/TicketSeatMap/``        -> Cinemark seat map (dom strategy)
  * ``/amc-listing``           -> AMC showtimes page (links to /showtimes/<id>)
  * ``/showtimes/<id>/seats``  -> AMC SVG seat map (geometry strategy)
  * ``/regal-listing``         -> Regal theatre page (sold-out state only; its
                                  seat page is CAPTCHA-gated and unreachable)
  * ``/interactive-seats``     -> seat map whose state is only enabled/disabled
  * ``/misleading-seats``      -> every seat enabled; state is only in colour
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

_EXACT = {
    "/cinemark-listing": "cinemark_listing.html",
    "/TicketSeatMap/": "cinemark_seatmap.html",
    "/amc-listing": "amc_listing.html",
    "/regal-listing": "regal_listing.html",
    "/interactive-seats": "interactive_seatmap.html",
    "/misleading-seats": "misleading_seatmap.html",
}

# /showtimes/<numeric id>/seats -> AMC's SVG-based map.
_AMC_SEATS_RE = re.compile(r"^/showtimes/\d+/seats/?$")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence per-request logging
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nAllow: /\n", "text/plain")
            return
        name = _EXACT.get(path)
        if name is None and _AMC_SEATS_RE.match(path):
            name = "amc_svg_seatmap.html"
        if name:
            with open(os.path.join(FIXTURES, name), "rb") as f:
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
