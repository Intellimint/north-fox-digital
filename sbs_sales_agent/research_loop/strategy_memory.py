from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterator

from .types import IterationResult, ReportScore, SalesSimulationScenario


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rnd_iterations (
                    iteration_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NULL,
                    business_id INTEGER NOT NULL,
                    business_name TEXT NOT NULL,
                    website TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS rnd_findings (
                    finding_id TEXT PRIMARY KEY,
                    iteration_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    page_url TEXT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS rnd_reports (
                    report_id TEXT PRIMARY KEY,
                    iteration_id TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    html_path TEXT NOT NULL,
                    score_value REAL NOT NULL,
                    score_accuracy REAL NOT NULL,
                    score_aesthetic REAL NOT NULL,
                    report_word_count INTEGER NOT NULL DEFAULT 0,
                    report_depth_level INTEGER NOT NULL DEFAULT 1,
                    sales_avg_close REAL NOT NULL DEFAULT 0.0,
                    sales_avg_trust REAL NOT NULL DEFAULT 0.0,
                    sales_avg_objection REAL NOT NULL DEFAULT 0.0,
                    roi_base_monthly_upside INTEGER NOT NULL DEFAULT 0,
                    roi_base_payback_days INTEGER NOT NULL DEFAULT 0,
                    report_attempt_count INTEGER NOT NULL DEFAULT 1,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    pass_gate INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS rnd_sales_sims (
                    sim_id TEXT PRIMARY KEY,
                    iteration_id TEXT NOT NULL,
                    scenario_key TEXT NOT NULL,
                    transcript_json TEXT NOT NULL,
                    score_close REAL NOT NULL,
                    score_trust REAL NOT NULL,
                    score_objection REAL NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS rnd_strategy_memory (
                    version INTEGER PRIMARY KEY,
                    memory_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS rnd_business_rotation (
                    source_entity_detail_id INTEGER PRIMARY KEY,
                    last_used_at TEXT NOT NULL,
                    run_count INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_findings_iter ON rnd_findings(iteration_id);
                CREATE INDEX IF NOT EXISTS idx_reports_iter ON rnd_reports(iteration_id);
                CREATE INDEX IF NOT EXISTS idx_sales_iter ON rnd_sales_sims(iteration_id);
                CREATE INDEX IF NOT EXISTS idx_iterations_date ON rnd_iterations(started_at);
                """
            )
            report_cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(rnd_reports)").fetchall()}
            if "reasons_json" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN reasons_json TEXT NOT NULL DEFAULT '[]'")
            if "report_word_count" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN report_word_count INTEGER NOT NULL DEFAULT 0")
            if "report_depth_level" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN report_depth_level INTEGER NOT NULL DEFAULT 1")
            if "sales_avg_close" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN sales_avg_close REAL NOT NULL DEFAULT 0.0")
            if "sales_avg_trust" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN sales_avg_trust REAL NOT NULL DEFAULT 0.0")
            if "sales_avg_objection" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN sales_avg_objection REAL NOT NULL DEFAULT 0.0")
            if "roi_base_monthly_upside" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN roi_base_monthly_upside INTEGER NOT NULL DEFAULT 0")
            if "roi_base_payback_days" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN roi_base_payback_days INTEGER NOT NULL DEFAULT 0")
            if "report_attempt_count" not in report_cols:
                conn.execute("ALTER TABLE rnd_reports ADD COLUMN report_attempt_count INTEGER NOT NULL DEFAULT 1")

    def get_latest_strategy(self) -> dict[str, Any]:
        with self.session() as conn:
            row = conn.execute(
                "SELECT version, memory_json FROM rnd_strategy_memory ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {
                "version": 1,
                "weights": {
                    "security": 1.2,
                    "email_auth": 1.0,
                    "seo": 1.0,
                    "ada": 1.0,
                    "conversion": 1.1,
                },
                "report_depth_level": 1,
                "sales_sim_target_count": 6,
                "min_findings": {
                    "security": 3,
                    "email_auth": 2,
                    "seo": 3,
                    "ada": 3,
                    "conversion": 3,
                },
                "notes": [],
            }
        mem = json.loads(str(row["memory_json"]))
        mem["version"] = int(row["version"])
        return mem

    def write_strategy(self, memory: dict[str, Any]) -> int:
        old = self.get_latest_strategy()
        new_version = int(old.get("version", 1)) + 1
        with self.session() as conn:
            conn.execute(
                "INSERT INTO rnd_strategy_memory (version, memory_json, created_at) VALUES (?, ?, ?)",
                (new_version, json.dumps(memory, ensure_ascii=True), utcnow_iso()),
            )
        return new_version

    def begin_iteration(self, *, iteration_id: str, business_id: int, business_name: str, website: str, config: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO rnd_iterations (iteration_id, started_at, business_id, business_name, website, status, config_json)
                VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (iteration_id, utcnow_iso(), business_id, business_name, website, json.dumps(config, ensure_ascii=True)),
            )
            conn.execute(
                """
                INSERT INTO rnd_business_rotation (source_entity_detail_id, last_used_at, run_count)
                VALUES (?, ?, 1)
                ON CONFLICT(source_entity_detail_id) DO UPDATE SET
                    last_used_at = excluded.last_used_at,
                    run_count = rnd_business_rotation.run_count + 1
                """,
                (business_id, utcnow_iso()),
            )

    def finish_iteration(self, *, iteration_id: str, status: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE rnd_iterations SET completed_at = ?, status = ? WHERE iteration_id = ?",
                (utcnow_iso(), status, iteration_id),
            )

    def mark_stale_running_iterations(self) -> int:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT iteration_id FROM rnd_iterations WHERE status = 'running'"
            ).fetchall()
            if not rows:
                return 0
            now = utcnow_iso()
            conn.execute(
                """
                UPDATE rnd_iterations
                SET status = 'failed', completed_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
        return len(rows)

    def used_business_ids(self, *, limit: int = 5000) -> set[int]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT source_entity_detail_id FROM rnd_business_rotation ORDER BY last_used_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {int(r["source_entity_detail_id"]) for r in rows}

    def recent_business_ids(self, *, limit: int = 32) -> set[int]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT business_id
                FROM rnd_iterations
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {int(r["business_id"]) for r in rows}

    def business_rotation_state(self) -> dict[int, tuple[int, str]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT source_entity_detail_id, run_count, last_used_at
                FROM rnd_business_rotation
                """
            ).fetchall()
        return {
            int(r["source_entity_detail_id"]): (
                int(r["run_count"] or 0),
                str(r["last_used_at"] or ""),
            )
            for r in rows
        }

    def record_iteration_result(self, result: IterationResult) -> None:
        from uuid import uuid4

        def _finding_json(finding: Any) -> str:
            if is_dataclass(finding):
                payload = asdict(finding)
            elif isinstance(finding, dict):
                payload = dict(finding)
            else:
                payload = {"value": str(finding)}
            return json.dumps(payload, ensure_ascii=True)

        with self.session() as conn:
            for finding in result.findings:
                category = str(getattr(finding, "category", "") or (finding.get("category") if isinstance(finding, dict) else "unknown"))
                severity = str(getattr(finding, "severity", "") or (finding.get("severity") if isinstance(finding, dict) else "unknown"))
                evidence = getattr(finding, "evidence", None)
                if evidence is None and isinstance(finding, dict):
                    evidence = finding.get("evidence")
                page_url = str(getattr(evidence, "page_url", "") or (evidence.get("page_url") if isinstance(evidence, dict) else ""))
                conn.execute(
                    """
                    INSERT INTO rnd_findings (finding_id, iteration_id, category, severity, evidence_json, page_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        result.iteration_id,
                        category or "unknown",
                        severity or "unknown",
                        _finding_json(finding),
                        page_url,
                    ),
                )
            score: ReportScore = result.score
            conn.execute(
                """
                INSERT INTO rnd_reports (
                    report_id, iteration_id, pdf_path, json_path, html_path, score_value, score_accuracy, score_aesthetic,
                    report_word_count, report_depth_level,
                    sales_avg_close, sales_avg_trust, sales_avg_objection,
                    roi_base_monthly_upside, roi_base_payback_days, report_attempt_count,
                    reasons_json, pass_gate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    result.iteration_id,
                    result.report_pdf_path,
                    result.report_json_path,
                    result.report_html_path,
                    score.value_score,
                    score.accuracy_score,
                    score.aesthetic_score,
                    int(result.report_word_count or 0),
                    int(result.report_depth_level or 1),
                    float(result.sales_avg_close or 0.0),
                    float(result.sales_avg_trust or 0.0),
                    float(result.sales_avg_objection or 0.0),
                    int(result.roi_base_monthly_upside or 0),
                    int(result.roi_base_payback_days or 0),
                    int(result.report_attempt_count or 1),
                    json.dumps(list(score.reasons or []), ensure_ascii=True),
                    1 if score.pass_gate else 0,
                ),
            )
            for sim in result.sales_scenarios:
                sim: SalesSimulationScenario
                conn.execute(
                    """
                    INSERT INTO rnd_sales_sims (
                        sim_id, iteration_id, scenario_key, transcript_json, score_close, score_trust, score_objection
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        result.iteration_id,
                        sim.scenario_key,
                        json.dumps(sim.turns, ensure_ascii=True),
                        sim.score_close,
                        sim.score_trust,
                        sim.score_objection,
                    ),
                )

    def top_reports_for_date(self, day_prefix: str, *, limit: int = 5) -> list[sqlite3.Row]:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT i.iteration_id, i.business_id, i.business_name, i.website,
                       r.pdf_path, r.score_value, r.score_accuracy, r.score_aesthetic, r.pass_gate,
                       r.report_word_count, r.report_depth_level, r.report_attempt_count,
                       r.sales_avg_close, r.sales_avg_trust, r.sales_avg_objection,
                       r.roi_base_monthly_upside, r.roi_base_payback_days,
                       (
                         (0.40 * r.score_value) +
                         (0.20 * r.score_accuracy) +
                         (0.10 * r.score_aesthetic) +
                         (
                           0.20 * (
                             (COALESCE(r.sales_avg_close, 0.0) + COALESCE(r.sales_avg_trust, 0.0) + COALESCE(r.sales_avg_objection, 0.0)) / 3.0
                           )
                         ) +
                         (
                           0.10 * CASE
                             WHEN COALESCE(r.roi_base_payback_days, 0) <= 0 THEN 0.0
                             WHEN r.roi_base_payback_days <= 30 THEN 100.0
                             WHEN r.roi_base_payback_days <= 60 THEN 80.0
                             WHEN r.roi_base_payback_days <= 90 THEN 65.0
                             ELSE 45.0
                           END
                         )
                       ) AS commercial_score
                FROM rnd_iterations i
                JOIN rnd_reports r ON r.iteration_id = i.iteration_id
                WHERE i.started_at LIKE ?
                ORDER BY r.pass_gate DESC, commercial_score DESC, r.score_value DESC, r.score_accuracy DESC
                LIMIT ?
                """,
                (f"{day_prefix}%", limit),
            ).fetchall()

    def iteration_dates_between(self, *, start_iso: str, end_iso: str) -> list[str]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(started_at, 1, 10) AS day
                FROM rnd_iterations
                WHERE started_at >= ? AND started_at <= ?
                ORDER BY day ASC
                """,
                (start_iso, end_iso),
            ).fetchall()
        return [str(r["day"]) for r in rows if str(r["day"] or "").strip()]

    def metrics_for_date(self, day_prefix: str) -> dict[str, Any]:
        with self.session() as conn:
            counts = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status = 'needs_improvement' THEN 1 ELSE 0 END) AS needs_improvement,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM rnd_iterations
                WHERE started_at LIKE ?
                """,
                (f"{day_prefix}%",),
            ).fetchone()
            score = conn.execute(
                """
                SELECT COALESCE(AVG(score_value), 0.0) AS avg_value,
                       COALESCE(AVG(score_accuracy), 0.0) AS avg_accuracy,
                       COALESCE(AVG(score_aesthetic), 0.0) AS avg_aesthetic,
                       COALESCE(AVG(report_word_count), 0.0) AS avg_report_words,
                       COALESCE(AVG(report_depth_level), 0.0) AS avg_report_depth,
                       COALESCE(AVG(sales_avg_close), 0.0) AS avg_sales_close,
                       COALESCE(AVG(sales_avg_trust), 0.0) AS avg_sales_trust,
                       COALESCE(AVG(sales_avg_objection), 0.0) AS avg_sales_objection,
                       COALESCE(AVG(roi_base_monthly_upside), 0.0) AS avg_roi_base_monthly_upside,
                       COALESCE(AVG(roi_base_payback_days), 0.0) AS avg_roi_base_payback_days,
                       COALESCE(AVG(report_attempt_count), 0.0) AS avg_report_attempt_count,
                       COALESCE(AVG(
                         (0.40 * score_value) +
                         (0.20 * score_accuracy) +
                         (0.10 * score_aesthetic) +
                         (
                           0.20 * ((COALESCE(sales_avg_close, 0.0) + COALESCE(sales_avg_trust, 0.0) + COALESCE(sales_avg_objection, 0.0)) / 3.0)
                         ) +
                         (
                           0.10 * CASE
                             WHEN COALESCE(roi_base_payback_days, 0) <= 0 THEN 0.0
                             WHEN roi_base_payback_days <= 30 THEN 100.0
                             WHEN roi_base_payback_days <= 60 THEN 80.0
                             WHEN roi_base_payback_days <= 90 THEN 65.0
                             ELSE 45.0
                           END
                         )
                       ), 0.0) AS avg_commercial_score,
                       COALESCE(MIN(score_value), 0.0) AS min_value,
                       COALESCE(MAX(score_value), 0.0) AS max_value
                FROM rnd_reports r
                JOIN rnd_iterations i ON i.iteration_id = r.iteration_id
                WHERE i.started_at LIKE ?
                """,
                (f"{day_prefix}%",),
            ).fetchone()
            pass_row = conn.execute(
                """
                SELECT COUNT(*) AS report_count,
                       SUM(CASE WHEN pass_gate = 1 THEN 1 ELSE 0 END) AS pass_count
                FROM rnd_reports r
                JOIN rnd_iterations i ON i.iteration_id = r.iteration_id
                WHERE i.started_at LIKE ?
                """,
                (f"{day_prefix}%",),
            ).fetchone()
            trend_rows = conn.execute(
                """
                SELECT r.score_value
                FROM rnd_reports r
                JOIN rnd_iterations i ON i.iteration_id = r.iteration_id
                WHERE i.started_at LIKE ?
                ORDER BY i.started_at ASC
                """,
                (f"{day_prefix}%",),
            ).fetchall()
            cat_rows = conn.execute(
                """
                SELECT f.category AS category,
                       COUNT(*) AS finding_count,
                       SUM(CASE WHEN f.severity IN ('high', 'critical') THEN 1 ELSE 0 END) AS high_critical_count
                FROM rnd_findings f
                JOIN rnd_iterations i ON i.iteration_id = f.iteration_id
                WHERE i.started_at LIKE ?
                GROUP BY f.category
                """,
                (f"{day_prefix}%",),
            ).fetchall()
            sim_rows = conn.execute(
                """
                SELECT COALESCE(AVG(s.score_close), 0.0) AS avg_close,
                       COALESCE(AVG(s.score_trust), 0.0) AS avg_trust,
                       COALESCE(AVG(s.score_objection), 0.0) AS avg_objection
                FROM rnd_sales_sims s
                JOIN rnd_iterations i ON i.iteration_id = s.iteration_id
                WHERE i.started_at LIKE ?
                """,
                (f"{day_prefix}%",),
            ).fetchone()
            sim_scenario_rows = conn.execute(
                """
                SELECT s.scenario_key AS scenario_key,
                       COUNT(*) AS run_count,
                       COALESCE(AVG(s.score_close), 0.0) AS avg_close,
                       COALESCE(AVG(s.score_trust), 0.0) AS avg_trust,
                       COALESCE(AVG(s.score_objection), 0.0) AS avg_objection
                FROM rnd_sales_sims s
                JOIN rnd_iterations i ON i.iteration_id = s.iteration_id
                WHERE i.started_at LIKE ?
                GROUP BY s.scenario_key
                """,
                (f"{day_prefix}%",),
            ).fetchall()
            fail_reason_rows = conn.execute(
                """
                SELECT r.reasons_json
                FROM rnd_reports r
                JOIN rnd_iterations i ON i.iteration_id = r.iteration_id
                WHERE i.started_at LIKE ? AND r.pass_gate = 0
                """,
                (f"{day_prefix}%",),
            ).fetchall()
        values = [float(r["score_value"]) for r in trend_rows]
        trend_delta = 0.0
        if len(values) >= 2:
            trend_delta = values[-1] - values[0]
        rolling_window = 3
        recent_avg = 0.0
        prior_avg = 0.0
        rolling_delta = 0.0
        if values:
            recent_avg = sum(values[-rolling_window:]) / float(min(len(values), rolling_window))
            if len(values) > rolling_window:
                prior = values[max(0, len(values) - (rolling_window * 2)) : -rolling_window]
                if prior:
                    prior_avg = sum(prior) / float(len(prior))
                    rolling_delta = recent_avg - prior_avg
        category_counts = {str(r["category"]): int(r["finding_count"] or 0) for r in cat_rows}
        category_high_critical = {str(r["category"]): int(r["high_critical_count"] or 0) for r in cat_rows}
        fail_reason_counts: dict[str, int] = {}
        for row in fail_reason_rows:
            raw = str(row["reasons_json"] or "[]")
            try:
                reasons = json.loads(raw)
            except Exception:
                reasons = []
            if not isinstance(reasons, list):
                continue
            for item in reasons:
                reason = str(item or "").strip()
                if not reason:
                    continue
                fail_reason_counts[reason] = int(fail_reason_counts.get(reason, 0)) + 1
        top_fail_reasons = sorted(fail_reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        scenario_stats: list[dict[str, Any]] = []
        weak_scenarios: list[dict[str, Any]] = []
        for row in sim_scenario_rows:
            avg_close = round(float(row["avg_close"] or 0.0), 2)
            avg_trust = round(float(row["avg_trust"] or 0.0), 2)
            avg_objection = round(float(row["avg_objection"] or 0.0), 2)
            avg_total = round((avg_close + avg_trust + avg_objection) / 3.0, 2)
            entry = {
                "scenario_key": str(row["scenario_key"] or "unknown"),
                "run_count": int(row["run_count"] or 0),
                "avg_close": avg_close,
                "avg_trust": avg_trust,
                "avg_objection": avg_objection,
                "avg_total": avg_total,
            }
            scenario_stats.append(entry)
            if avg_close < 70.0 or avg_trust < 72.0 or avg_objection < 70.0:
                weak_scenarios.append(entry)
        scenario_stats.sort(key=lambda item: (item["avg_total"], item["scenario_key"]))
        weak_scenarios.sort(key=lambda item: (item["avg_total"], item["scenario_key"]))
        return {
            "total": int(counts["total"] or 0),
            "completed": int(counts["completed"] or 0),
            "needs_improvement": int(counts["needs_improvement"] or 0),
            "failed": int(counts["failed"] or 0),
            "avg_value": float(score["avg_value"] or 0.0),
            "avg_accuracy": float(score["avg_accuracy"] or 0.0),
            "avg_aesthetic": float(score["avg_aesthetic"] or 0.0),
            "avg_report_words": float(score["avg_report_words"] or 0.0),
            "avg_report_depth": float(score["avg_report_depth"] or 0.0),
            "avg_sales_close_from_report": float(score["avg_sales_close"] or 0.0),
            "avg_sales_trust_from_report": float(score["avg_sales_trust"] or 0.0),
            "avg_sales_objection_from_report": float(score["avg_sales_objection"] or 0.0),
            "avg_roi_base_monthly_upside": float(score["avg_roi_base_monthly_upside"] or 0.0),
            "avg_roi_base_payback_days": float(score["avg_roi_base_payback_days"] or 0.0),
            "avg_report_attempt_count": float(score["avg_report_attempt_count"] or 0.0),
            "avg_commercial_score": float(score["avg_commercial_score"] or 0.0),
            "min_value": float(score["min_value"] or 0.0),
            "max_value": float(score["max_value"] or 0.0),
            "median_value": float(median(values)) if values else 0.0,
            "rolling_recent_value_avg": round(float(recent_avg), 2),
            "rolling_prior_value_avg": round(float(prior_avg), 2),
            "rolling_value_delta": round(float(rolling_delta), 2),
            "report_count": int(pass_row["report_count"] or 0),
            "pass_count": int(pass_row["pass_count"] or 0),
            "pass_rate": (
                float(pass_row["pass_count"] or 0) / float(pass_row["report_count"])
                if float(pass_row["report_count"] or 0) > 0
                else 0.0
            ),
            "value_trend_delta": round(float(trend_delta), 2),
            "score_values": [round(v, 1) for v in values],
            "category_counts": category_counts,
            "category_high_critical": category_high_critical,
            "fail_reason_counts": fail_reason_counts,
            "top_fail_reasons": [{"reason": k, "count": v} for k, v in top_fail_reasons],
            "sales_avg_close": round(float(sim_rows["avg_close"] or 0.0), 2),
            "sales_avg_trust": round(float(sim_rows["avg_trust"] or 0.0), 2),
            "sales_avg_objection": round(float(sim_rows["avg_objection"] or 0.0), 2),
            "sales_scenario_stats": scenario_stats,
            "sales_weak_scenarios": weak_scenarios[:6],
        }
