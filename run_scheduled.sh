#!/bin/bash
# Run by launchd hourly in three windows (Thu 10pm-Fri 6am, Sun 4pm-Mon 6am,
# Mon 11pm-Tue 6am) Central — see com.flffl-scorer.weekly.plist. Auto-detects the current
# week and fills every assigned team live. Output goes to
# logs/scheduled_runs.log in addition to the per-run JSON audit log
# main.py always writes.
set -uo pipefail

cd "$(dirname "$0")"
mkdir -p logs

{
    echo "===== $(date) ====="
    # Always force a fresh download: the play-by-play cache never expires on
    # its own (see stats.py), so a scheduled run that reused an
    # already-cached file from earlier in the day would miss a game that
    # finished since then — exactly what happened to the first Monday run.
    .venv/bin/python main.py --live --refresh-stats
    echo "----- exit code $? -----"
} >> logs/scheduled_runs.log 2>&1
