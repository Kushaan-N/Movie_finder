"""Build the seat-check bookmarklet from the shared extractor.

The bookmarklet is the delivery mechanism for browser-assisted seat checking: the
user opens a showtime's seat page themselves, clicks the bookmarklet, and the grid
comes back to the app. That reaches all three chains, because neither Regal's
CAPTCHA nor Cinemark's robots policy applies to a person browsing their own
session.

Two constraints, both measured rather than assumed:

* **The chain page cannot talk to localhost.** Chrome's Private Network Access
  blocks a fetch from an https page to 127.0.0.1 — verified against AMC's seat
  page, where the request hung until aborted even with permissive CORS and
  ``Access-Control-Allow-Private-Network``. So the result is handed over by
  *navigation* (a URL fragment the app reads) with a clipboard copy as fallback,
  never by an HTTP call from the page.

* **The extractor must be inlined.** Loading it from localhost as a script is
  blocked for the same reason, so the whole thing ships in the bookmarklet body.
  Comments are stripped to keep it manageable.

Generating it here, from ``seat_extract.js``, means the bookmarklet can never drift
from the extractor the tests exercise.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

_EXTRACTOR = Path(__file__).with_name("seat_extract.js")

# The fragment key the app looks for when the bookmarklet hands off a grid.
FRAGMENT_KEY = "seatcheck"


def _strip_comments(js: str) -> str:
    """Remove /* */ and // comments and collapse indentation.

    Deliberately conservative: it only strips a ``//`` comment when the line has
    no quote or slash before it, so URLs and regex literals in the source are left
    alone.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    out = []
    for line in js.split("\n"):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        idx = stripped.find("//")
        if idx > 0 and not re.search(r"""['"/]""", stripped[:idx]):
            stripped = stripped[:idx].rstrip()
        if stripped:
            out.append(stripped)
    return "\n".join(out)


@lru_cache
def extractor_source(minify: bool = True) -> str:
    src = _EXTRACTOR.read_text(encoding="utf-8").strip()
    return _strip_comments(src) if minify else src


def build_js(app_url: str, options_json: str = "{}") -> str:
    """The bookmarklet body: extract, then hand the grid to ``app_url``."""
    extractor = extractor_source()
    return (
        "(function(){try{"
        f"var EX={extractor};"
        f"var r=EX({options_json});"
        "if(!r.ok){alert('showtime-finder: '+(r.reason||'no seat map found here'));return;}"
        # Compact, screen-first: O free, . taken, _ gap.
        "var rows=r.rows.map(function(row){return row.map(function(c){"
        "return c.gap?'_':(c.available?'O':'.');}).join('');});"
        "var p={rows:rows,strategy:r.strategy,source_url:location.href,"
        "title:document.title,stats:r.stats};"
        "var enc=encodeURIComponent(JSON.stringify(p));"
        # Clipboard first so the grid survives even if the app isn't running.
        "try{navigator.clipboard&&navigator.clipboard.writeText(JSON.stringify(p));}catch(e){}"
        f"var u={app_url!r}.replace(/#.*$/,'')+'#{FRAGMENT_KEY}='+enc;"
        "var w=window.open(u,'_blank');"
        "if(!w){alert('showtime-finder: grid copied to clipboard \\u2014 paste it into the app.');}"
        "}catch(err){alert('showtime-finder failed: '+err.message);}})()"
    )


def build_href(app_url: str, options_json: str = "{}") -> str:
    """The full ``javascript:`` URL to drag onto a bookmarks bar."""
    return "javascript:" + quote(build_js(app_url, options_json), safe="")
