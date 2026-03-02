#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/satoshinakamoto/Documents/The Experiment"
cd "$ROOT"

LOG_DIR="$ROOT/logs/overnight_reports/daemon"
mkdir -p "$LOG_DIR" "$ROOT/tmp"

SUP_PIDFILE="$ROOT/tmp/report_rnd_supervisor.pid"
CHILD_PIDFILE="$ROOT/tmp/report_rnd_worker.pid"
HEARTBEAT="$ROOT/tmp/report_rnd_heartbeat.txt"

DURATION_HOURS="${DURATION_HOURS:-8}"
INTERVAL_MINUTES="${INTERVAL_MINUTES:-15}"
RESTART_DELAY_SECONDS="${RESTART_DELAY_SECONDS:-20}"
REQUEST_TIMEOUT_SECONDS="${SBS_AGENT_REQUEST_TIMEOUT_SECONDS:-12}"

if [[ -f "$SUP_PIDFILE" ]]; then
  old_pid="$(cat "$SUP_PIDFILE" || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Supervisor already running with PID $old_pid"
    exit 0
  fi
fi

echo $$ > "$SUP_PIDFILE"

cleanup() {
  if [[ -f "$CHILD_PIDFILE" ]]; then
    child_pid="$(cat "$CHILD_PIDFILE" || true)"
    if [[ -n "${child_pid}" ]] && kill -0 "$child_pid" 2>/dev/null; then
      kill "$child_pid" 2>/dev/null || true
    fi
    rm -f "$CHILD_PIDFILE"
  fi
  rm -f "$SUP_PIDFILE"
}
trap cleanup EXIT INT TERM

while true; do
  ts="$(date '+%Y-%m-%d_%H-%M-%S')"
  run_log="$LOG_DIR/run_${ts}.log"
  echo "[$(date '+%F %T')] Starting run-report-rnd-loop (duration=${DURATION_HOURS}h interval=${INTERVAL_MINUTES}m)" | tee -a "$run_log"
  echo "start $(date -u '+%FT%TZ')" > "$HEARTBEAT"

  (
    export PYTHONPATH="."
    export SBS_AGENT_REQUEST_TIMEOUT_SECONDS="$REQUEST_TIMEOUT_SECONDS"
    python3 -m sbs_sales_agent.cli run-report-rnd-loop \
      --duration-hours "$DURATION_HOURS" \
      --interval-minutes "$INTERVAL_MINUTES" \
      --dry-run-email-sim
  ) >> "$run_log" 2>&1 &

  child_pid=$!
  echo "$child_pid" > "$CHILD_PIDFILE"

  wait "$child_pid"
  exit_code=$?

  echo "stop $(date -u '+%FT%TZ') code=${exit_code}" >> "$HEARTBEAT"
  echo "[$(date '+%F %T')] Worker exited with code ${exit_code}. Restarting in ${RESTART_DELAY_SECONDS}s" | tee -a "$run_log"
  sleep "$RESTART_DELAY_SECONDS"
done
