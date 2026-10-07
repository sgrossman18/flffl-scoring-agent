"""Playwright automation for flffl.com's scoring-assignments form.

The site renders each position tab as a sequence of 2-row blocks: a row of
plain-text labels ("INT", "COMP", "ATT", "YDS", ...) immediately followed by
a row with one <input> per label, in the same left-to-right order (confirmed
from the live Defense tab's HTML). This module discovers that pairing at
runtime via JS instead of hardcoding guessed `name=` attributes, which we
were not able to fully verify for every tab ahead of time — see README
"Known limitations" before trusting this against a live game.

Fields the site visibly computes itself from other inputs (Y/Carry, Y/Rec,
FG%, Net Pts) are intentionally left untouched; we only fill counting stats
and let the page's own onkeyup handlers derive the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from playwright.sync_api import Page, sync_playwright

from . import config

# --- label -> stat-dict key, scoped by the sub-table's header text ---
# Confirmed against the live site's real HTML/JS (see debug_dump.py); the
# accessibility-tree-only pass during initial development had missed the
# TD columns entirely and misread the FG yardage boxes as distance buckets
# rather than per-kick distance slots.
LABEL_MAPS = {
    "Passing": {"TD": "pass_td", "INT": "int", "COMP": "comp", "ATT": "att", "YDS": "pass_yds"},
    "Rushing": {"TD": "rush_td", "Carries": "carries", "YDS": "rush_yds"},
    "Receiving": {"TD": "rec_td", "Rec": "rec", "YDS": "rec_yds"},
    "Kicking": {
        "XP made": "xp_made", "XP missed": "xp_missed", "XP blocked": "xp_blocked",
        "FG made": "fg_made", "FG attempts": "fg_att",
    },
    # Each FG-N box is a per-kick slot (this kicker's Nth made FG) taking a
    # raw yardage; the site computes the point tier itself.
    "Yardage Points": {f"FG-{i+1}": f"fg_dist_{i}" for i in range(10)},
    "Defensive Points": {
        "Def TD": "def_td", "Spec TD": "spec_td", "Safety": "safety",
        "Int": "interceptions", "Fum Rec": "fum_rec", "Sack": "sack",
    },
    # "Total" (opponent's points) is disabled/site-managed — confirmed live,
    # it isn't something scorers fill in (presumably populated from wherever
    # the site sources final scores). Not mapped; the discovery JS also
    # skips disabled inputs entirely so it would never reach here anyway.
    "Points/Yards Allowed": {
        "-Pick/Fum 6": "opp_pick_fum6",
        "-Safety": "opp_safety", "Rush": "rush_yds_allowed", "Pass": "pass_yds_allowed_net",
    },
    "Misc": {"2 Pt": "two_pt"},  # combined 2pt value computed by caller before filling
}

# JS run in-page: walks the page in true document order so a section header
# (<span class="small">Passing</span>, which is a SIBLING of the label/input
# tables, not a descendant of any one <tr>) is correctly attributed to every
# label row that follows it until the next header. Pairs each "labels row"
# with the very next sibling <tr> ("inputs row"), tagging each <input> with
# data-flffl-tag so Python can target it with an ordinary Playwright locator
# afterward.
_DISCOVER_JS = r"""
() => {
    const walk = document.querySelectorAll('span.small, strong, tr');
    let currentSection = null;
    let currentPlayer = null;
    const found = [];
    let tagCounter = 0;

    for (const el of walk) {
        if (el.tagName === 'SPAN') {
            const t = el.textContent.trim();
            if (t) currentSection = t;
            continue;
        }
        if (el.tagName === 'STRONG') {
            const t = el.textContent.trim();
            if (t) currentPlayer = t;
            continue;
        }

        const row = el;
        const cells = Array.from(row.children).filter(c => c.tagName === 'TD');
        if (!cells.length) continue;
        const cellTexts = cells.map(c => c.textContent.trim());
        const looksLikeLabels = cellTexts.every(t => t.length > 0 && t.length < 20)
            && !row.querySelector('input');
        if (!looksLikeLabels) continue;

        let nextRow = row.nextElementSibling;
        while (nextRow && nextRow.tagName !== 'TR') nextRow = nextRow.nextElementSibling;
        if (!nextRow) continue;
        const nextCells = Array.from(nextRow.children).filter(c => c.tagName === 'TD');
        if (nextCells.length !== cells.length) continue;

        for (let j = 0; j < cells.length; j++) {
            const input = nextCells[j].querySelector('input[type="text"]:not([disabled])');
            if (!input) continue;
            tagCounter++;
            const tag = 'flffl-input-' + tagCounter;
            input.setAttribute('data-flffl-tag', tag);
            found.push({
                tag,
                player: currentPlayer,
                section: currentSection,
                label: cellTexts[j],
            });
        }
    }
    return found;
}
"""


@dataclass
class FieldTarget:
    tag: str
    player: str
    section: str
    label: str


def discover_fields(page: Page) -> list[FieldTarget]:
    raw = page.evaluate(_DISCOVER_JS)
    return [FieldTarget(**r) for r in raw]


def fill_field(page: Page, tag: str, value) -> bool:
    """Returns False (and does nothing) if the field turned out to be
    disabled — belt-and-suspenders on top of discover_fields already
    excluding disabled inputs, so a surprise one can't hang the run."""
    if value is None:
        return False
    locator = page.locator(f'[data-flffl-tag="{tag}"]')
    if locator.is_disabled():
        return False
    locator.fill(str(value))
    locator.dispatch_event("keyup")
    locator.dispatch_event("change")
    return True


def login(page: Page, username: str, password: str) -> None:
    page.goto(f"{config.FLFFL_BASE_URL}/index.php")
    page.fill("#UserName", username)
    page.fill("#Password", password)
    page.click("#loginbutton")
    page.wait_for_load_state("networkidle")


def get_assigned_teams(page: Page) -> list[dict]:
    """Return [{'team': 'ARI', 'opp': 'LAC', ...}] for every team on the
    scoring-assignments page, in row order."""
    page.goto(config.SCORING_ASSIGNMENTS_URL)
    page.wait_for_load_state("networkidle")
    rows = page.evaluate(
        """
        () => {
            const table = Array.from(document.querySelectorAll('table'))
                .find(t => t.textContent.includes('Scoring'));
            if (!table) return [];
            return Array.from(table.querySelectorAll('tr')).slice(1).map((tr, idx) => {
                const cells = Array.from(tr.querySelectorAll('td'));
                if (!cells.length) return null;
                const team = cells[0].textContent.trim();
                return team ? { team, rowIndex: idx } : null;
            }).filter(Boolean);
        }
        """
    )
    return rows


class NoScoringControl(Exception):
    """Raised when a team's row has no 'enter stats' control yet (e.g. their
    game hasn't been played/completed, so the site has nothing to score)."""


_CLICKABLE_SELECTOR = "a, input[type=button]:not([disabled]), input[type=submit]:not([disabled]), button:not([disabled])"

_HAS_CLICKABLE_JS = f"""
(team) => {{
    const table = Array.from(document.querySelectorAll('table'))
        .find(t => t.textContent.includes('Scoring'));
    const row = Array.from(table.querySelectorAll('tr'))
        .find(tr => tr.querySelector('td') && tr.querySelector('td').textContent.trim() === team);
    if (!row) return false;
    return !!row.querySelector('{_CLICKABLE_SELECTOR}');
}}
"""


def open_team_popup(page: Page, team: str):
    """Click the 'enter stats' control for `team`'s row and return the popup Page.

    Raises NoScoringControl if the row has no enabled control yet (their
    game hasn't happened/finished — the site disables the button in that
    case rather than omitting it)."""
    if not page.evaluate(_HAS_CLICKABLE_JS, team):
        raise NoScoringControl(f"No enabled 'Enter Stats' button for {team} yet (game not played/completed?)")

    with page.context.expect_page() as popup_info:
        page.evaluate(
            f"""
            (team) => {{
                const table = Array.from(document.querySelectorAll('table'))
                    .find(t => t.textContent.includes('Scoring'));
                const row = Array.from(table.querySelectorAll('tr'))
                    .find(tr => tr.querySelector('td') && tr.querySelector('td').textContent.trim() === team);
                const clickable = row.querySelector('{_CLICKABLE_SELECTOR}');
                clickable.click();
            }}
            """,
            team,
        )
    popup = popup_info.value
    popup.wait_for_load_state("networkidle")
    return popup


def get_popup_week(popup: Page) -> int | None:
    """Week number shown in the popup's header ("Quarterbacks - Week 2"), or
    None if it can't be read. Used to refuse to fill a form for a different
    week than the one being scored (FLFFL can keep showing last week's form
    for a team until its next game is final)."""
    header = popup.locator("#position")
    if header.count() == 0:
        return None
    m = re.search(r"Week\s+(\d+)", header.first.inner_text())
    return int(m.group(1)) if m else None


def click_position_tab(popup: Page, tab_label: str) -> None:
    popup.click(f'input[type="submit"][value="{tab_label}"]')
    popup.wait_for_load_state("networkidle")
    popup.wait_for_timeout(300)


def fill_game_score(popup: Page, team_score, opp_score) -> None:
    """Fill the "TEAM [score] at OPP [score]" boxes shown at the top of
    every position tab (id="Score" / id="OpponentScore", confirmed from the
    live HTML). This is what feeds the Defense tab's disabled "Total"
    (opponent points) field — it isn't entered on the Defense tab itself.
    Safe/idempotent to call on every tab since the value is always the
    real final score, not a per-tab guess."""
    for value, selector in ((team_score, "#Score"), (opp_score, "#OpponentScore")):
        if value is None:
            continue
        locator = popup.locator(selector)
        if locator.count() == 0 or locator.is_disabled():
            continue
        locator.fill(str(value))
        locator.dispatch_event("keyup")
        locator.dispatch_event("change")


def click_review_and_submit(popup: Page, submit: bool) -> str:
    """Visit Review and return its summary text.

    Confirmed from the live site: there is no separate Save/Submit button —
    each position tab's own POST (triggered by clicking to the next tab)
    already persists that section server-side, and Review is just a
    read-only summary with a Close button (window.close(), no extra save).
    `submit` only gates whether stat values were actually typed into fields
    earlier in the run (see main.py) — nothing further is needed here.
    """
    popup.click('input[type="submit"][value="Review"]')
    popup.wait_for_load_state("networkidle")
    popup.wait_for_timeout(300)
    return popup.inner_text("body")
