#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/satoshinakamoto/Documents/The Experiment"
LABEL="com.northfox.report-rnd-loop"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl unload -w "$PLIST_DST" >/dev/null 2>&1 || true

echo "stopped launch agent ${LABEL}"
