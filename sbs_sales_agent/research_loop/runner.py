from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import AgentSettings
from ..source_sbs import SourceProspectRepository
from .iteration import run_iteration
from .strategy_memory import ResearchDB


class IterationTimeoutError(RuntimeError):
    pass


def _iteration_timeout_seconds() -> int:
    raw = os.getenv("SBS_RND_ITERATION_TIMEOUT_SECONDS", "900")
    try:
        val = int(raw)
    except Exception:
        val = 900
    return max(120, val)


def _run_iteration_with_timeout(*, settings: AgentSettings, research_db: ResearchDB, source_repo: SourceProspectRepository, iteration_label: str, timeout_seconds: int) -> None:
    def _on_alarm(_sig: int, _frame: Any) -> None:
        raise IterationTimeoutError(f"iteration_timeout:{timeout_seconds}s")

    prev_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        run_iteration(
            settings=settings,
            research_db=research_db,
            source_repo=source_repo,
            iteration_label=iteration_label,
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev_handler)


def _research_db_path(settings: AgentSettings) -> Path:
    return Path(settings.report_rnd_db_path)


def _sparkline(values: list[float]) -> str:
    """Return an ASCII sparkline (up to 32 chars) from a list of 0–100 score values."""
    if not values:
        return "(no data)"
    blocks = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    chars = [blocks[min(8, int(round((v - lo) / span * 8)))] for v in values]
    return "".join(chars)


def _write_daily_index(*, day_dir: Path, rows: list[dict[str, Any]], metrics: dict[str, Any]) -> Path:
    category_counts = dict(metrics.get("category_counts") or {})
    category_urgent = dict(metrics.get("category_high_critical") or {})
    category_lines: list[str] = []
    for cat in sorted(category_counts.keys()):
        total = int(category_counts.get(cat, 0))
        urgent = int(category_urgent.get(cat, 0))
        category_lines.append(f"- {cat}: {total} findings ({urgent} high/critical)")
    top_fail_reasons = list(metrics.get("top_fail_reasons") or [])
    fail_reason_lines = [
        f"- {str(item.get('reason') or 'unknown')}: {int(item.get('count') or 0)}"
        for item in top_fail_reasons
    ]
    weak_scenarios = list(metrics.get("sales_weak_scenarios") or [])
    weak_scenario_lines = [
        (
            f"- {str(item.get('scenario_key') or 'unknown')}: total {float(item.get('avg_total') or 0.0):.1f} "
            f"(close {float(item.get('avg_close') or 0.0):.1f}, trust {float(item.get('avg_trust') or 0.0):.1f}, "
            f"objection {float(item.get('avg_objection') or 0.0):.1f}) across {int(item.get('run_count') or 0)} run(s)"
        )
        for item in weak_scenarios[:6]
    ]
    scenario_stats = list(metrics.get("sales_scenario_stats") or [])
    scenario_coverage = len(scenario_stats)
    scenario_run_total = sum(int(item.get("run_count") or 0) for item in scenario_stats)
    scenario_min_runs = min((int(item.get("run_count") or 0) for item in scenario_stats), default=0)
    lines = [
        f"# Overnight Report R&D Index ({day_dir.name})",
        "",
        f"- Total iterations: {metrics.get('total', 0)}",
        f"- Completed: {metrics.get('completed', 0)}",
        f"- Needs improvement: {metrics.get('needs_improvement', 0)}",
        f"- Failed: {metrics.get('failed', 0)}",
        f"- Gate pass rate: {float(metrics.get('pass_rate', 0.0)) * 100.0:.1f}% ({metrics.get('pass_count', 0)}/{metrics.get('report_count', 0)})",
        f"- Avg value score: {metrics.get('avg_value', 0.0):.1f}",
        f"- Median value score: {metrics.get('median_value', 0.0):.1f}",
        f"- Avg accuracy score: {metrics.get('avg_accuracy', 0.0):.1f}",
        f"- Avg aesthetic score: {metrics.get('avg_aesthetic', 0.0):.1f}",
        f"- Avg report words: {metrics.get('avg_report_words', 0.0):.0f}",
        f"- Avg report depth level: {metrics.get('avg_report_depth', 0.0):.1f}/5",
        f"- Avg commercial score: {metrics.get('avg_commercial_score', 0.0):.1f}",
        (
            "- Avg base-case upside / payback: "
            f"${int(metrics.get('avg_roi_base_monthly_upside', 0.0) or 0):,}/month "
            f"| {metrics.get('avg_roi_base_payback_days', 0.0):.1f} days"
        ),
        f"- Avg report attempts to final: {metrics.get('avg_report_attempt_count', 0.0):.2f}",
        f"- Value trend delta (first→latest): {metrics.get('value_trend_delta', 0.0):+.1f}",
        f"- Rolling value delta (recent vs prior): {metrics.get('rolling_value_delta', 0.0):+.1f}",
        f"- Value score trend: {_sparkline(list(metrics.get('score_values') or []))}",
        f"- Sales sim avg scores: close {metrics.get('sales_avg_close', 0.0):.1f} | trust {metrics.get('sales_avg_trust', 0.0):.1f} | objection {metrics.get('sales_avg_objection', 0.0):.1f}",
        f"- Sales scenario coverage: {scenario_coverage} personas exercised ({scenario_run_total} total simulations, min {scenario_min_runs} runs/persona)",
        "",
        "## Category Mix",
        *(category_lines or ["- No finding-category telemetry available."]),
        "",
        "## Top Gate Failure Reasons",
        *(fail_reason_lines or ["- No gate failures recorded."]),
        "",
        "## Top Sales Simulation Weak Spots",
        *(weak_scenario_lines or ["- No weak sales scenarios detected in this window."]),
        "",
        "## Top Reports",
    ]
    if not rows:
        lines.append("- No completed reports found.")
    for i, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"{i}. {row['business_name']} ({row['website']})",
                f"   - PDF: {row['pdf_path']}",
                f"   - Score: value {row['score_value']:.1f} | accuracy {row['score_accuracy']:.1f} | aesthetic {row['score_aesthetic']:.1f}",
                (
                    "   - Commercial score: "
                    f"{float(row.get('commercial_score') or 0.0):.1f}"
                    f" | sales avg {((float(row.get('sales_avg_close') or 0.0) + float(row.get('sales_avg_trust') or 0.0) + float(row.get('sales_avg_objection') or 0.0)) / 3.0):.1f}"
                ),
                (
                    "   - ROI model (base): "
                    f"${int(row.get('roi_base_monthly_upside') or 0):,}/month"
                    f" | payback {int(row.get('roi_base_payback_days') or 0)} day(s)"
                ),
                f"   - Report depth: {int(row.get('report_depth_level', 1) or 1)}/5 | words: {int(row.get('report_word_count', 0) or 0)}",
                f"   - Generation attempts: {int(row.get('report_attempt_count', 1) or 1)}",
                f"   - Gate: {'PASS' if int(row.get('pass_gate', 0)) == 1 else 'FAIL'}",
            ]
        )
    path = day_dir / "_index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_single_iteration(*, settings: AgentSettings, iteration_id: str | None = None, entity_id: int | None = None) -> dict[str, Any]:
    db = ResearchDB(_research_db_path(settings))
    db.init_db()
    db.mark_stale_running_iterations()
    src = SourceProspectRepository(settings.sbs_db_path)
    result = run_iteration(
        settings=settings,
        research_db=db,
        source_repo=src,
        iteration_label=iteration_id,
        force_entity_id=entity_id,
    )
    return {
        "ok": True,
        "iteration_id": result.iteration_id,
        "business_id": result.entity_detail_id,
        "business_name": result.business_name,
        "pdf": result.report_pdf_path,
        "value_score": result.score.value_score,
        "accuracy_score": result.score.accuracy_score,
        "aesthetic_score": result.score.aesthetic_score,
        "pass_gate": result.score.pass_gate,
    }


def run_loop(*, settings: AgentSettings, duration_hours: float, interval_minutes: int, dry_run_email_sim: bool = True) -> dict[str, Any]:
    _ = dry_run_email_sim  # all simulations are offline in this subsystem.

    start = datetime.now(timezone.utc)
    start_iso = start.isoformat()
    end_ts = time.time() + max(1.0, duration_hours * 3600.0)
    iterations = 0
    failures = 0
    last_error = ""
    db = ResearchDB(_research_db_path(settings))
    db.init_db()
    db.mark_stale_running_iterations()
    src = SourceProspectRepository(settings.sbs_db_path)
    timeout_seconds = _iteration_timeout_seconds()
    print(
        f"[rnd-loop] start utc={start.isoformat()} duration_h={duration_hours} interval_m={interval_minutes} iter_timeout_s={timeout_seconds}",
        flush=True,
    )

    while time.time() < end_ts:
        iter_started = time.time()
        iteration_id = datetime.now(timezone.utc).strftime("iter_%Y%m%d_%H%M%S_") + str(uuid4())[:8]
        print(f"[rnd-loop] iteration_start id={iteration_id} utc={datetime.now(timezone.utc).isoformat()}", flush=True)
        try:
            _run_iteration_with_timeout(
                settings=settings,
                research_db=db,
                source_repo=src,
                iteration_label=iteration_id,
                timeout_seconds=timeout_seconds,
            )
            iterations += 1
            print(
                f"[rnd-loop] iteration_done id={iteration_id} elapsed_s={time.time() - iter_started:.1f}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            last_error = str(exc)
            print(
                f"[rnd-loop] iteration_failed id={iteration_id} elapsed_s={time.time() - iter_started:.1f} err={last_error}",
                flush=True,
            )
        elapsed = time.time() - iter_started
        sleep_for = max(0.0, interval_minutes * 60 - elapsed)
        if sleep_for > 0:
            print(f"[rnd-loop] sleep_s={sleep_for:.1f}", flush=True)
            time.sleep(sleep_for)

    end_iso = datetime.now(timezone.utc).isoformat()
    day_prefixes = db.iteration_dates_between(start_iso=start_iso, end_iso=end_iso)
    if not day_prefixes:
        day_prefixes = [start.strftime("%Y-%m-%d")]
    index_paths: dict[str, str] = {}
    latest_rows: list[dict[str, Any]] = []
    latest_metrics: dict[str, Any] = {}
    for day_prefix in day_prefixes:
        metrics = db.metrics_for_date(day_prefix)
        top_rows = db.top_reports_for_date(day_prefix, limit=5)
        rows = [dict(r) for r in top_rows]
        day_dir = Path("logs/overnight_reports") / day_prefix
        day_dir.mkdir(parents=True, exist_ok=True)
        index_path = _write_daily_index(day_dir=day_dir, rows=rows, metrics=metrics)
        index_paths[day_prefix] = str(index_path)
        latest_rows = rows
        latest_metrics = metrics

    return {
        "ok": failures == 0,
        "iterations": iterations,
        "failures": failures,
        "last_error": last_error,
        "metrics": latest_metrics,
        "top_reports": latest_rows,
        "index_path": index_paths[day_prefixes[-1]],
        "index_paths": index_paths,
        "db_path": str(_research_db_path(settings)),
    }


def summarize_run_date(*, settings: AgentSettings, date: str) -> dict[str, Any]:
    db = ResearchDB(_research_db_path(settings))
    db.init_db()
    metrics = db.metrics_for_date(date)
    top_rows = [dict(r) for r in db.top_reports_for_date(date, limit=5)]
    day_dir = Path("logs/overnight_reports") / date
    day_dir.mkdir(parents=True, exist_ok=True)
    index_path = _write_daily_index(day_dir=day_dir, rows=top_rows, metrics=metrics)
    return {
        "ok": True,
        "date": date,
        "metrics": metrics,
        "top_reports": top_rows,
        "index_path": str(index_path),
    }
