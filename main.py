#!/usr/bin/env python3
"""Fill in FLFFL weekly scoring for your assigned teams.

Usage:
    python main.py --week 1 --live              # actually fill & submit
    python main.py --week 1                     # dry run (default): fills nothing,
                                                  # just prints what it WOULD enter
    python main.py --week 1 --teams ARI,BUF      # only these teams
    python main.py --week 1 --headed             # show the browser while it runs

Always writes a full audit log to logs/, live run or not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from flffl_scorer import config, name_match, site, stats
from flffl_scorer import state as run_state


def notify_macos(title: str, message: str) -> None:
    """Best-effort macOS notification — used so an unattended (scheduled)
    run surfaces weird plays or failures even if nobody's watching the
    terminal. Never raises; a missing/failed osascript just means no
    notification, not a broken run."""
    try:
        import subprocess

        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        # osascript notifications open Script Editor when clicked, so point
        # at the log in the text instead.
        log = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.LOG_DIR, "scheduled_runs.log")
        log = log.replace(os.path.expanduser("~"), "~", 1)
        message = f"{message} Log: {log}"
        print(f"  [notification] {title} — {message}")
        script = f'display notification "{esc(message)}" with title "{esc(title)}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], timeout=10, capture_output=True)
    except Exception:
        pass


def build_position_fills(section: str, label: str, player_stats: dict | None) -> str | None:
    label_map = site.LABEL_MAPS.get(section)
    if not label_map or label not in label_map:
        return None
    if player_stats is None:
        return None  # DNP — leave blank

    stat_key = label_map[label]
    if stat_key == "two_pt":
        value = sum(
            player_stats.get(k, 0) for k in ("pass_2pt", "rush_2pt", "rec_2pt") if k in player_stats
        )
    elif stat_key.startswith("fg_dist_"):
        idx = int(stat_key.rsplit("_", 1)[1])
        distances = player_stats.get("fg_distances", [])
        value = distances[idx] if idx < len(distances) else None  # unused kick slot — leave blank
    else:
        value = player_stats.get(stat_key)

    return None if value is None else str(value)


def process_team(popup, team: str, rows: list[dict], offense: dict, kicking: dict, defense: dict, audit: dict, live: bool):
    audit["teams"][team] = {
        "positions": {}, "unmatched_players": [], "weird_plays": [],
        "game_score": {"team": defense.get("team_total_pts"), "opponent": defense.get("opp_total_pts")},
    }
    known_qb_pbp_names: set[str] = set()

    for tab in config.POSITION_TABS:
        site.click_position_tab(popup, tab)
        if live:
            site.fill_game_score(popup, defense.get("team_total_pts"), defense.get("opp_total_pts"))
        targets = site.discover_fields(popup)
        tab_audit = []

        # Group targets by player so we resolve the name-match once per player.
        players_in_tab = []
        seen = set()
        for t in targets:
            if t.player and t.player not in seen:
                seen.add(t.player)
                players_in_tab.append(t.player)

        resolved: dict[str, dict | None] = {}
        matched_by: dict[str, list[str]] = {}  # pbp_name -> [flffl_names that matched it]
        for flffl_name in players_in_tab:
            if tab == "Defense":
                resolved[flffl_name] = defense
                continue
            source = kicking if tab == "Kickers" else offense
            pbp_name = name_match.match_player(flffl_name, list(source.keys()))
            if pbp_name is None:
                audit["teams"][team]["unmatched_players"].append({"tab": tab, "name": flffl_name})
                resolved[flffl_name] = None
            else:
                resolved[flffl_name] = source[pbp_name]
                matched_by.setdefault(pbp_name, []).append(flffl_name)
                if tab == "Quarterbacks":
                    known_qb_pbp_names.add(pbp_name)

        # Two different FLFFL players matching the same real player is
        # exactly the symptom of the Arian-Smith/Geno-Smith bug found live
        # (a name-parsing edge case made an unrelated player's stats get
        # written onto a DNP player's row). Catching it generically here
        # means any FUTURE name-format surprise gets flagged and refused
        # to fill, instead of silently duplicating data, the way that one
        # slipped through in week 1 before this check existed.
        for pbp_name, flffl_names in matched_by.items():
            if len(flffl_names) > 1:
                for flffl_name in flffl_names:
                    resolved[flffl_name] = None
                audit["teams"][team]["weird_plays"].append(
                    {
                        "type": "ambiguous_name_match",
                        "tab": tab,
                        "pbp_player": pbp_name,
                        "flffl_players": flffl_names,
                    }
                )
                print(f"  ⚠️  WEIRD: {tab} — {flffl_names} all matched to the same real player ({pbp_name}); skipped filling all of them, needs manual review")

        for t in targets:
            player_stats = resolved.get(t.player)
            value = build_position_fills(t.section, t.label, player_stats)
            tab_audit.append(
                {"player": t.player, "section": t.section, "label": t.label, "value": value}
            )
            if live and value is not None:
                site.fill_field(popup, t.tag, value)

        audit["teams"][team]["positions"][tab] = tab_audit

    # Extend, don't replace: ambiguous-name-match warnings were already added
    # to this list while walking the tabs (an earlier version overwrote them).
    weird_plays = (
        audit["teams"][team]["weird_plays"]
        + stats.find_unaccounted_touchdowns(rows, team)
        + stats.find_non_qb_passers(rows, team, known_qb_pbp_names)
        + stats.find_lateral_plays(rows, team)
        + stats.find_yardage_anomalies(rows, team)
    )
    audit["teams"][team]["weird_plays"] = weird_plays
    if weird_plays:
        print(f"  ⚠️  {len(weird_plays)} WEIRD PLAY(S) for {team} — not represented on the normal form, needs manual entry / commissioner note:")
        for w in weird_plays:
            if w["type"] == "unaccounted_touchdown":
                print(f"      - Unaccounted TD: {w['player']} on a {w['play_type']} play, {w['yards']} yds — \"{w['desc'].strip()}\"")
            elif w["type"] == "non_qb_passer":
                print(f"      - Non-QB passing: {w['player']} attempted a pass — check offense stats for their passing line")
            elif w["type"] == "lateral_play":
                note = ("entered as nfl.com credits it: first receiver up to the lateral, recipient gets the rest as receiving yards with 0 rec"
                        if w["kind"] == "reception" else "the second runner's yards were NOT entered anywhere — add by hand")
                print(f"      - Lateral {w['kind']}: {w['from']} -> {w['to']} ({w['yards_after_lateral']} yds after the lateral{', TD' if w['touchdown'] else ''}); {note}")
            elif w["type"] == "yardage_mismatch":
                print(f"      - Yardage doesn't add up on a {w['kind']}: credited {w['credited_yards']} of {w['play_yards']} yds — check who should get the rest — \"{w['desc'].strip()}\"")
            elif w["type"] == "ambiguous_name_match":
                print(f"      - Ambiguous name match on {w['tab']}: {w['flffl_players']} all matched {w['pbp_player']} — none were filled")

    review_text = site.click_review_and_submit(popup, submit=live)
    audit["teams"][team]["review_text"] = review_text
    return weird_plays


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=config.SEASON_YEAR)
    parser.add_argument("--week", type=int, default=None, help="Defaults to the current week, auto-detected from today's date")
    parser.add_argument("--teams", type=str, default=None, help="Comma-separated team codes; default = all assigned teams")
    parser.add_argument("--live", action="store_true", help="Actually fill and submit. Without this flag, nothing is typed or saved.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless")
    parser.add_argument("--force", action="store_true", help="Re-fill teams even if their data and the code are unchanged since the last live fill")
    parser.add_argument("--refresh-stats", action="store_true", help="Force re-download of play-by-play data even if cached")
    args = parser.parse_args()
    if args.week is None:
        args.week = config.resolve_current_week()
        print(f"--week not given, auto-detected as week {args.week}")

    load_dotenv()
    username = os.environ.get("FLFFL_USERNAME")
    password = os.environ.get("FLFFL_PASSWORD")
    if not username or not password:
        sys.exit("Set FLFFL_USERNAME and FLFFL_PASSWORD in .env (see .env.example)")

    print(f"Loading week {args.week}, {args.season} play-by-play data...")
    rows = stats.load_week_rows(args.season, args.week, force_refresh=args.refresh_stats)
    if not rows:
        sys.exit(f"No play-by-play rows found for {args.season} week {args.week}. Games may not have started yet.")
    print(f"  {len(rows)} plays loaded.")

    audit = {
        "season": args.season, "week": args.week, "live": args.live,
        "run_at": datetime.now().isoformat(), "teams": {},
    }

    # Run state (live runs only): lets repeated runs skip teams whose data and
    # code haven't changed since they were last filled, and keeps notifications
    # to things the user hasn't already been told about. See flffl_scorer/state.py.
    run_st = run_state.load() if args.live else {}
    wk_st = run_state.week_state(run_st, args.season, args.week)
    code_fp = run_state.code_fingerprint()
    filled = {"new": [], "updated": [], "refreshed": []}
    new_weird = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page()
        print("Logging in...")
        site.login(page, username, password)

        if args.teams:
            teams = [t.strip().upper() for t in args.teams.split(",")]
        else:
            teams = [row["team"] for row in site.get_assigned_teams(page)]
        print(f"Teams: {', '.join(teams)}")

        data_not_ready = []
        week_mismatch = []
        for team in teams:
            print(f"\n{team}:")
            offense = stats.offensive_player_stats(rows, team)
            kicking = stats.kicking_stats(rows, team)
            defense = stats.defense_stats(rows, team)
            print(f"  offense players with stats: {len(offense)}, kickers: {len(kicking)}")

            # Skip a team whose data AND the code are identical to the last
            # time it was live-filled: nothing new to write, and not re-writing
            # means hand-entered adjustments (e.g. points for a weird play) are
            # left alone until the data actually changes. --force overrides.
            data_fp = run_state.data_fingerprint(offense, kicking, defense)
            prev = wk_st["teams"].get(team)
            if (args.live and not args.force and (offense or kicking) and prev
                    and prev.get("data") == data_fp and prev.get("code") == code_fp):
                print("  UNCHANGED since last fill — skipping (use --force to re-fill)")
                audit["teams"][team] = {"skipped": "unchanged since last fill"}
                continue

            page.goto(config.SCORING_ASSIGNMENTS_URL)
            page.wait_for_load_state("networkidle")
            try:
                popup = site.open_team_popup(page, team)
            except site.NoScoringControl as e:
                print(f"  SKIPPED: {e}")
                audit["teams"][team] = {"skipped": str(e)}
                continue

            # Refuse to fill a form for a different week than the one we're
            # scoring: FLFFL keeps last week's form enabled for a team until
            # its next game is final, and writing this week's stats onto it
            # would overwrite the wrong week's data. Fails closed if the
            # week can't be read.
            popup_week = site.get_popup_week(popup)
            if popup_week != args.week:
                reason = f"popup is for week {popup_week}, not week {args.week} — not filling"
                print(f"  ⚠ WEEK MISMATCH: {team} {reason}")
                audit["teams"][team] = {"skipped": reason}
                week_mismatch.append(team)
                popup.close()
                continue

            # FLFFL has the "Enter Stats" button enabled (their game is
            # final) but our data source has nothing for this team at all —
            # a real gap found in production: nflverse hadn't published a
            # just-finished game yet when the Monday scheduled run fired.
            # Rather than silently submit blank/DNP for a team that
            # obviously played, skip it and flag loudly for a re-run once
            # data catches up.
            if not offense and not kicking:
                print(f"  ⚠ DATA NOT READY: {team}'s game is marked final but no play data was found (source lag?) — SKIPPING, needs a follow-up run.")
                audit["teams"][team] = {"skipped": "FLFFL shows this game final but no play-by-play data was found yet (source lag)"}
                data_not_ready.append(team)
                popup.close()
                continue
            try:
                weird = process_team(popup, team, rows, offense, kicking, defense, audit, live=args.live)
            finally:
                popup.close()

            if args.live:
                kind = "new" if not prev else ("updated" if prev.get("data") != data_fp else "refreshed")
                filled[kind].append(team)
                wk_st["teams"][team] = {
                    "data": data_fp, "code": code_fp,
                    "filled_at": datetime.now().isoformat(timespec="seconds"),
                }
                if team in wk_st["alerted_not_ready"]:
                    wk_st["alerted_not_ready"].remove(team)
                for w in weird:
                    key = f"{team}|{json.dumps(w, sort_keys=True)}"
                    if key not in wk_st["alerted_weird"]:
                        wk_st["alerted_weird"].append(key)
                        new_weird.append(team)
                run_state.save(run_st)

        browser.close()

    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_path = os.path.join(
        config.LOG_DIR, f"run_{args.season}_wk{args.week}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(log_path, "w") as f:
        json.dump(audit, f, indent=2)
    print(f"\nAudit log written to {log_path}")

    total_unmatched = sum(len(t.get("unmatched_players", [])) for t in audit["teams"].values())
    if total_unmatched:
        print(f"\n⚠ {total_unmatched} player(s) could not be matched to a stat line (treated as DNP). Check the audit log.")

    total_weird = sum(len(t.get("weird_plays", [])) for t in audit["teams"].values())
    if total_weird:
        print(f"\n⚠️  {total_weird} WEIRD PLAY(S) this week need manual entry — see the per-team warnings above and the 'weird_plays' section of the audit log.")

    if data_not_ready:
        print(f"\n⚠ DATA NOT READY for: {', '.join(data_not_ready)} — their game is final on FLFFL but no play data was found yet. Re-run with --teams {','.join(data_not_ready)} --live once data catches up.")

    if week_mismatch:
        print(f"\n⚠ WEEK MISMATCH for: {', '.join(week_mismatch)} — their FLFFL form isn't for week {args.week}, so nothing was filled.")

    # Notifications (live runs only), each raised once rather than every run:
    # new weird plays, newly-not-ready teams, and teams newly scored/updated.
    if args.live:
        if new_weird:
            notify_macos(
                f"FLFFL: new weird play(s) — week {args.week}",
                f"{', '.join(sorted(set(new_weird)))} — needs manual entry / commissioner note.",
            )
        newly_not_ready = [t for t in data_not_ready if t not in wk_st["alerted_not_ready"]]
        if newly_not_ready:
            wk_st["alerted_not_ready"].extend(newly_not_ready)
            notify_macos(
                f"FLFFL: {len(newly_not_ready)} team(s) need a follow-up run",
                f"{', '.join(newly_not_ready)} — game final but no stats data yet (retried automatically while the schedule runs).",
            )
        if filled["new"] or filled["updated"]:
            parts = []
            if filled["new"]:
                parts.append(f"scored {', '.join(filled['new'])}")
            if filled["updated"]:
                parts.append(f"updated {', '.join(filled['updated'])}")
            notify_macos(
                f"FLFFL week {args.week}: " + "; ".join(parts),
                f"{len(wk_st['teams'])} team(s) filled so far this week.",
            )
        run_state.save(run_st)

    if not args.live:
        print("\nThis was a DRY RUN — nothing was typed or submitted. Re-run with --live to actually fill the form.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify_macos("FLFFL scoring FAILED", f"{type(e).__name__}: {e}")
        raise
