"""Detect players whose form values were changed by hand since this script
last wrote them, so a later run doesn't overwrite a manual score.

FLFFL shows no "last updated" time, so instead of timestamps we compare the
form's current values with a record of what we last wrote (kept in
logs/run_state.json): a mismatch means someone else edited the player.
"""

from __future__ import annotations


def _norm(v) -> str:
    v = ("" if v is None else str(v)).strip()
    try:
        f = float(v)
    except ValueError:
        return v
    return str(int(f)) if f == int(f) else str(f)


def _blank(v) -> bool:
    return _norm(v) in ("", "0")


def field_key(section: str, label: str) -> str:
    return f"{section}|{label}"


def classify(current: dict[str, str], written: dict[str, str] | None) -> tuple[str, list[dict]]:
    """Return (status, differences) for one player on one tab.

    `current` is every field the form holds for the player; `written` is what
    we last wrote (None if we have no record of writing this player).
      "clean"      - nothing there yet, or exactly what we wrote
      "edited"     - we have a record and the form no longer matches it
      "unrecorded" - no record, but the form already holds values (somebody
                     entered them, or an earlier run died before recording)
    A blank and a "0" count as the same, since the form shows untouched
    stat boxes as either.
    """
    diffs = []
    if written is None:
        for k, v in current.items():
            if not _blank(v):
                diffs.append({"field": k, "form": _norm(v), "we_wrote": None})
        return ("unrecorded" if diffs else "clean"), diffs

    for k, v in written.items():
        if _norm(current.get(k)) != _norm(v) and not (_blank(current.get(k)) and _blank(v)):
            diffs.append({"field": k, "form": _norm(current.get(k)), "we_wrote": _norm(v)})
    for k, v in current.items():
        if k not in written and not _blank(v):
            diffs.append({"field": k, "form": _norm(v), "we_wrote": None})
    return ("edited" if diffs else "clean"), diffs
