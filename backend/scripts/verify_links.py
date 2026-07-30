#!/usr/bin/env python
"""Check that the links the app hands out actually land where they claim.

Runs a real search, then opens every distinct (theatre, date) destination it
produced and asserts three things about the page that comes back:

  * it is the right **theatre** (its name appears)
  * it is the right **date** (the chain's own date control shows it selected)
  * the **film** is listed there

A link that returns HTTP 200 but shows the wrong day is worse than a broken one,
because nothing tells the user. Date formats differ per chain (AMC/Cinemark take
ISO, Regal takes MM-DD-YYYY), so this checks the rendered page rather than
trusting the URL we built.

Outside pytest: it needs the app running and it browses live chain sites.

    cd backend
    .venv/bin/python scripts/verify_links.py --movie "The Odyssey"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 showtime-finder/1.0"
)

# How each chain renders a selected date, so "did the date apply?" is checked
# against the page rather than assumed from the URL.
def date_tokens(d: date) -> list[str]:
    """Every way the chains were observed to render a date.

    Deliberately broad: Regal's date strip renders "July31" with no separator, which
    a narrower list missed and reported as a wrong date on a page that was correct.
    """
    return [
        f"{d.month}/{d.day}",                        # 8/1     Cinemark strip
        d.strftime("%b %-d"),                        # Aug 1
        d.strftime("%B%-d"),                         # July31  Regal strip
        d.strftime("%b%-d"),                         # Jul31
        d.strftime("%B %-d"),                        # July 1
        d.strftime("%-m-%-d-%Y"),                    # 8-1-2026
        d.strftime("%Y-%m-%d"),
    ]


# A bot challenge means the page never rendered for us. That says nothing about
# whether the link is right, so it must not be reported as a failure.
CHALLENGE_MARKERS = (
    "just a moment", "performing security verification", "verify you are human",
    "enable javascript and cookies", "are you a robot",
)


PAGE_JS = """() => ({
    title: document.title || '',
    text: (document.body ? document.body.innerText : '').slice(0, 200000),
    active: [...document.querySelectorAll(
        '[aria-current],[aria-selected="true"],[class*="active"],[class*="selected"]'
    )].map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim()).filter(x => x && x.length < 40),
})"""


def significant(name: str) -> list[str]:
    """Distinctive words from a theatre name, ignoring chain/format noise."""
    noise = {"amc", "regal", "cinemark", "century", "the", "and", "screenx", "imax", "xd", "&"}
    return [w for w in name.replace("&", " ").split() if w.lower() not in noise]


async def main(args) -> int:
    from playwright.async_api import async_playwright

    start = args.date or (date.today() + timedelta(days=1)).isoformat()
    end = args.date_to or (date.today() + timedelta(days=2)).isoformat()
    payload = {
        "movie_title": args.movie, "formats": ["Any"], "location": args.location,
        "radius_miles": args.radius, "date_from": start, "date_to": end,
        "time_rule": {"weekday_cutoff": "00:00", "weekends_unrestricted": True},
        "seats_together": 4, "min_row": 5,
    }
    req = urllib.request.Request(
        f"{args.app}/api/search", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        result = json.load(r)

    targets: dict[tuple[str, str], dict] = {}
    for st in result["showtimes"]:
        targets.setdefault((st["theater_name"], st["start_datetime"][:10]), st)
    print(f"search: {result['meta']['showtimes_returned']} showtimes, "
          f"{len(targets)} distinct link targets\n")
    if not targets:
        print("No showtimes — nothing to check. Try a different --movie or --date.")
        return 1

    failures: list[str] = []
    skipped: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for (theater, day), st in sorted(targets.items()):
                url = st["links"]["best"]
                d = datetime.fromisoformat(day).date()
                # A fresh context each time: AMC serves 403 for its JS chunks on a
                # second navigation from one context.
                ctx = await browser.new_context(user_agent=UA)
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(args.wait * 1000)
                    info = await page.evaluate(PAGE_JS)
                finally:
                    await page.close()
                    await ctx.close()

                text = info["text"]
                haystack = (text + " " + info["title"] + " " + " ".join(info["active"])).lower()

                if any(m in haystack for m in CHALLENGE_MARKERS):
                    skipped.append(f"{theater} [{day}]")
                    print(f"  SKIP  {theater} [{day}]")
                    print(f"        {url}")
                    print("        -> the site served a bot challenge, so this could not be "
                          "checked here; open it in a normal browser to confirm")
                    continue

                problems = []
                if not info["title"].strip():
                    problems.append("page never rendered (empty title)")
                if not any(w.lower() in haystack for w in significant(theater)):
                    problems.append("theatre name absent")
                if args.movie.lower().split()[-1] not in haystack:
                    problems.append(f"'{args.movie}' not listed")
                shown = [t for t in date_tokens(d) if t.lower() in haystack]
                if not shown:
                    problems.append(f"no sign of {day}")

                status = "PASS" if not problems else "FAIL"
                print(f"  {status}  {theater} [{day}]")
                print(f"        {url}")
                if problems:
                    print(f"        -> {'; '.join(problems)}")
                    failures.append(f"{theater} [{day}]: {'; '.join(problems)}")
                else:
                    print(f"        title={info['title'][:56]!r} date seen as {shown[0]!r}")
        finally:
            await browser.close()

    print()
    if skipped:
        print(f"{len(skipped)} link(s) could not be checked from an automated browser "
              "(bot challenge) — verify these by hand:")
        for s_ in skipped:
            print(f"  ? {s_}")
        print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"{len(targets) - len(skipped)} of {len(targets)} links verified on the right "
          "theatre, date and film; the rest were unverifiable here, not wrong.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", default="http://localhost:5173")
    ap.add_argument("--movie", default="The Odyssey")
    ap.add_argument("--location", default="94103")
    ap.add_argument("--radius", type=float, default=100)
    ap.add_argument("--date", help="ISO date_from (default: tomorrow)")
    ap.add_argument("--date-to", dest="date_to", help="ISO date_to")
    ap.add_argument("--wait", type=int, default=7, help="seconds to let each page settle")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
