#!/bin/bash
# Installs (or re-installs) the launchd job that runs run_scheduled.sh hourly,
# pointing it at THIS folder. Safe to re-run.
#   ./install_schedule.sh          install and load the job
#   ./install_schedule.sh --print  just print the plist that would be installed
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd -P)"
LABEL="com.flffl-scorer.weekly"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RENDERED="$(sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" launchd/$LABEL.plist.template)"

if [ "${1:-}" = "--print" ]; then
    echo "$RENDERED"
    exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" logs
echo "$RENDERED" > "$DEST"
plutil -lint "$DEST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed $DEST (runs hourly; run_scheduled.sh limits it to the Central-time windows)."
