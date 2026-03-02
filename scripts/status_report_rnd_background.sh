#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/satoshinakamoto/Documents/The Experiment"
LABEL="com.northfox.report-rnd-loop"
UID_NUM="$(id -u)"
DB="$ROOT/tmp/sbs_report_rnd.db"

if launchctl print "gui/${UID_NUM}/${LABEL}" >/tmp/report_rnd_launchctl_status.txt 2>/dev/null; then
  echo "launch agent: loaded (${LABEL})"
  awk '/state =|pid =|last exit code =/' /tmp/report_rnd_launchctl_status.txt || true
else
  echo "launch agent: not loaded (${LABEL})"
fi

if [[ -f "$DB" ]]; then
  sqlite3 "$DB" "select 'iterations_today', count(*) from rnd_iterations where started_at like strftime('%Y-%m-%d','now')||'%';" || true
  sqlite3 "$DB" "select iteration_id, status, started_at, completed_at from rnd_iterations order by started_at desc limit 3;" || true
fi

echo "recent logs:"
ls -1t "$ROOT"/logs/overnight_reports/daemon/*.log 2>/dev/null | head -n 5 || true
