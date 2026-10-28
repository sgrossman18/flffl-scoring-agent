"""Constants for the FLFFL scoring form and this season's schedule."""

import datetime

FLFFL_BASE_URL = "https://flffl.com/2026"
LOGIN_URL = f"{FLFFL_BASE_URL}/index.php"
SCORING_ASSIGNMENTS_URL = f"{FLFFL_BASE_URL}/scoring_assignments.php"

POSITION_TABS = ["Quarterbacks", "Running Backs", "Receivers", "Kickers", "Defense"]

# nflverse uses the same team codes FLFFL displayed on the assignments page
# (ARI, LAC, etc.) for every team we've seen so far. If a team fails to match
# during a run, add an override here.
TEAM_CODE_OVERRIDES: dict[str, str] = {}

# FLFFL roster names that can't be matched automatically because FLFFL lists a
# nickname while nflverse uses the legal first initial, mapped to the nflverse
# "F.Last" name: {FLFFL roster name: nflverse name}. An alias overrides the
# normal matching entirely (see name_match.match_player). Only add one after
# confirming it's the same person, e.g. against nfl.com's box score — a wrong
# alias writes one player's stats onto another's row. A run flags likely
# candidates as "possible_name_mismatch" weird plays.
NAME_ALIASES: dict[str, str] = {
    "Ogletree, Drew": "A.Ogletree",  # IND TE Andrew "Drew" Ogletree; nfl.com lists him as A. Ogletree
}

DATA_CACHE_DIR = "data_cache"
LOG_DIR = "logs"

# First day of week 1. The week rolls over at midnight Wednesday, NOT
# Tuesday: Monday Night Football finishes late Monday and nflverse can lag
# for hours, so Tuesday (including the early-Tuesday scheduled runs) still
# has to resolve to the week that just played. (Resolving Tuesday to the next
# week made the old Tuesday 8 AM runs exit with "No play-by-play rows found".)
# Update this at the start of each new season.
SEASON_WEEK1_START = datetime.date(2026, 9, 9)
SEASON_YEAR = 2026

STATE_FILE = "logs/run_state.json"


def resolve_current_week(today: datetime.date | None = None) -> int:
    """Best-guess current NFL week from today's date, clamped to [1, 18]
    (regular season only — postseason weeks aren't modeled)."""
    today = today or datetime.date.today()
    days_since = (today - SEASON_WEEK1_START).days
    week = days_since // 7 + 1
    return max(1, min(18, week))
