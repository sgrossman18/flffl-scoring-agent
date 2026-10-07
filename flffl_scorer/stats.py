"""Compute FLFFL-shaped stat lines from nflverse play-by-play data.

Source: https://github.com/nflverse/nflverse-data (play_by_play_<season>.csv),
updated by nflverse within a few hours of games. We compute everything from
raw plays rather than nflverse's pre-aggregated player_stats file because
that file (a) doesn't include field-goal distance or the defensive
pass/rush-yards-allowed splits FLFFL wants, and (b) was stale/behind by the
time this was written — building from play-by-play avoids depending on it
being current.
"""

from __future__ import annotations

import csv
import os
import urllib.request
from collections import defaultdict

from . import config

PBP_URL_TEMPLATE = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv"


def _cache_path(season: int) -> str:
    return os.path.join(config.DATA_CACHE_DIR, f"play_by_play_{season}.csv")


def download_pbp(season: int, force: bool = False) -> str:
    """Download (or reuse a cached copy of) the season's play-by-play CSV."""
    os.makedirs(config.DATA_CACHE_DIR, exist_ok=True)
    path = _cache_path(season)
    if force or not os.path.exists(path):
        urllib.request.urlretrieve(PBP_URL_TEMPLATE.format(season=season), path)
    return path


def load_week_rows(season: int, week: int, force_refresh: bool = False) -> list[dict]:
    """Load every play for one week, refreshing the cache if that week isn't in it yet."""
    path = download_pbp(season, force=force_refresh)
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("week") == str(week)]
    if not rows and not force_refresh:
        path = download_pbp(season, force=True)
        with open(path, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("week") == str(week)]
    return rows


def _team_code(flffl_code: str) -> str:
    return config.TEAM_CODE_OVERRIDES.get(flffl_code, flffl_code)


def _yards(row: dict) -> int:
    y = row.get("yards_gained") or "0"
    try:
        return int(float(y))
    except ValueError:
        return 0


def _play_yards(row: dict, col: str) -> int:
    """A player's OWN yards on the play (nflverse's per-player column such as
    receiving_yards / rushing_yards), falling back to the whole play's
    yards_gained when that column is empty. They differ on lateral plays:
    yards_gained is the full play, but the first receiver is only credited up
    to the lateral (e.g. Schultz: 2 of a 21-yard play)."""
    v = row.get(col)
    if v not in (None, ""):
        try:
            return int(float(v))
        except ValueError:
            pass
    return _yards(row)


def _is_2pt_success(row: dict) -> bool:
    return row.get("two_point_attempt") == "1" and row.get("two_point_conv_result") == "success"


def offensive_player_stats(rows: list[dict], team: str) -> dict[str, dict]:
    """Return {pbp_player_name: stat_dict} for every skill player who touched the ball for `team`.

    pbp_player_name is nflverse's "F.Last" format (e.g. "J.Brissett") — match
    it to the FLFFL roster with name_match.match_player.
    """
    team = _team_code(team)
    players: dict[str, dict] = defaultdict(
        lambda: {
            "comp": 0, "att": 0, "pass_yds": 0, "pass_td": 0, "int": 0, "pass_2pt": 0,
            "carries": 0, "rush_yds": 0, "rush_td": 0, "rush_2pt": 0,
            "rec": 0, "rec_yds": 0, "rec_td": 0, "rec_2pt": 0,
        }
    )

    for row in rows:
        if row.get("posteam") != team:
            continue

        # Two-point conversion attempts are NOT normal down plays: nflverse
        # still sets pass_attempt/rush_attempt to "1" on them, but always
        # reports complete_pass as "0" even on a successful conversion — the
        # real outcome lives in two_point_conv_result instead. FLFFL's own
        # rules treat a conversion as its own category ("this yardage is not
        # counted into any bonus calculation"), so these plays are excluded
        # from the normal attempt/completion/carry/reception counting below
        # and scored separately via _is_2pt_success().
        is_two_point_attempt = row.get("two_point_attempt") == "1"

        # Sacks carry pass_attempt=1 in nflverse's pbp, but official box
        # scores (nfl.com) exclude sacks from a passer's attempt count —
        # confirmed against Lamar Jackson's week-1 line (27 pass_attempt
        # rows including 2 sacks; official ATT was 25).
        passer = row.get("passer_player_name")
        if passer and row.get("pass_attempt") == "1" and row.get("sack") != "1" and not is_two_point_attempt:
            p = players[passer]
            p["att"] += 1
            if row.get("complete_pass") == "1":
                p["comp"] += 1
                p["pass_yds"] += _yards(row)
            if row.get("pass_touchdown") == "1":
                p["pass_td"] += 1
            if row.get("interception") == "1":
                p["int"] += 1

        # QB kneels ARE counted as carries by nfl.com's official box score
        # (with their usual -1 yard each) — confirmed against Lamar
        # Jackson's week-1 line (7 carries/40 yds includes 3 kneels;
        # excluding them, as an earlier version of this code did, gave 4/43).
        rusher = row.get("rusher_player_name")
        if rusher and row.get("rush_attempt") == "1" and not is_two_point_attempt:
            p = players[rusher]
            p["carries"] += 1
            p["rush_yds"] += _play_yards(row, "rushing_yards")
            if row.get("rush_touchdown") == "1":
                p["rush_td"] += 1

        receiver = row.get("receiver_player_name")
        if receiver and row.get("complete_pass") == "1" and not is_two_point_attempt:
            p = players[receiver]
            p["rec"] += 1
            p["rec_yds"] += _play_yards(row, "receiving_yards")

            # Lateral after the catch: nfl.com's box score credits the first
            # receiver only up to the lateral, and gives the lateral
            # recipient the remaining yards as RECEIVING yards with no
            # reception (and the TD, if the lateral scored one) — confirmed
            # on week 3 (Evans -> Samuel: Samuel 0 rec / 80 yds / 1 TD) and
            # week 4 (Schultz 2 rec / 9 yds, Montgomery 0 rec / 19 yds).
            # The passer keeps the full passing yards/TD (Stroud 347 matched).
            lateral_to = row.get("lateral_receiver_player_name")
            is_lateral = row.get("lateral_reception") == "1" and bool(lateral_to)
            if row.get("pass_touchdown") == "1" and not is_lateral:
                p["rec_td"] += 1
            if is_lateral:
                q = players[lateral_to]
                q["rec_yds"] += _play_yards(row, "lateral_receiving_yards")
                if row.get("pass_touchdown") == "1":
                    q["rec_td"] += 1

        if _is_2pt_success(row):
            if passer:
                players[passer]["pass_2pt"] += 1
            if rusher:
                players[rusher]["rush_2pt"] += 1
            if receiver:
                players[receiver]["rec_2pt"] += 1

    return dict(players)


def kicking_stats(rows: list[dict], team: str) -> dict[str, dict]:
    """Return {pbp_player_name: stat_dict} for the team's kicker(s).

    fg_distances is the yardage of each made field goal, in the order they
    happened. The form's "FG-1".."FG-10" boxes are per-kick slots (this
    kicker's 1st made FG, 2nd, ...) that each take a raw distance — the site
    itself computes the point tier from whatever yardage goes in each slot
    (confirmed from the live page's JS, tiers match the published rules
    page exactly), so we just report distances in order, capped at 10.
    """
    team = _team_code(team)
    kickers: dict[str, dict] = defaultdict(
        lambda: {
            "xp_made": 0, "xp_missed": 0, "xp_blocked": 0,
            "fg_made": 0, "fg_att": 0,
            "fg_distances": [],
        }
    )

    for row in rows:
        if row.get("posteam") != team:
            continue
        kicker = row.get("kicker_player_name")
        if not kicker:
            continue

        if row.get("extra_point_attempt") == "1":
            k = kickers[kicker]
            result = row.get("extra_point_result")
            if result == "good":
                k["xp_made"] += 1
            elif result == "blocked":
                k["xp_blocked"] += 1
            else:
                k["xp_missed"] += 1

        if row.get("field_goal_attempt") == "1":
            k = kickers[kicker]
            k["fg_att"] += 1
            if row.get("field_goal_result") == "made":
                k["fg_made"] += 1
                dist_raw = row.get("kick_distance")
                if dist_raw and len(k["fg_distances"]) < 10:
                    k["fg_distances"].append(int(float(dist_raw)))

    return dict(kickers)


def defense_stats(rows: list[dict], team: str) -> dict:
    """Team-level defensive stat line for `team`.

    Known gap: a punt/kickoff return TD scored *against* this team's special
    teams isn't backed out of opp_total_pts (only turnover-return TDs and
    safeties are, matching the "-Pick/Fum 6" field literally). This is rare;
    flagged in the audit report so it can be checked by hand if it happens.
    """
    team = _team_code(team)
    stats = {
        "def_td": 0, "spec_td": 0, "safety": 0, "interceptions": 0, "fum_rec": 0, "sack": 0,
        "opp_total_pts": 0, "opp_pick_fum6": 0, "opp_safety": 0,
        "pass_yds_allowed": 0, "rush_yds_allowed": 0,
    }

    opponent = None
    team_final_score = 0
    opp_final_score = 0

    for row in rows:
        posteam, defteam = row.get("posteam"), row.get("defteam")
        if team not in (posteam, defteam):
            continue
        if posteam and posteam != team:
            opponent = posteam
        elif defteam and defteam != team:
            opponent = defteam

        if posteam == team:
            team_final_score = max(team_final_score, int(float(row.get("posteam_score_post") or 0)))
        elif defteam == team:
            team_final_score = max(team_final_score, int(float(row.get("defteam_score_post") or 0)))
        if posteam == opponent:
            opp_final_score = max(opp_final_score, int(float(row.get("posteam_score_post") or 0)))
        elif defteam == opponent:
            opp_final_score = max(opp_final_score, int(float(row.get("defteam_score_post") or 0)))

        # "Fum Rec" = fumbles the OPPONENT lost to us (their fumbles lost,
        # matching nfl.com's box-score LOST column), on any kind of play
        # including special teams. Judged per fumble slot (a play can have
        # two) as fumbled-by-them AND recovered-by-us. Deliberately NOT just
        # "we recovered something": that wrongly credited a blocked-field-goal
        # recovery (DEN week 4) and a fumble recovered by the fumbler's own
        # teammate (D.Samuel, SF), and missed special-teams fumbles (a punt
        # muff recovered by the kicking team).
        for slot in ("1", "2"):
            fumbled_team = row.get(f"fumbled_{slot}_team")
            recovery_team = row.get(f"fumble_recovery_{slot}_team")
            if fumbled_team and recovery_team == team and fumbled_team != team:
                stats["fum_rec"] += 1

        if defteam == team:
            if row.get("sack") == "1":
                stats["sack"] += 1
            if row.get("interception") == "1":
                stats["interceptions"] += 1
            if row.get("safety") == "1":
                stats["safety"] += 1
            if row.get("touchdown") == "1" and row.get("td_team") == team:
                if row.get("play_type") in ("kickoff", "punt"):
                    stats["spec_td"] += 1
                else:
                    stats["def_td"] += 1

            if row.get("play_type") == "pass":
                stats["pass_yds_allowed"] += _yards(row)
            elif row.get("play_type") == "run":
                stats["rush_yds_allowed"] += _yards(row)

        # Opponent's defense/special teams scoring against this team's own
        # offense. These are COUNTS, not points: the form's "-Pick/Fum 6" and
        # "-Safety" boxes are multiplied by 6 and 2 by the site's own JS
        # (CalculateOppDefPts). Entering points here (as the first weeks'
        # runs did) subtracted 6x / 2x too much from points allowed.
        if posteam == team and defteam and defteam != team:
            if row.get("touchdown") == "1" and row.get("td_team") == defteam:
                stats["opp_pick_fum6"] += 1
            if row.get("safety") == "1":
                stats["opp_safety"] += 1

    stats["opp_total_pts"] = opp_final_score
    stats["team_total_pts"] = team_final_score
    stats["opponent"] = opponent
    # Sacks are coded as play_type == "pass" with a negative yards_gained, so
    # pass_yds_allowed (summed over all "pass" plays, complete or not) is
    # already net of sack yardage — matching the rule text directly, no
    # separate subtraction needed.
    stats["pass_yds_allowed_net"] = stats["pass_yds_allowed"]
    return stats


def find_unaccounted_touchdowns(rows: list[dict], team: str) -> list[dict]:
    """Touchdowns scored by `team` that don't fit a normal pass/rush TD (an
    offensive player's own row) or a defensive/special-teams TD (the team
    Defense tab's Def TD / Spec TD fields) — most commonly an individual
    punt/kickoff return TD by an offensive player, which FLFFL's per-player
    forms have no field for at all (the rules page says the returner gets 6
    points, but there's nowhere on their Rushing/Receiving row to put it).

    Returns raw play facts, not a point value — computing FLFFL's exact
    point award for a play their form doesn't model is a judgment call, not
    something to guess at silently.
    """
    team = _team_code(team)
    weird = []
    for row in rows:
        if row.get("td_team") != team:
            continue
        if row.get("posteam") == team and row.get("pass_touchdown") == "1":
            continue  # normal passing TD — passer/receiver rows handle it
        if row.get("posteam") == team and row.get("rush_touchdown") == "1":
            continue  # normal rushing TD — rusher row handles it
        if row.get("defteam") == team and row.get("touchdown") == "1":
            continue  # defensive/ST TD — Def TD or Spec TD field handles it

        weird.append(
            {
                "type": "unaccounted_touchdown",
                "play_type": row.get("play_type"),
                "player": row.get("punt_returner_player_name")
                or row.get("kickoff_returner_player_name")
                or row.get("fumble_recovery_1_player_name")
                or row.get("interception_player_name"),
                "yards": _yards(row),
                "desc": row.get("desc", ""),
            }
        )
    return weird


def find_yardage_anomalies(rows: list[dict], team: str) -> list[dict]:
    """Plays where the yards we credit to players don't add up to the play.

    On a normal completed pass or run, the receiver's/rusher's own yards equal
    the play's yards. They differ only on lateral plays, where the first
    player's yards plus the lateral recipient's must still add up to the whole
    play. Anything else -- a lateral nflverse didn't flag, a double lateral,
    some other odd play -- means yards may be credited to the wrong player (the
    way the week-3 Evans/Samuel lateral gave Evans the whole 82 yards), so it's
    flagged for a human instead of trusted. (Team-level totals can't catch
    this: the whole play's yards still land on *someone*.)"""
    team = _team_code(team)
    flagged = []
    for row in rows:
        if row.get("posteam") != team or row.get("two_point_attempt") == "1":
            continue
        if row.get("complete_pass") == "1" and row.get("receiver_player_name"):
            own, extra, kind = _play_yards(row, "receiving_yards"), 0, "reception"
            if row.get("lateral_reception") == "1" and row.get("lateral_receiver_player_name"):
                extra = _play_yards(row, "lateral_receiving_yards")
        elif row.get("rush_attempt") == "1" and row.get("rusher_player_name"):
            own, extra, kind = _play_yards(row, "rushing_yards"), 0, "rush"
            if row.get("lateral_rush") == "1" and row.get("lateral_rusher_player_name"):
                extra = _play_yards(row, "lateral_rushing_yards")
        else:
            continue
        if own + extra != _yards(row):
            flagged.append({
                "type": "yardage_mismatch", "kind": kind,
                "credited_yards": own + extra, "play_yards": _yards(row), "desc": row.get("desc", ""),
            })
    return flagged


def find_lateral_plays(rows: list[dict], team: str) -> list[dict]:
    """Plays where the ball was lateraled after a catch or a handoff. Receiving
    laterals are entered the way nfl.com credits them (see
    offensive_player_stats), but it's an unusual play worth a look; rushing
    laterals aren't credited to the second runner at all because nfl.com's
    convention for them hasn't been verified, so those need a manual entry."""
    team = _team_code(team)
    flagged = []
    for row in rows:
        if row.get("posteam") != team:
            continue
        if row.get("lateral_reception") == "1" and row.get("lateral_receiver_player_name"):
            flagged.append({
                "type": "lateral_play", "kind": "reception", "from": row.get("receiver_player_name"),
                "to": row.get("lateral_receiver_player_name"),
                "yards_after_lateral": _play_yards(row, "lateral_receiving_yards"),
                "touchdown": row.get("pass_touchdown") == "1", "desc": row.get("desc", ""),
            })
        elif row.get("lateral_rush") == "1" and row.get("lateral_rusher_player_name"):
            flagged.append({
                "type": "lateral_play", "kind": "rush", "from": row.get("rusher_player_name"),
                "to": row.get("lateral_rusher_player_name"),
                "yards_after_lateral": _play_yards(row, "lateral_rushing_yards"),
                "touchdown": row.get("rush_touchdown") == "1", "desc": row.get("desc", ""),
            })
    return flagged


def find_non_qb_passers(rows: list[dict], team: str, known_qb_names: set[str]) -> list[dict]:
    """Pass attempts by a player not on FLFFL's Quarterbacks tab for this
    team — e.g. a trick-play TD pass by a WR/RB. FLFFL's RB/WR forms have no
    Passing section, so these stats have nowhere to go on the normal form.
    """
    team = _team_code(team)
    flagged = []
    seen = set()
    for row in rows:
        if row.get("posteam") != team or row.get("pass_attempt") != "1":
            continue
        passer = row.get("passer_player_name")
        if not passer or passer in known_qb_names or passer in seen:
            continue
        seen.add(passer)
        flagged.append({"type": "non_qb_passer", "player": passer})
    return flagged
