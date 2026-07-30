#!/usr/bin/env python
"""End-to-end check of the browser-assisted seat flow, against live pages.

Drives the whole chain the way a person does, with nothing stubbed except the
final ``window.open`` (captured so the run stays headless):

  1. search in the app                    -> showtimes, persisted to localStorage
  2. open a real chain seat page          -> the map a human would look at
  3. run the real bookmarklet             -> grid handed over in a URL fragment
  4. follow the handoff into a NEW tab    -> app restores results, posts the grid
  5. apply the verdict to a showtime      -> that card's badge changes

This is deliberately NOT part of `pytest`: it needs both servers running and it
touches a live chain website, whose seat map changes as tickets sell. Use it to
re-verify after changing the extractor, the bookmarklet, or the handoff.

    cd backend
    .venv/bin/python -m uvicorn app.main:app --port 8000 &
    (cd ../frontend && npm run dev &)
    .venv/bin/python scripts/e2e_browser_seatcheck.py \
        --seat-url https://www.amctheatres.com/showtimes/<id>/seats

Find a seat URL by opening a showtime on the chain's site. Pass --movie to match
whatever is actually playing.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scrape.bookmarklet import build_js  # noqa: E402

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 showtime-finder/1.0"
)

SET_TITLE_JS = """(title) => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    const el = [...document.querySelectorAll('input')].find(i => /Dune/i.test(i.placeholder || ''));
    if (!el) throw new Error('movie title input not found');
    set.call(el, title);
    el.dispatchEvent(new Event('input', { bubbles: true }));
}"""

READ_APP_JS = """() => {
    const t = document.body.innerText;
    const g = (re) => (t.match(re) || [])[0] || null;
    const sel = document.querySelector('#apply-to');
    return {
      results: g(/\\d+ showtimes ·/),
      verdict: g(/No \\d+-block|\\d+ together|Check manually/),
      stats: g(/\\d+ of \\d+ seats free[^\\n]*/),
      applyOptions: sel ? sel.options.length - 1 : 0,
      // The unverified-seats badge. Keep in sync with SeatBadge in Results.jsx.
      manualBadges: (t.match(/Seats unknown/g) || []).length,
    };
}"""

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


async def run(args) -> None:
    from playwright.async_api import async_playwright

    bookmarklet = build_js(args.app)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed)
        ctx = await browser.new_context(user_agent=UA)
        try:
            print("\n1. search in the app")
            app1 = await ctx.new_page()
            await app1.goto(args.app, wait_until="load")
            await app1.wait_for_timeout(1500)
            await app1.evaluate(SET_TITLE_JS, args.movie)
            await app1.wait_for_timeout(300)
            await app1.click("text=Search showtimes")
            await app1.wait_for_timeout(args.search_wait * 1000)
            found = await app1.evaluate(
                "() => (document.body.innerText.match(/(\\d+) showtimes/) || [])[1]"
            )
            check("search returned showtimes", bool(found and int(found) > 0), f"{found} showtimes")

            print("\n2. open the chain's real seat page")
            seat = await ctx.new_page()
            await seat.goto(args.seat_url, wait_until="domcontentloaded", timeout=40000)
            await seat.wait_for_timeout(args.seat_wait * 1000)
            title = await seat.title()
            check("seat page loaded", bool(title), title)

            print("\n3. run the bookmarklet")
            await seat.evaluate("() => { window.__u = null; window.open = (u) => { window.__u = u; return {}; }; }")
            await seat.evaluate(bookmarklet)
            handoff = await seat.evaluate("() => window.__u")
            alerted = await seat.evaluate("() => window.__alerted || null")
            check("bookmarklet produced a handoff", bool(handoff),
                  f"{len(handoff)} chars" if handoff else f"alert: {alerted}")
            if not handoff:
                return

            print("\n4. follow the handoff into a new tab")
            app2 = await ctx.new_page()
            await app2.goto(handoff, wait_until="load")
            await app2.wait_for_timeout(3000)
            state = await app2.evaluate(READ_APP_JS)
            check("previous results restored", bool(state["results"]), str(state["results"]))
            check("verdict rendered", bool(state["verdict"]), str(state["verdict"]))
            check("grid stats rendered", bool(state["stats"]), str(state["stats"]))
            check("fragment cleared from the URL",
                  await app2.evaluate("() => !location.hash"))
            check("showtimes offered to attach to", state["applyOptions"] > 0,
                  f"{state['applyOptions']} options")
            if not state["applyOptions"]:
                return

            print("\n5. apply the verdict to a showtime")
            before = state["manualBadges"]
            chosen = await app2.evaluate("""() => {
                const s = document.querySelector('#apply-to');
                s.value = s.options[1].value;
                s.dispatchEvent(new Event('change', { bubbles: true }));
                return s.options[1].text;
            }""")
            await app2.wait_for_timeout(400)
            await app2.click("button:has-text('Apply')")
            await app2.wait_for_timeout(900)
            after = await app2.evaluate(READ_APP_JS)
            check("that showtime's badge changed", after["manualBadges"] == before - 1,
                  f"{chosen} · check-manually {before} -> {after['manualBadges']}")
        finally:
            await browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", default="http://localhost:5173", help="the running frontend")
    ap.add_argument("--seat-url", required=True, help="a real showtime's seat-selection URL")
    ap.add_argument("--movie", default="The Odyssey", help="a movie currently in theaters")
    ap.add_argument("--seat-wait", type=int, default=8, help="seconds for the seat map to draw")
    ap.add_argument("--search-wait", type=int, default=14, help="seconds to allow for the search")
    ap.add_argument("--headed", action="store_true", help="watch it run")
    args = ap.parse_args()

    asyncio.run(run(args))
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("All steps passed: search -> seat page -> bookmarklet -> handoff -> applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
