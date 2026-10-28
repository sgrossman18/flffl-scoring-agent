"""Match FLFFL roster names ("Harrison Jr., Marvin") to nflverse play-by-play
names ("M.Harrison"), scoped to one team's roster at a time so a last-name
match is almost always unique.
"""

from __future__ import annotations

import re

from . import config

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NICKNAME_RE = re.compile(r"^([^(]+?)\s*\(([^)]+)\)\s*$")


def parse_flffl_name(flffl_name: str) -> dict:
    """'Harrison Jr., Marvin' -> {'last': 'Harrison', 'first_candidates': ['Marvin']}
    'Washington, Jr., Mike' -> {'last': 'Washington', 'first_candidates': ['Mike']}
    'Knight, Bam (Zonovan)' -> {'last': 'Knight', 'first_candidates': ['Bam', 'Zonovan']}
    (the parenthetical form covers players FLFFL lists by nickname but who
    appear in pbp under their legal first name, e.g. Zonovan "Bam" Knight)

    FLFFL formats suffixes inconsistently — sometimes attached to the last
    name ("Harrison Jr., Marvin"), sometimes as their own comma segment
    ("Washington, Jr., Mike") — both are handled here.
    """
    if "," not in flffl_name:
        return {"last": flffl_name.strip(), "first_candidates": []}

    parts = [p.strip() for p in flffl_name.split(",")]

    last_tokens = parts[0].split()
    while last_tokens and last_tokens[-1].strip(".").lower() in _SUFFIXES:
        last_tokens.pop()
    last = " ".join(last_tokens)

    if len(parts) >= 3 and parts[1].strip(".").lower() in _SUFFIXES:
        first_part = parts[2]
    else:
        first_part = ",".join(parts[1:]).strip()

    m = _NICKNAME_RE.match(first_part)
    first_candidates = [m.group(1).strip(), m.group(2).strip()] if m else [first_part]

    return {"last": last, "first_candidates": first_candidates}


def match_player(flffl_name: str, pbp_names: list[str]) -> str | None:
    """Match one FLFFL roster name against pbp 'F.Last' names for the same team.

    Returns the matching pbp name, or None if no confident match is found
    (no stats that week — DNP — or a name this matcher can't parse).

    The first-initial check runs even when there's only one last-name
    candidate: a lone "Smith" in the pbp data must NOT be assumed to be
    whichever FLFFL roster player happens to share that last name — a real
    bug found live (Arian Smith, a WR with zero snaps, was matched to Geno
    Smith the QB's stat line because he was the only "Smith" who'd recorded
    a play that week). Zero or multiple initial-verified matches both
    return None rather than guessing.
    """
    alias = config.NAME_ALIASES.get(flffl_name)
    if alias is not None:
        # Explicit alias (a nickname the first-initial check can't see): it
        # names exactly one nflverse player, so either that player recorded
        # stats or this is a DNP. Never fall back to the heuristic.
        return alias if alias in pbp_names else None

    parsed = parse_flffl_name(flffl_name)
    last = parsed["last"].lower()
    if not last:
        return None

    candidates = [n for n in pbp_names if n.split(".", 1)[-1].lower() == last]
    if not candidates:
        return None

    matches = []
    for cand in candidates:
        initial = cand.split(".", 1)[0].lower()
        if any(fc.lower().startswith(initial) for fc in parsed["first_candidates"]):
            matches.append(cand)

    return matches[0] if len(matches) == 1 else None


def find_possible_nickname_mismatches(unmatched_flffl: list[str], unmatched_pbp: list[str]) -> list[tuple[str, str]]:
    """Pair up FLFFL roster names that matched nobody with nflverse players who
    recorded stats but matched no roster entry, when exactly one of each shares
    a last name — the signature of a nickname the first-initial check rejects
    (FLFFL "Ogletree, Drew" vs nflverse "A.Ogletree"). It can also be two
    different people, so these are only ever reported for a human to confirm
    (then add to config.NAME_ALIASES), never matched automatically.
    """
    pairs = []
    for f in unmatched_flffl:
        last = parse_flffl_name(f)["last"].lower()
        same_f = [x for x in unmatched_flffl if parse_flffl_name(x)["last"].lower() == last]
        same_p = [p for p in unmatched_pbp if p.split(".", 1)[-1].lower() == last]
        if last and len(same_f) == 1 and len(same_p) == 1:
            pairs.append((f, same_p[0]))
    return pairs
