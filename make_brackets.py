#!/usr/bin/env python3
"""Build the five bowls' playoff brackets as one PDF, one page per bowl.

Usage:
    python make_brackets.py                         # log in, read seeds + playoff scores, write the PDF
    python make_brackets.py --snapshot brackets/snapshot_2026-12-02.json   # re-render a saved read, no login
    python make_brackets.py --results results.json  # take scores from a hand-written file instead

Logging in uses FLFFL_USERNAME / FLFFL_PASSWORD from .env, as main.py does.
Seeds come from rankings.php; scores and winners from match_ups.php for each
playoff week. Only finished weeks advance teams: a week counts as finished
once the next one has started (the week rolls over at midnight Wednesday,
Central), or pass --final-through. Every live read is saved to
brackets/snapshot_<date>.json so it can be re-rendered without logging in.
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

PLAYOFF_WEEKS = list(range(config.PLAYOFF_FIRST_WEEK, config.PLAYOFF_FIRST_WEEK + 4))


def read_site(page, username: str, password: str) -> dict:
    site.login(page, username, password)
    page.goto(config.RANKINGS_URL)
    page.wait_for_load_state("networkidle")
    rankings = brackets.read_rankings(page)
    matchups = brackets.read_matchups(page, PLAYOFF_WEEKS)
    return {"read_at": datetime.now(tz=config.schedule_now().tzinfo).isoformat(timespec="minutes"),
            "rankings": rankings, "matchups": {str(w): g for w, g in matchups.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", "--rankings", dest="snapshot",
                    help="saved snapshot (JSON) to use instead of logging in")
    ap.add_argument("--results", help="hand-written scores JSON to use instead of the site's "
                                      "(see flffl_scorer/brackets.py Results)")
    ap.add_argument("--final-through", type=int,
                    help="last finished playoff week (default: every week before the current one)")
    ap.add_argument("--out", help=f"PDF path (default {config.BRACKETS_DIR}/FLFFL_Brackets_<year>.pdf)")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args()

    os.makedirs(config.BRACKETS_DIR, exist_ok=True)
    out = args.out or os.path.join(config.BRACKETS_DIR, f"FLFFL_Brackets_{config.SEASON_YEAR}.pdf")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page()
        try:
            if args.snapshot:
                with open(args.snapshot) as f:
                    snap = json.load(f)
            else:
                load_dotenv()
                user, pw = os.environ.get("FLFFL_USERNAME"), os.environ.get("FLFFL_PASSWORD")
                if not user or not pw:
                    print("FLFFL_USERNAME / FLFFL_PASSWORD missing from .env", file=sys.stderr)
                    return 2
                snap = read_site(page, user, pw)
                snap_path = os.path.join(config.BRACKETS_DIR, f"snapshot_{snap['read_at'][:10]}.json")
                with open(snap_path, "w") as f:
                    json.dump(snap, f, indent=1)
                print(f"Saved snapshot: {snap_path}")

            read_at = datetime.fromisoformat(snap["read_at"])
            matchups = {int(w): g for w, g in snap.get("matchups", {}).items()}
            final_through = args.final_through
            if final_through is None:
                final_through = config.resolve_current_week(read_at.date()) - 1

            if args.results:
                results = brackets.Results.load(args.results)
                score_note = f"Scores from {os.path.basename(args.results)}"
            else:
                results = brackets.Results.from_matchups(matchups, final_through)
                scored = results.weeks_with_scores
                final = [w for w in scored if w <= final_through]
                live = [w for w in scored if w > final_through]
                parts = []
                if final:
                    parts.append(f"results through week {final[-1]}")
                if live:
                    parts.append(f"week {live[0]} in progress")
                score_note = ("Scores: " + ", ".join(parts)) if parts else ""

            seeds = brackets.seeds_by_bowl(snap["rankings"])
            try:
                bowls = [brackets.build_bowl(key, seeds[key], results) for key in config.BOWLS]
            except brackets.ResultsError as e:
                print(f"Can't place a game: {e}", file=sys.stderr)
                return 1

            subtitle = f"From flffl.com, {read_at:%b %-d, %Y %-I:%M %p}"
            if score_note:
                subtitle += f". {score_note}"

            html_path = os.path.splitext(out)[0] + ".html"
            brackets.write_pdf(page, bowls, config.SEASON_YEAR, subtitle, out, html_path)
        finally:
            browser.close()

    problems = brackets.check_pairings(bowls, matchups)
    for b in bowls:
        print(f"\n{b.title}")
        for grp in b.root.all_groups():
            for rnd in grp.rounds:
                for g in rnd:
                    if g.a is None or g.b is None:
                        continue
                    if g.score_a is None and g.week != config.PLAYOFF_FIRST_WEEK:
                        continue
                    sa = "" if g.score_a is None else f"{g.score_a:g}"
                    sb = "" if g.score_b is None else f"{g.score_b:g}"
                    mark = "" if g.winner is None else f"  -> {g.winner.name}"
                    print(f"  wk{g.week} ({g.a.seed:>2}) {g.a.name:<30} {sa:>5} vs {sb:<5} "
                          f"({g.b.seed:>2}) {g.b.name}{mark}")
    print(f"\n{subtitle}")
    if problems:
        print(f"\nWARNING: {len(problems)} bracket game(s) don't match flffl.com's pairings:")
        for line in problems:
            print(f"  {line}")
    else:
        checked = sorted(w for w, g in matchups.items() if g)
        if checked:
            print(f"Bracket pairings match flffl.com for week(s) {', '.join(map(str, checked))}.")
        else:
            print("flffl.com hasn't posted any playoff pairings yet, so nothing was cross-checked.")
    print(f"\nWrote {out}")
    return 3 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
