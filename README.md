# FLFFL Scorer

Automates weekly stat entry on flffl.com's scoring-assignments form: pulls
real NFL play-by-play from [nflverse](https://github.com/nflverse/nflverse-data),
matches players to your assigned teams' rosters, and fills in the form.

Verified end-to-end against the live site (ARI, week 1, 2026) — logged in,
filled every position tab, and confirmed on a fresh reopen that every value
persisted correctly, including Defense's yards-allowed and the computed
point totals matching the published scoring rules exactly.

> **Maintenance rule:** every fix or change is logged as a detailed GitHub
> pull request (what changed and why, how it was verified), and this README is
> updated in the same change: the relevant section, plus a numbered item under
> "Corrections made after going live" for bug fixes.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then edit .env with your own FLFFL username/password
```

`.env` is gitignored and is only ever read locally by `main.py` — it's never
sent anywhere else.

## Usage

Run these from the project folder. macOS has no bare `python` command, and
the dependencies live in `.venv`, so either activate it first
(`source .venv/bin/activate`) or replace `python` below with `.venv/bin/python`.

```bash
python main.py                              # dry run for the CURRENT week (auto-detected), changes nothing
python main.py --live                       # actually fills the form and submits, current week
python main.py --week 1                     # dry run for a specific week
python main.py --week 1 --live --headed     # live, showing the browser so you can watch
python main.py --week 1 --teams ARI,BUF     # only specific teams instead of all assigned ones
```

`--week` defaults to today's date mapped onto the season schedule
(`flffl_scorer/config.py`'s `resolve_current_week`). The week rolls over at
**midnight Wednesday**, not Tuesday, so Monday Night Football cleanup runs
on Tuesday still target the week that just played. Update
`SEASON_WEEK1_START` there at the start of each new season.

## Scheduled runs

A launchd job (`com.flffl-scorer.weekly`, installed at
`~/Library/LaunchAgents/com.flffl-scorer.weekly.plist`) runs `run_scheduled.sh`
— which calls `main.py --live --refresh-stats` for the auto-detected current
week — **hourly, on the hour, in three windows** (system local timezone; this
Mac is set to America/Chicago, so that's Central time), 32 runs a week:

- **Thursday 10 PM → Friday 6 AM** (Thursday Night Football)
- **Sunday 4 PM → Monday 6 AM** (Sunday games)
- **Monday 11 PM → Tuesday 6 AM** (Monday Night Football)

Both endpoints are inclusive (e.g. the last Friday run is 6:00 AM). The
windows exist to approximate live scoring: FLFFL only enables a team's
"Enter Stats" button once its game is final, so each hourly run picks up
games as they finish. Runs are cheap to repeat: games whose "Enter Stats"
button is still disabled are skipped, and games already filled with
unchanged data are skipped too (see below). (Earlier schedules had
Tuesday/Friday 8 AM safety-net runs. The Tuesday ones never actually worked: the old Tuesday-rollover week logic made them
target the *next* week and exit with "No play-by-play rows found". The last
run in each window is now 6 AM, so a game whose data publishes later than
that waits for the next window.)

### Repeat runs: skip-unchanged and de-duplicated notifications

Because the same finished games are seen hourly, live runs keep a small
record in `logs/run_state.json` (live runs only — dry runs never read or
write it):

- **Skip-unchanged.** A team is skipped when its computed stats and the
  program's own source code are identical to the last time it was filled
  (`flffl_scorer/state.py`). This stops hourly re-submissions and, importantly,
  means anything you hand-enter on the site (e.g. points for a weird play)
  is left alone until that team's data actually changes. A code change
  (e.g. a stat-logic fix) forces everyone to be re-filled once. Pass
  `--force` to re-fill regardless.
- **One alert per event.** Notifications fire only for something new: a team
  newly scored or whose stats changed, a weird play not already alerted, or a
  team newly flagged "data not ready". A routine run with nothing new is
  silent. Every notification is also echoed into `logs/scheduled_runs.log`
  as a `[notification]` line, so the log shows what was alerted. Failures
  still alert every run, since a persistent failure (e.g. the network is
  down overnight) is worth knowing about.
- Dry runs no longer send notifications.

Requirements: the Mac must be powered on and the user logged in (a GUI
LaunchAgent doesn't run at the login window or while fully shut down — if
the Mac was asleep, launchd runs the missed job as soon as it wakes; if it
was off, as soon as it's turned back on).

Every run writes to `logs/scheduled_runs.log` (a running history) in
addition to its own timestamped JSON audit log in `logs/`. A macOS
notification fires for new events and failures (see above;
`main.py`'s `notify_macos`) so you don't have to go check the terminal —
but the log is still the source of truth.

```bash
# Check it's loaded / see next scheduled run info:
launchctl print gui/$(id -u)/com.flffl-scorer.weekly

# Stop it:
launchctl bootout gui/$(id -u)/com.flffl-scorer.weekly

# Re-enable it after stopping:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.flffl-scorer.weekly.plist

# Trigger a run right now (for testing — this is a REAL --live run):
launchctl kickstart -k gui/$(id -u)/com.flffl-scorer.weekly
```

Every run — dry or live — writes a full audit log to `logs/` with exactly
what was computed and (on a live run) entered for every player, tab by tab.

## How it works

1. Downloads `play_by_play_<season>.csv` from nflverse (cached in
   `data_cache/`, refreshed automatically if the requested week isn't in the
   cached copy yet).
2. Computes per-player passing/rushing/receiving/kicking stats (including
   TDs) and per-team defensive stats directly from the raw plays (see
   `flffl_scorer/stats.py` — every formula has a comment explaining where it
   came from, mostly flffl.com's own published scoring rules page).
3. Matches FLFFL's "Last, First" roster names to nflverse's "F.Last" names
   (`flffl_scorer/name_match.py`), scoped per-team so a last-name match is
   almost always unique. A player with no matching plays that week (DNP) is
   left blank rather than filled with zeros.
4. Logs into flffl.com, opens each assigned team's "enter stats" popup, and
   for each position tab fills in the raw counting stats — including the
   game score box at the top of every tab, which the site requires before it
   will correctly save the Defense section's data (its "Total" points-allowed
   field reads from that, not from anything entered on the Defense tab
   itself). Derived fields the page computes itself (Y/Carry, Y/Rec, FG%,
   Net Pts) are left alone so the site's own JavaScript calculates them.
5. Flags "weird plays" the form has no field for — see below.

## Weird plays

Some scoring plays don't fit FLFFL's per-player form at all: a punt/kickoff
return TD by an individual player (there's no return-TD field on their
Rushing/Receiving row — only the team Defense tab has a Spec TD field, which
doesn't credit the individual returner), or a non-QB throwing a pass (the
RB/WR forms have no Passing section). `flffl_scorer/stats.py`'s
`find_unaccounted_touchdowns` and `find_non_qb_passers` catch these
generically — any touchdown not explained by a normal pass/rush/def/special
TD, or any pass attempt by someone not on the Quarterbacks tab — rather than
trying to enumerate every possible trick play.

When a run finds one, it prints `⚠️ WEIRD PLAY` prominently to the console
with the player, play type, yardage, and (for touchdowns) the full play
description, and adds it under `"weird_plays"` in that team's audit log
entry. Nothing is auto-filled for these — they're flagged so you can
calculate the right point value and enter it manually / message the
commissioner, same as your normal workflow.

The same `weird_plays` list also catches **ambiguous name matches**: if two
different FLFFL roster entries ever match to the same real player (the
`type` is `"ambiguous_name_match"`), neither gets filled and both are
flagged instead of guessing. This exists because of a real incident — see
below.

## Corrections made after going live (week 1)

Two bugs were found by cross-checking against nfl.com's official box score
after week 1's numbers looked off, both now fixed and covered by the notes
in `stats.py`/`name_match.py`:

1. **Attempts/carries/yards were wrong for nearly every QB.** The code
   excluded QB kneels entirely and didn't exclude sacks from pass attempts.
   nfl.com's official box score does the opposite of both: kneels count as
   rushing attempts (with their usual -1 yard), and sacks don't count
   toward passing attempts. Verified against Lamar Jackson's actual line
   (17/25, 324 yds; 7 car, 40 yds) after the fix.
2. **A worse one**: `name_match.match_player` returned the only same-last-name
   candidate on a team without checking the first name matched at all. This
   wrote a real teammate's entire stat line onto a different (genuinely DNP)
   player's row — confirmed for 3 players in week 1 (Kyle Allen got Josh
   Allen's line, Shedeur Sanders got an unrelated player's stats, Arian
   Smith got Geno Smith's rushing line). The first-initial check now always
   runs, and a second bug in `parse_flffl_name` — it didn't handle FLFFL's
   "Last, Jr., First" comma-separated suffix format, only "Last Jr., First"
   — is fixed too. The `ambiguous_name_match` weird-play check above is the
   general safety net so a future name-format surprise gets flagged instead
   of silently repeating this.
3. **The Monday scheduled run silently missed Monday Night Football.**
   `stats.download_pbp` only re-downloads the play-by-play file if the local
   cache is entirely missing — it never expires. The Monday 11pm run reused
   a copy cached earlier that day, before DEN@KC had finished, and both
   teams got submitted with essentially no data (safe — nobody got wrong
   stats, but 17 real players stayed incorrectly blank until manually
   caught up the next day). Fixed two ways: `run_scheduled.sh` now always
   passes `--refresh-stats` to force a fresh download every scheduled run,
   and `main.py` now separately detects "FLFFL shows this game final but we
   have zero play data for this team" as its own case — skipping that team
   and flagging it (with a notification) for a manual follow-up, instead of
   quietly submitting blanks for a team that obviously played.

4. **Week guard (added week 2, preventive).** FLFFL keeps last week's form
   enabled for a team until its next game is final, so on early-week runs
   (e.g. Friday) 15 teams had enabled week-1 forms while we held week-2
   data. They were only skipped because that data happened to be empty.
   `main.py` now reads the week from the popup header ("Quarterbacks - Week
   N", via `site.get_popup_week`) and refuses to fill any team whose form
   isn't for the week being scored (also refuses if the week can't be read),
   reporting `WEEK MISMATCH`. Side effect: you can't score a past week
   while the site is showing a newer one — that's intentional.

5. **"-Pick/Fum 6" and "-Safety" take counts, not points (fixed week 4).**
   The Defense tab's `-Pick/Fum 6` and `-Safety` boxes are multiplied by 6
   and 2 by the site's own JS (`CalculateOppDefPts`), but the code was
   entering the points (6 per pick/fumble-6, 2 per safety), so those events
   were subtracted from points allowed 6x / 2x too hard. `stats.py` now
   reports `opp_pick_fum6` / `opp_safety` as event counts (1 each). Weeks
   1-3 were deliberately NOT re-entered; the fix applies from week 4 on.

6. **"Fum Rec" now means the opponent's fumbles lost (fixed week 4).** It
   used to count any play where our team "recovered" something, which
   wrongly credited a blocked-field-goal recovery (DEN) and missed
   special-teams fumbles (a punt muff recovered by the kicking team). Per
   FLFFL's scoring, a defense only gets credit for a fumble the opponent
   fumbled and the defense recovered, so `stats.defense_stats` now counts
   each fumble slot where the opponent fumbled and we recovered, on any play
   type. Matches nfl.com's "fumbles lost" and agrees with nflverse's own
   `fumble_lost` flag for every game in weeks 1-4. Weeks 1-3 were not
   re-entered.

7. **Lateral plays gave the first receiver the whole play's yardage (fixed
   week 4).** On a catch followed by a lateral, `yards_gained` is the full
   play, so e.g. Schultz (HOU) was scored 28 receiving yards instead of 9.
   nfl.com credits the first receiver only up to the lateral, and gives the
   lateral recipient the rest as *receiving* yards with 0 receptions (plus the
   TD, if the lateral scored one); the passer keeps the full yards. Receiving
   and rushing yards now use nflverse's per-player columns, and lateral plays
   are flagged as weird plays. Verified against nfl.com for HOU (Schultz,
   Montgomery, Stroud) and SF (Evans, Samuel, Purdy). Past plays that were
   entered wrongly (not re-entered): wk1 BUF Coleman +10 yds / Shakir -10 yds;
   wk3 SF Evans +80 yds and a TD / Samuel -80 yds and a TD; wk4 HOU Schultz +19 yds /
   Montgomery -19 yds is corrected by re-running week 4. A *rushing* lateral
   (none seen yet) is flagged but not credited to the second runner, because
   nfl.com's convention for it hasn't been checked.

8. **Yardage-attribution guard (added week 4, after the week-3 Samuel
   lateral touchdown was mis-scored).** Samuel's 80-yard lateral TD was
   entered as 0 yds / 0 TD while Evans, who made the catch, got 114 yds and 2
   TDs (nfl.com: Samuel 0 rec / 80 yds / 1 TD; Evans 3 rec / 34 yds / 1 TD) —
   the lateral bug in item 7, whose week-3 entry predated the fix. Team-level
   totals can't catch this class of bug (the play's yards still land on
   *someone*, so they balance; a reconciliation of receivers vs passers was
   tried and proved useless for it). `stats.find_yardage_anomalies` checks each
   completed pass / run instead: the credited player's own yards (plus a
   recognized lateral recipient's) must add up to the play's yards, otherwise
   it's flagged as a `yardage_mismatch` weird play. Verified: 0 false alarms
   across weeks 1-4, and it flags all 4 lateral plays when the lateral flag is
   hidden to simulate nflverse missing one. Lateral plays themselves are also
   always flagged. This only catches yardage that doesn't add up; an
   independent cross-check of every player line against nfl.com's box score
   would catch more, but isn't built.
   (The week-3 entries were reconciled by hand on the site.)
   FLFFL's form has no week selector (only Team/position/Opp are submitted;
   it always shows a team's latest game), so a past week can't be re-opened
   and corrected by this tool.

If you're ever unsure whether a past week's numbers are trustworthy,
cross-check a player or two against nfl.com's box score (Game Center →
STATS tab) the way this was caught.

## Known limitations

- **Punt/kickoff return TD scored *against* your team's special teams**
  isn't backed out of `opp_total_pts` (only turnover-return TDs and
  safeties are, per the "-Pick/Fum 6" field). Rare; the weird-play detector
  above will catch it on the *scoring* team's side, but the conceding
  team's Points/Yards Allowed won't automatically exclude it — check by
  hand if it happens.
- **Two-point conversions**: FLFFL credits every offensive player involved
  (passer *and* rusher/receiver). The QB's single "2 Pt" box is filled with
  passing + rushing 2pt conversions combined; RB/WR's with
  rushing + receiving. This matches the rules page's wording ("all
  offensive players involved... receive 2 points") but hasn't been checked
  against a real 2pt play yet.
- **Kicker 2pt / fake-kick pass-run field** ("Pass/Run" under the Kickers
  tab's TD section) isn't automated at all (very rare) — left blank always.
- **Roster staleness**: if FLFFL's listed player for a slot (e.g. a team's
  kicker) doesn't match who actually played, that player correctly shows as
  DNP — this is a roster/depth-chart issue on FLFFL's side, not something
  this tool can fix.

## Files

- `main.py` — CLI entry point
- `flffl_scorer/stats.py` — nflverse data → stat lines, weird-play detection
- `flffl_scorer/name_match.py` — FLFFL name ↔ nflverse name matching
- `flffl_scorer/site.py` — Playwright browser automation
- `flffl_scorer/config.py` — team codes, season schedule / current-week detection
- `run_scheduled.sh` — wrapper the launchd job runs (see "Scheduled runs")
- `~/Library/LaunchAgents/com.flffl-scorer.weekly.plist` — the launchd job itself
