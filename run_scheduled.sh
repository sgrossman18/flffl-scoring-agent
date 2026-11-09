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

# A normal run takes ~5 minutes. If the Mac sleeps mid-run the network
# connection can die without ever erroring, leaving the process hung - and
# launchd never starts a second copy of a job that's still running, so one
# hung run silently blocked every later hourly run for 10 days (Oct 30 - Nov 8).
# So cap each run: `perl alarm` delivers SIGALRM after RUN_TIMEOUT_SECS, which
# kills Python (exit code 142 = 128 + SIGALRM). Perl ships with macOS, unlike
# GNU `timeout`.
RUN_TIMEOUT_SECS=1200

{
    echo "===== $(date) ====="
    # Always force a fresh download: the play-by-play cache never expires on
    # its own (see stats.py), so a scheduled run that reused an
    # already-cached file from earlier in the day would miss a game that
    # finished since then — exactly what happened to the first Monday run.
    perl -e 'alarm shift; exec @ARGV' "$RUN_TIMEOUT_SECS" .venv/bin/python main.py --live --refresh-stats
    code=$?
    echo "----- exit code $code -----"
    if [ "$code" -eq 142 ]; then
        echo "TIMED OUT after ${RUN_TIMEOUT_SECS}s - run killed (the next hourly run will retry)"
        osascript -e 'display notification "Scheduled run hung and was killed after 20 minutes. Log: ~/PycharmProjects/flffl-scorer/logs/scheduled_runs.log" with title "FLFFL scoring TIMED OUT"' >/dev/null 2>&1 || true
    fi
} >> logs/scheduled_runs.log 2>&1
