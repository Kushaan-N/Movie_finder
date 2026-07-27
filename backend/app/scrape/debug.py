"""Offline selector-tuning harness.

Save a real chain seat-selection page from your browser (right-click → Save As, or
DevTools → Copy outerHTML) and run:

    python -m app.scrape.debug amc /path/to/saved_seatmap.html
    python -m app.scrape.debug amc page.html --seats 4 --min-row 5

It prints the parsed rows with their physical-row normalization and a simulated
seat check, so you can verify/tune the selectors in scrape_selectors.json against
a real page — no live scraping, no API keys.
"""
from __future__ import annotations

import argparse
import sys

from ..rows import normalize_row
from ..services.seatcheck import evaluate_rows
from .seatmap import parse_seat_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse a saved seat-map HTML file and show the result.")
    parser.add_argument("chain", help="chain key (amc, regal, cinemark, ...)")
    parser.add_argument("html_file", help="path to a saved seat-selection HTML page")
    parser.add_argument("--seats", type=int, default=4, help="seats_together to simulate (default 4)")
    parser.add_argument("--min-row", type=int, default=5, help="min physical row to simulate (default 5)")
    parser.add_argument("--theater-id", default="debug", help="theater id for override lookups")
    args = parser.parse_args(argv)

    try:
        with open(args.html_file, encoding="utf-8") as f:
            html = f.read()
    except OSError as exc:
        print(f"Could not read {args.html_file}: {exc}", file=sys.stderr)
        return 2

    result = parse_seat_html(args.chain, html)
    print(f"chain={args.chain}  file={args.html_file}")
    print(f"stats: {result.stats}")

    if not result.ok:
        print(f"\n❌ Not parseable → 'check manually'. Reason: {result.reason}")
        print("   Tip: open scrape_selectors.json and adjust the selectors for this chain,")
        print("   then re-run. DOM order is used for physical position regardless of labels.")
        return 1

    print(f"\n✅ Parsed {len(result.rows)} rows (top = closest to screen):\n")
    for idx, row in enumerate(result.rows):
        interp = normalize_row(args.chain, row.raw_label, dom_order_index=idx, theater_id=args.theater_id)
        avail = "".join("█" if a else "·" for a in row.seats_available)
        n_avail = sum(row.seats_available)
        print(f"  {interp.display:<34} avail={n_avail:>2}/{len(row.seats_available):<2}  [{avail}]")

    check = evaluate_rows(result.rows, args.chain, args.theater_id, args.seats, args.min_row)
    print(f"\nSimulated seat check (seats_together={args.seats}, min_row={args.min_row}):")
    print(f"  status = {check.status}")
    if check.best_block_row:
        print(f"  best contiguous block = {check.best_block_size} in {check.best_block_row.display}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
