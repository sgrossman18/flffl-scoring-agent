#!/bin/bash
# Fired by launchd EVERY HOUR (see launchd/com.flffl-scorer.weekly.plist.template);
# it only does anything during the scoring windows, judged in Central time by
# `main.py --window-check` (config.RUN_HOURS) so travelling or a changed Mac
# timezone doesn't shift the schedule. Inside a window it auto-detects the
# current week and fills every team on the scoring page live. Output goes to
# logs/scheduled_runs.log in addition to the per-run JSON audit log main.py
# always writes. Outside a window it exits silently and logs nothing.
set -uo pipefail

cd "$(dirname "$0")"
mkdir -p logs

# Exit code 10 = outside the windows: nothing to do. Any other non-zero code
# means the check itself broke (e.g. the venv is missing) - fall through and
# run, so the failure shows up in the log and as a notification.
.venv/bin/python main.py --window-check >/dev/null 2>&1
if [ $? -eq 10 ]; then
    exit 0
fi

{
    echo "===== $(date) ====="
    # Always force a fresh download: the play-by-play cache never expires on
    # its own (see stats.py), so a scheduled run that reused an
    # already-cached file from earlier in the day would miss a game that
    # finished since then — exactly what happened to the first Monday run.
    .venv/bin/python main.py --live --refresh-stats
    echo "----- exit code $? -----"
} >> logs/scheduled_runs.log 2>&1
