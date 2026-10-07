"""Small persistent record of what each live run has already done, so the
hourly scheduled runs can (a) skip teams whose data hasn't changed since they
were last filled and (b) only raise notifications for things they haven't
already told the user about.

Lives in logs/run_state.json (gitignored with the rest of logs/). Only live
runs read or write it; dry runs never touch it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import config

_ROOT = Path(__file__).resolve().parent.parent


def load() -> dict:
    try:
        with open(config.STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    tmp = config.STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, config.STATE_FILE)


def week_state(state: dict, season: int, week: int) -> dict:
    ws = state.setdefault(f"{season}-wk{week}", {})
    ws.setdefault("teams", {})
    ws.setdefault("alerted_weird", [])
    ws.setdefault("alerted_not_ready", [])
    return ws


def code_fingerprint() -> str:
    """Hash of the program's own source. A code change (say, a stat-logic fix)
    must force every team to be re-filled even if its raw data is identical,
    otherwise the old, wrong values would be skipped as "unchanged"."""
    h = hashlib.sha256()
    for path in [_ROOT / "main.py", *sorted((_ROOT / "flffl_scorer").glob("*.py"))]:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def data_fingerprint(offense: dict, kicking: dict, defense: dict) -> str:
    blob = json.dumps(
        {"offense": offense, "kicking": kicking, "defense": defense}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
