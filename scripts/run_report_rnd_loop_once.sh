#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/satoshinakamoto/Documents/The Experiment"
cd "$ROOT"

mkdir -p "$ROOT/logs/overnight_reports/daemon" "$ROOT/tmp"
LOCK_DIR="$ROOT/tmp/report_rnd_loop.lock"
LOCK_PID="$LOCK_DIR/pid"

if mkdir "$LOCK_DIR" 2>/dev/null; then
  echo $$ > "$LOCK_PID"
else
  if [[ -f "$LOCK_PID" ]]; then
    old_pid="$(cat "$LOCK_PID" || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "[$(date '+%F %T')] run_report_rnd_loop_once skipped: already running pid=$old_pid" >> "$ROOT/logs/overnight_reports/daemon/launchd.out.log"
      exit 0
    fi
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  echo $$ > "$LOCK_PID"
fi

cleanup() {
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY_BIN="$ROOT/.venv/bin/python"
else
  PY_BIN="$(command -v python3)"
fi

export PYTHONPATH="."
export SBS_AGENT_REQUEST_TIMEOUT_SECONDS="${SBS_AGENT_REQUEST_TIMEOUT_SECONDS:-12}"
export DYLD_FALLBACK_LIBRARY_PATH="${DYLD_FALLBACK_LIBRARY_PATH:-/opt/homebrew/lib:/opt/homebrew/opt/libffi/lib:/usr/local/lib:/usr/local/opt/libffi/lib}"

echo "[$(date '+%F %T')] run_report_rnd_loop_once start (python=$PY_BIN)" >> "$ROOT/logs/overnight_reports/daemon/launchd.out.log"
echo "start $(date -u '+%FT%TZ')" > "$ROOT/tmp/report_rnd_heartbeat.txt"

"$PY_BIN" -m sbs_sales_agent.cli run-report-rnd-loop \
  --duration-hours "${DURATION_HOURS:-8}" \
  --interval-minutes "${INTERVAL_MINUTES:-15}" \
  --dry-run-email-sim

echo "stop $(date -u '+%FT%TZ') code=0" >> "$ROOT/tmp/report_rnd_heartbeat.txt"
