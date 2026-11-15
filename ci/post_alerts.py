"""Post this run's notifications to the standing "FLFFL scoring alerts" GitHub
issue (the cloud replacement for the macOS notifications).

main.py, when running on GitHub Actions, appends each notification to
logs/notifications.jsonl. This script (the workflow's last step) turns them into
one comment, and adds a line if the scoring step failed. Nothing is posted for a
quiet dry run. The issue is assigned to / mentions the repo owner so GitHub's
mobile app and email notify them. Needs GH_TOKEN, RUN_URL, SCORE_OUTCOME,
IS_LIVE, GITHUB_REPOSITORY and GITHUB_REPOSITORY_OWNER in the environment.
"""

import json
import os
import subprocess
import sys

ISSUE_TITLE = "FLFFL scoring alerts"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    notes = []
    try:
        with open("logs/notifications.jsonl") as f:
            notes = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        pass

    failed = os.environ.get("SCORE_OUTCOME") == "failure"
    if not notes and not failed:
        print("No alerts to post.")
        return

    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    run_url = os.environ["RUN_URL"]
    lines = []
    for n in notes:
        lines.append(f"- **{n['title']}** — {n['message']}")
    if failed and not any("FAILED" in n["title"] for n in notes):
        lines.append("- **FLFFL scoring FAILED** — the run crashed before it could report why.")
    mode = "" if os.environ.get("IS_LIVE") == "true" else " (DRY RUN, nothing was written)"
    body = f"@{owner}{mode}\n\n" + "\n".join(lines) + f"\n\n[Run log]({run_url})"

    found = gh("issue", "list", "--state", "open", "--search", f'"{ISSUE_TITLE}" in:title',
               "--json", "number,title", "--jq", f'[.[] | select(.title == "{ISSUE_TITLE}")][0].number')
    number = found if found and found != "null" else ""
    if not number:
        out = gh("issue", "create", "--title", ISSUE_TITLE, "--assignee", owner, "--body",
                 "Alerts from the cloud scoring job: new weird plays, scored teams and failures. "
                 "Close this issue to stop them; the next alert opens a new one.")
        number = out.rstrip("/").rsplit("/", 1)[-1]
    gh("issue", "comment", number, "--body", body)
    print(f"Posted {len(lines)} alert(s) to issue #{number}.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"Could not post alerts: {e.stderr}", file=sys.stderr)
        sys.exit(1)
