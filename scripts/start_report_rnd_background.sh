#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/satoshinakamoto/Documents/The Experiment"
LABEL="com.northfox.report-rnd-loop"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

cd "$ROOT"

bash "$ROOT/scripts/install_report_rnd_launchagent.sh"

launchctl unload -w "$PLIST_DST" >/dev/null 2>&1 || true
launchctl load -w "$PLIST_DST"

echo "started launch agent ${LABEL}"
