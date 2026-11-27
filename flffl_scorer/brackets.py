"""Playoff brackets for the five FLFFL bowls.

Each bowl is a fixed 16-team bracket (no reseeding) that decides every place
from 1st to 16th, the same format as the hand-made 2025 brackets:

- Week 1 of the playoffs pairs seeds 1v16, 8v9, 4v13, 5v12, 3v14, 6v11, 7v10,
  2v15, top to bottom.
- Every game's winner moves on in its group and its loser drops into the
  group playing for the places just below. The 16-team group's first-round
  losers play for 9th-16th, its second-round losers for 5th-8th, its semifinal
  losers for 3rd. Those groups split the same way (9th-16th's first-round
  losers play for 13th-16th, and so on) until every game is a single game for
  two adjacent places.
- Losers meet in bracket order: the losers of 1v16 and 8v9 play each other,
  just as the winners do.

That is 8 games a week for the 4 playoff weeks, 32 per bowl.

Results are optional: with none, only the first week's matchups are filled in
and later lines stay blank, as on a freshly posted bracket.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

from . import config

FIRST_ROUND_SEED_ORDER = [1, 16, 8, 9, 4, 13, 5, 12, 3, 14, 6, 11, 7, 10, 2, 15]
BOWL_SIZE = 16


@dataclass(frozen=True)
class Team:
    seed: int  # seed within the bowl, 1-16
    rank: int  # overall league rank, 1-80
    name: str


@dataclass
class Game:
    week: int
    a: Team | None
    b: Team | None
    score_a: float | None = None
    score_b: float | None = None
    winner: Team | None = None
    loser: Team | None = None


@dataclass
class Group:
    """Teams playing for places first_place .. first_place + len(entrants) - 1,
    starting in start_week. rounds[k] holds the k-th week's games; children
    are the groups that this group's losers drop into, one per round."""

    first_place: int
    start_week: int
    entrants: list[Team | None]
    rounds: list[list[Game]] = field(default_factory=list)
    children: list["Group"] = field(default_factory=list)
    place_winner: Team | None = None  # who finishes in first_place, once decided

    @property
    def size(self) -> int:
        return len(self.entrants)

    @property
    def last_place(self) -> int:
        return self.first_place + self.size - 1

    def all_groups(self):
        yield self
        for child in self.children:
            yield from child.all_groups()


@dataclass
class Bowl:
    key: str
    title: str
    seeds: list[Team]
    root: Group


class ResultsError(Exception):
    pass


# --- rankings.php -----------------------------------------------------------

# Run in the rankings page: every row of the table whose header has
# "Rank" and "Bowl", as plain cell text.
_RANKINGS_JS = """
() => {
  const table = [...document.querySelectorAll('table')].find(t => {
    const head = t.querySelector('tr');
    return head && /\\bRank\\b/.test(head.innerText) && /\\bBowl\\b/.test(head.innerText);
  });
  if (!table) return null;
  const rows = [...table.querySelectorAll('tr')];
  const header = [...rows[0].querySelectorAll('th,td')].map(c => c.innerText.trim());
  return {header, rows: rows.slice(1).map(r => [...r.querySelectorAll('td')].map(c => c.innerText.trim()))};
}
"""


def read_rankings(page) -> list[dict]:
    """Parse the rankings table on an already-loaded rankings.php page into
    [{'rank': 1, 'name': 'TEXAS POKES', 'bowl': 'Fingerlakes', ...}] in rank order."""
    data = page.evaluate(_RANKINGS_JS)
    if not data:
        raise ValueError("no rankings table (with Rank and Bowl columns) on this page")
    header = [h.lower() for h in data["header"]]

    def col(prefix: str) -> int:
        for i, h in enumerate(header):
            if h.startswith(prefix):
                return i
        raise ValueError(f"rankings table has no {prefix!r} column (columns: {data['header']})")

    i_rank, i_name, i_bowl = col("rank"), col("team"), col("bowl")
    out = []
    for cells in data["rows"]:
        if len(cells) <= max(i_rank, i_name, i_bowl) or not cells[i_rank].isdigit():
            continue
        out.append({"rank": int(cells[i_rank]), "name": cells[i_name], "bowl": cells[i_bowl],
                    "cells": dict(zip(data["header"], cells))})
    out.sort(key=lambda r: r["rank"])
    return out


def seeds_by_bowl(rankings: list[dict]) -> dict[str, list[Team]]:
    """Group ranked teams by the table's Bowl column, seeding 1-16 within each
    bowl in rank order, and check each bowl's teams hold consecutive ranks."""
    known = {k.lower(): k for k in config.BOWLS}
    grouped: dict[str, list[dict]] = {k: [] for k in config.BOWLS}
    for row in rankings:
        key = known.get(row["bowl"].strip().lower())
        if key is None:
            raise ValueError(f"rank {row['rank']} {row['name']!r} is in unknown bowl {row['bowl']!r}; "
                             f"expected one of {list(config.BOWLS)}")
        grouped[key].append(row)
    out = {}
    for key, rows in grouped.items():
        if len(rows) != BOWL_SIZE:
            raise ValueError(f"{key} bowl has {len(rows)} teams, expected {BOWL_SIZE}")
        ranks = [r["rank"] for r in rows]
        if ranks != list(range(ranks[0], ranks[0] + BOWL_SIZE)):
            raise ValueError(f"{key} bowl's ranks aren't consecutive: {ranks}")
        out[key] = [Team(seed=i + 1, rank=r["rank"], name=r["name"]) for i, r in enumerate(rows)]
    return out


# --- results ---------------------------------------------------------------

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class Results:
    """Playoff scores, keyed by week then team name (matched ignoring case and
    punctuation). JSON shape:

        {"scores": {"12": {"TEXAS POKES": 201, "MATT'S MILITIA": 188, ...}, ...},
         "tiebreak_winners": {"12": ["TEXAS POKES"]}}

    A tied game needs its winner listed under tiebreak_winners for that week.
    """

    def __init__(self, scores: dict | None = None, tiebreak_winners: dict | None = None):
        self.scores = {int(w): {_norm(n): float(s) for n, s in teams.items()}
                       for w, teams in (scores or {}).items()}
        self.tiebreak = {int(w): {_norm(n) for n in names} for w, names in (tiebreak_winners or {}).items()}

    @classmethod
    def load(cls, path: str) -> "Results":
        with open(path) as f:
            data = json.load(f)
        return cls(data.get("scores"), data.get("tiebreak_winners"))

    def score(self, week: int, team: Team) -> float | None:
        return self.scores.get(week, {}).get(_norm(team.name))

    def decide(self, game: Game) -> None:
        if game.a is None or game.b is None:
            return
        game.score_a, game.score_b = self.score(game.week, game.a), self.score(game.week, game.b)
        if game.score_a is None or game.score_b is None:
            return
        if game.score_a != game.score_b:
            a_wins = game.score_a > game.score_b
        else:
            listed = self.tiebreak.get(game.week, set())
            a_in, b_in = _norm(game.a.name) in listed, _norm(game.b.name) in listed
            if a_in == b_in:
                raise ResultsError(f"week {game.week}: {game.a.name} and {game.b.name} tied "
                                   f"{game.score_a:g}-{game.score_b:g}; list the winner under "
                                   f"tiebreak_winners[\"{game.week}\"]")
            a_wins = a_in
        game.winner, game.loser = (game.a, game.b) if a_wins else (game.b, game.a)


# --- building --------------------------------------------------------------

def build_group(entrants: list[Team | None], first_place: int, start_week: int, results: Results) -> Group:
    group = Group(first_place=first_place, start_week=start_week, entrants=list(entrants))
    current = list(entrants)
    week = start_week
    while len(current) > 1:
        games = [Game(week, current[i], current[i + 1]) for i in range(0, len(current), 2)]
        for g in games:
            results.decide(g)
        group.rounds.append(games)
        losers = [g.loser for g in games]
        group.children.append(build_group(losers, first_place + len(games), week + 1, results))
        current = [g.winner for g in games]
        week += 1
    group.place_winner = current[0]
    return group


def build_bowl(key: str, seeds: list[Team], results: Results | None = None) -> Bowl:
    by_seed = {t.seed: t for t in seeds}
    entrants = [by_seed[s] for s in FIRST_ROUND_SEED_ORDER]
    root = build_group(entrants, 1, config.PLAYOFF_FIRST_WEEK, results or Results())
    return Bowl(key=key, title=config.BOWLS[key], seeds=sorted(seeds, key=lambda t: t.seed), root=root)


# --- rendering -------------------------------------------------------------

def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _place_label(group: Group) -> str:
    if group.size == 2:
        return "Championship" if group.first_place == 1 else f"{ordinal(group.first_place)} place game"
    return "Championship" if group.first_place == 1 else \
        f"{ordinal(group.first_place)}–{ordinal(group.last_place)} place"


def _fmt_score(s: float | None) -> str:
    return "" if s is None else f"{s:g}"


class _Svg:
    def __init__(self):
        self.parts: list[str] = []

    def line(self, x1, y1, x2, y2, cls="ln"):
        self.parts.append(f'<line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')

    def text(self, x, y, s, cls, anchor="start", fit: float | None = None):
        fit_attr = f' data-fit="{fit:.1f}"' if fit else ""
        self.parts.append(f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}"{fit_attr}>'
                          f'{html.escape(s)}</text>')

    def team_line(self, x, y, w, team: Team | None, score: float | None, won: bool | None):
        """A horizontal line with the team's seed and name above it and its
        score at the right end."""
        self.line(x, y, x + w, y)
        if team is None:
            return
        score_s = _fmt_score(score)
        name_cls = "nm" + (" win" if won else " lost" if won is False else "")
        self.text(x + 2, y - 2.5, str(team.seed), "sd")
        self.text(x + 13, y - 2.5, team.name, name_cls, fit=w - 15 - (6 * len(score_s) + 4 if score_s else 0))
        if score_s:
            self.text(x + w - 2, y - 2.5, score_s, "sc" + (" win" if won else ""), anchor="end")


def draw_group(svg: _Svg, group: Group, x: float, y: float, col_w: float, gap: float,
               week_labels: bool = True) -> tuple[float, float]:
    """Draw `group` as a left-to-right tree with its top-left corner at (x, y):
    one column per round plus a final column for the place winner. `gap` is
    the vertical distance between first-round lines. Returns (width, height)."""
    header_h = 13
    svg.text(x, y + 9, _place_label(group), "gh")
    top = y + header_h + (22 if week_labels else 12)
    ys = [top + i * gap for i in range(group.size)]
    for k, games in enumerate(group.rounds):
        cx = x + k * col_w
        if week_labels:
            svg.text(cx + 2, top - 13, f"Week {games[0].week}", "wk")
        next_ys = []
        for j, g in enumerate(games):
            ya, yb = ys[2 * j], ys[2 * j + 1]
            decided = g.winner is not None
            svg.team_line(cx, ya, col_w, g.a, g.score_a, (g.winner is g.a) if decided else None)
            svg.team_line(cx, yb, col_w, g.b, g.score_b, (g.winner is g.b) if decided else None)
            svg.line(cx + col_w, ya, cx + col_w, yb)
            next_ys.append((ya + yb) / 2)
        ys = next_ys
    fx = x + len(group.rounds) * col_w
    svg.team_line(fx, ys[0], col_w, group.place_winner, None, None)
    label = "CHAMPION" if group.first_place == 1 else ordinal(group.first_place)
    svg.text(fx + 2, ys[0] + 9, label, "pl")
    width = (len(group.rounds) + 1) * col_w
    height = (top - y) + (group.size - 1) * gap + 10
    return width, height


def _groups_by_place(bowl: Bowl) -> dict[int, Group]:
    return {g.first_place: g for g in bowl.root.all_groups() if g.size >= 2}


def render_bowl_svg(bowl: Bowl, year: int, subtitle: str) -> str:
    W, H = 1000, 740
    svg = _Svg()
    svg.text(W / 2 - 70, 16, f"FLFFL {year}", "t1", anchor="middle")
    svg.text(W / 2 - 70, 34, bowl.title, "t2", anchor="middle")
    if subtitle:
        svg.text(W / 2 - 70, 47, subtitle, "t3", anchor="middle")

    g = _groups_by_place(bowl)
    # Championship tree and the 3rd-place game beside its final.
    draw_group(svg, g[1], 0, 58, 140, 21)
    draw_group(svg, g[3], 700, 350, 125, 21, week_labels=False)
    # Lower half: the other places, smaller.
    lower = 455
    draw_group(svg, g[9], 0, lower, 130, 18)
    draw_group(svg, g[13], 0, lower + 175, 120, 18)
    draw_group(svg, g[11], 385, lower + 175, 120, 18, week_labels=False)
    draw_group(svg, g[15], 385, lower + 235, 120, 18, week_labels=False)
    draw_group(svg, g[5], 560, lower, 135, 18)
    draw_group(svg, g[7], 560, lower + 100, 125, 18, week_labels=False)

    # Seeds table, top right.
    tx, ty, tw = W - 175, 58, 175
    svg.text(tx, ty + 9, "Seeds", "gh")
    for i, t in enumerate(bowl.seeds):
        ry = ty + 26 + i * 15
        svg.line(tx, ry + 3.5, tx + tw, ry + 3.5, "rule")
        svg.text(tx + 2, ry, str(t.seed), "sd")
        svg.text(tx + 16, ry, t.name, "nm", fit=tw - 52)
        svg.text(tx + tw - 2, ry, f"#{t.rank}", "sd", anchor="end")

    return (f'<svg class="page" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">'
            + "".join(svg.parts) + "</svg>")


_CSS = """
@page { size: letter landscape; margin: 0.35in; }
html, body { margin: 0; padding: 0; }
body { font-family: Helvetica, Arial, sans-serif; }
svg.page { width: 10.3in; height: 7.6in; display: block; page-break-after: always; }
svg.page:last-child { page-break-after: auto; }
.ln { stroke: #222; stroke-width: 0.9; }
.rule { stroke: #ccc; stroke-width: 0.6; }
text { fill: #111; }
.t1 { font-size: 15px; font-weight: 700; }
.t2 { font-size: 13px; font-weight: 700; letter-spacing: 0.3px; }
.t3 { font-size: 8.5px; fill: #666; }
.gh { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px; fill: #444; }
.wk { font-size: 7.5px; fill: #888; }
.sd { font-size: 7.5px; fill: #888; }
.nm { font-size: 8.5px; font-weight: 600; }
.nm.lost { fill: #888; font-weight: 400; }
.sc { font-size: 8px; fill: #555; }
.sc.win { fill: #111; font-weight: 700; }
.pl { font-size: 7.5px; font-weight: 700; fill: #444; }
"""

# Shrink any name wider than its line (data-fit, in px) so it never runs into
# the score or the next column.
_FIT_JS = """
() => {
  for (const t of document.querySelectorAll('text[data-fit]')) {
    const max = parseFloat(t.dataset.fit);
    if (t.getComputedTextLength() > max) {
      t.setAttribute('textLength', max);
      t.setAttribute('lengthAdjust', 'spacingAndGlyphs');
    }
  }
}
"""


def render_html(bowls: list[Bowl], year: int, subtitle: str) -> str:
    pages = "".join(render_bowl_svg(b, year, subtitle) for b in bowls)
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{pages}</body></html>"


def write_pdf(page, bowls: list[Bowl], year: int, subtitle: str, pdf_path: str, html_path: str | None = None) -> None:
    """Render the bowls (one landscape letter page each) with a Playwright page."""
    doc = render_html(bowls, year, subtitle)
    page.set_content(doc)
    page.evaluate(_FIT_JS)
    if html_path:
        with open(html_path, "w") as f:
            f.write(page.content())
    page.pdf(path=pdf_path, format="Letter", landscape=True, print_background=True,
             margin={"top": "0.35in", "bottom": "0.35in", "left": "0.35in", "right": "0.35in"})
