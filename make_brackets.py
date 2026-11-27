#!/usr/bin/env python3
"""Build the five bowls' playoff brackets as one PDF, one page per bowl.

Usage:
    python make_brackets.py                                # log in, read rankings.php, write the PDF
    python make_brackets.py --rankings brackets/rankings_2026-11-26.json   # reuse a saved snapshot
    python make_brackets.py --results results.json         # also fill in playoff scores

Logging in uses FLFFL_USERNAME / FLFFL_PASSWORD from .env, as main.py does.
Every live read saves the rankings table to brackets/rankings_<date>.json, so
the same seeds can be re-rendered later without logging in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from flffl_scorer import brackets, config, site


def fetch_rankings(page, username: str, password: str) -> list[dict]:
    site.login(page, username, password)
    page.goto(config.RANKINGS_URL)
    page.wait_for_load_state("networkidle")
    return brackets.read_rankings(page)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rankings", help="saved rankings snapshot (JSON) to use instead of logging in")
    ap.add_argument("--results", help="playoff scores JSON (see flffl_scorer/brackets.py Results)")
    ap.add_argument("--out", help=f"PDF path (default {config.BRACKETS_DIR}/FLFFL_Brackets_<year>.pdf)")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args()

    os.makedirs(config.BRACKETS_DIR, exist_ok=True)
    out = args.out or os.path.join(config.BRACKETS_DIR, f"FLFFL_Brackets_{config.SEASON_YEAR}.pdf")
    results = brackets.Results.load(args.results) if args.results else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page()
        try:
            if args.rankings:
                with open(args.rankings) as f:
                    snap = json.load(f)
            else:
                load_dotenv()
                user, pw = os.environ.get("FLFFL_USERNAME"), os.environ.get("FLFFL_PASSWORD")
                if not user or not pw:
                    print("FLFFL_USERNAME / FLFFL_PASSWORD missing from .env", file=sys.stderr)
                    return 2
                snap = {"read_at": datetime.now().isoformat(timespec="minutes"),
                        "source": config.RANKINGS_URL,
                        "rankings": fetch_rankings(page, user, pw)}
                snap_path = os.path.join(config.BRACKETS_DIR, f"rankings_{snap['read_at'][:10]}.json")
                with open(snap_path, "w") as f:
                    json.dump(snap, f, indent=1)
                print(f"Saved rankings snapshot: {snap_path}")

            seeds = brackets.seeds_by_bowl(snap["rankings"])
            try:
                bowls = [brackets.build_bowl(key, seeds[key], results) for key in config.BOWLS]
            except brackets.ResultsError as e:
                print(f"Can't place a game: {e}", file=sys.stderr)
                return 1
            read_at = datetime.fromisoformat(snap["read_at"])
            subtitle = f"Seeds from flffl.com rankings, {read_at:%b %-d, %Y %-I:%M %p}"

            html_path = os.path.splitext(out)[0] + ".html"
            brackets.write_pdf(page, bowls, config.SEASON_YEAR, subtitle, out, html_path)
        finally:
            browser.close()

    for b in bowls:
        first = b.root.rounds[0]
        print(f"\n{b.title}")
        for g in first:
            print(f"  ({g.a.seed:>2}) {g.a.name:<32} vs ({g.b.seed:>2}) {g.b.name}")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
