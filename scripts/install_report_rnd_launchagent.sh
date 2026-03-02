#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/satoshinakamoto/Documents/The Experiment"
LABEL="com.northfox.report-rnd-loop"
PLIST_SRC="$ROOT/scripts/${LABEL}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs/overnight_reports/daemon" "$ROOT/tmp"

if [[ ! -f "$PLIST_SRC" ]]; then
  echo "missing plist template: $PLIST_SRC" >&2
  exit 1
fi

cp "$PLIST_SRC" "$PLIST_DST"
echo "installed launch agent: $PLIST_DST"
