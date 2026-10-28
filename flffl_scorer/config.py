"""Constants for the FLFFL scoring form and this season's schedule."""

import datetime
from zoneinfo import ZoneInfo

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

# Everything time-based below is defined in this timezone, NOT the Mac's.
# The launchd job fires in the Mac's local time, which changes when you
# travel (in Los Angeles the old schedule ran 2 hours late in Central terms),
# so the job now fires every hour and the run itself decides, in Central
# time, whether this hour is one it should run.
SCHEDULE_TZ = "America/Chicago"

# Hours (Central, 0-23, inclusive) of each weekday on which a scheduled run
# should go ahead; keys are Python weekdays (Mon=0 ... Sun=6). Hourly runs in
# three windows: Thu 10 PM-Fri 6 AM (Thursday Night Football), Sun 4 PM-Mon 6
# AM, and Mon 11 PM-Tue 6 AM (Monday Night Football).
RUN_HOURS: dict[int, list[int]] = {
    3: [22, 23],                      # Thu 10 PM - 11 PM
    4: list(range(0, 7)),             # Fri 12 AM - 6 AM
    6: list(range(16, 24)),           # Sun 4 PM - 11 PM
    0: list(range(0, 7)) + [23],      # Mon 12 AM - 6 AM, and 11 PM
    1: list(range(0, 7)),             # Tue 12 AM - 6 AM
}


def schedule_now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(SCHEDULE_TZ))


def in_scoring_window(now: datetime.datetime | None = None) -> bool:
    """True if `now` (any timezone; naive means Central) falls in an hour
    listed in RUN_HOURS, judged in Central time."""
    now = now or schedule_now()
    tz = ZoneInfo(SCHEDULE_TZ)
    now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    return now.hour in RUN_HOURS.get(now.weekday(), [])


def resolve_current_week(today: datetime.date | None = None) -> int:
    """Best-guess current NFL week from today's date in Central time (the
    week rolls over at midnight Central, whatever timezone this Mac is in),
    clamped to [1, 18] (regular season only — postseason isn't modeled)."""
    today = today or schedule_now().date()
    days_since = (today - SEASON_WEEK1_START).days
    week = days_since // 7 + 1
    return max(1, min(18, week))
