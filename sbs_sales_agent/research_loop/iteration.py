from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import AgentSettings
from ..source_sbs import SourceProspectRepository
from .business_sampler import SampledBusiness, pick_next_business
from .report_builder import build_report_payload
from .report_pdf import build_pdf_report
from .sales_simulator import preferred_persona_order, run_sales_simulation
from .scan_pipeline import run_scan_pipeline
from .strategy_memory import ResearchDB
from .types import IterationResult
from .value_judge import adapt_strategy, evaluate_report


def _date_dir(base: Path, when: datetime) -> Path:
    return base / when.strftime("%Y-%m-%d")


def _scan_error_reason(scan_payload: dict[str, object] | None) -> str:
    if not isinstance(scan_payload, dict):
        return ""
    return str(scan_payload.get("scan_error") or "").strip().lower()


def _should_resample_business(*, scan_payload: dict[str, object] | None) -> bool:
    reason = _scan_error_reason(scan_payload)
    if not reason:
        return False
    hard_fail_markers = (
        "403",
        "forbidden",
        "certificate_verify_failed",
        "tls",
        "nodename nor servname",
        "name or service not known",
        "temporary failure in name resolution",
        "connection refused",
        "connection reset",
        "no_pages_fetched",
    )
    return any(marker in reason for marker in hard_fail_markers)


def _local_retry_strategy(*, current_strategy: dict[str, Any], score_reasons: list[str]) -> dict[str, Any]:
    """Escalate strategy for an intra-iteration report retry."""
    next_strategy = dict(current_strategy)
    next_strategy["report_depth_level"] = min(5, int(next_strategy.get("report_depth_level", 1) or 1) + 1)
    next_strategy["report_word_target"] = min(3800, int(next_strategy.get("report_word_target", 1200) or 1200) + 400)
    min_findings = dict(next_strategy.get("min_findings") or {})
    for reason in score_reasons:
        if reason.startswith("min_findings_not_met:"):
            cat = reason.split(":", 1)[1]
            min_findings[cat] = min(10, int(min_findings.get(cat, 2) or 2) + 1)
    next_strategy["min_findings"] = min_findings
    return next_strategy


def run_iteration(*, settings: AgentSettings, research_db: ResearchDB, source_repo: SourceProspectRepository, iteration_label: str | None = None, force_entity_id: int | None = None) -> IterationResult:
    started = datetime.now(timezone.utc)
    iteration_id = iteration_label or started.strftime("iter_%Y%m%d_%H%M%S_") + str(uuid4())[:8]
    strategy = research_db.get_latest_strategy()
    sales_sim_target_count = max(6, min(10, int(strategy.get("sales_sim_target_count", 6) or 6)))
    attempted_ids: set[int] = set()

    def _pick_business() -> SampledBusiness:
        if force_entity_id is not None:
            row = source_repo.get_prospect(int(force_entity_id))
            if row is None:
                raise RuntimeError(f"entity_not_found:{force_entity_id}")
            from ..features import features_from_sbs_row

            f = features_from_sbs_row(row)
            return SampledBusiness(
                entity_detail_id=f.entity_detail_id,
                business_name=f.business_name,
                website=f.website or "",
                contact_name=f.contact_name_normalized or f.first_name_for_greeting,
                email=f.email,
            )
        return pick_next_business(source_repo, research_db, excluded_ids=attempted_ids)

    business = _pick_business()
    if not business.website:
        raise RuntimeError("sampled_business_missing_website")

    base_dir = _date_dir(Path("logs/overnight_reports"), started)
    out_dir = base_dir / iteration_id
    out_dir.mkdir(parents=True, exist_ok=True)

    scan_attempt = 0
    scan_out_dir = out_dir / f"scan_attempt_{scan_attempt}"
    scan = run_scan_pipeline(settings=settings, website=str(business.website), out_dir=scan_out_dir)
    if force_entity_id is None:
        max_resample_attempts = 4
        resample_attempt = 0
        while _should_resample_business(scan_payload=scan) and resample_attempt < max_resample_attempts:
            attempted_ids.add(int(business.entity_detail_id))
            resample_attempt += 1
            scan_attempt += 1
            business = _pick_business()
            if not business.website:
                raise RuntimeError("sampled_business_missing_website")
            scan_out_dir = out_dir / f"scan_attempt_{scan_attempt}"
            scan = run_scan_pipeline(settings=settings, website=str(business.website), out_dir=scan_out_dir)

    research_db.begin_iteration(
        iteration_id=iteration_id,
        business_id=int(business.entity_detail_id),
        business_name=str(business.business_name),
        website=str(business.website),
        config={
            "scan_depth": "deep_active",
            "model_policy": "hybrid_codex_oss",
            "report_style": "exec_plus_technical",
            "strategy_version": int(strategy.get("version", 1)),
            "report_depth_level": int(strategy.get("report_depth_level", 1) or 1),
            "sales_sim_target_count": sales_sim_target_count,
            "sales_turn_count": max(4, min(8, int(strategy.get("sales_turn_count", 5) or 5))),
        },
    )

    try:
        findings = scan["findings"]
        max_report_attempts = max(1, min(3, int(strategy.get("max_report_attempts", 2) or 2)))
        attempt_strategy = dict(strategy)
        attempt_rows: list[dict[str, Any]] = []
        report: dict[str, Any] = {}
        pdf_info: dict[str, Any] = {}
        score = None
        chosen_attempt_dir = out_dir

        for attempt_no in range(1, max_report_attempts + 1):
            attempt_dir = out_dir / f"report_attempt_{attempt_no}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            report = build_report_payload(
                settings=settings,
                business=business,
                scan_payload=scan,
                out_dir=attempt_dir,
                strategy=attempt_strategy,
            )
            pdf_info = build_pdf_report(report, attempt_dir)
            pdf_info["report_word_count"] = int((report.get("meta") or {}).get("total_word_count") or 0)
            pdf_info["report_depth_level"] = int((report.get("meta") or {}).get("report_depth_level") or 1)
            score = evaluate_report(
                findings=findings,
                pdf_info=pdf_info,
                min_findings=dict(attempt_strategy.get("min_findings") or {}),
            )
            attempt_rows.append(
                {
                    "attempt": attempt_no,
                    "value_score": round(float(score.value_score), 2),
                    "accuracy_score": round(float(score.accuracy_score), 2),
                    "aesthetic_score": round(float(score.aesthetic_score), 2),
                    "pass_gate": bool(score.pass_gate),
                    "reasons": list(score.reasons or []),
                    "report_word_count": int(pdf_info.get("report_word_count") or 0),
                    "report_depth_level": int(pdf_info.get("report_depth_level") or 1),
                    "report_pdf_path": str(pdf_info.get("pdf_path") or ""),
                    "strategy_version": int(attempt_strategy.get("version", strategy.get("version", 1)) or 1),
                }
            )
            chosen_attempt_dir = attempt_dir
            if score.pass_gate:
                break
            if attempt_no < max_report_attempts:
                attempt_strategy = _local_retry_strategy(
                    current_strategy=attempt_strategy,
                    score_reasons=list(score.reasons or []),
                )

        (out_dir / "report_attempts.json").write_text(
            json.dumps({"attempts": attempt_rows}, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

        final_pdf = Path(str(pdf_info.get("pdf_path") or ""))
        final_html = Path(str(pdf_info.get("html_path") or ""))
        final_json = Path(str(pdf_info.get("json_path") or ""))
        if final_pdf.exists():
            (out_dir / "report.pdf").write_bytes(final_pdf.read_bytes())
        if final_html.exists():
            (out_dir / "report.html").write_text(final_html.read_text(encoding="utf-8"), encoding="utf-8")
        if final_json.exists():
            (out_dir / "report.json").write_text(final_json.read_text(encoding="utf-8"), encoding="utf-8")

        first_attempt_score = float(attempt_rows[0]["value_score"]) if attempt_rows else 0.0
        final_attempt_score = float(attempt_rows[-1]["value_score"]) if attempt_rows else 0.0
        attempt_delta = final_attempt_score - first_attempt_score

        _sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
        highlights = [
            f.title for f in sorted(
                findings,
                key=lambda x: (_sev_rank.get(x.severity, 0), float(x.confidence)),
                reverse=True,
            )[:6]
        ]
        # Prioritise least-covered personas so all 10 scenarios are exercised evenly across iterations
        persona_coverage = dict(strategy.get("persona_coverage") or {})
        persona_pressure = dict(strategy.get("persona_pressure") or {})
        preferred = preferred_persona_order(persona_coverage, persona_pressure)
        sales_turn_count = max(4, min(8, int(strategy.get("sales_turn_count", 5) or 5)))
        sims = run_sales_simulation(
            settings=settings,
            business=business,
            report_highlights=highlights,
            preferred_personas=preferred,
            scenario_count=sales_sim_target_count,
            persona_pressure=persona_pressure,
            max_turn_count=sales_turn_count,
        )

        # Persist readable email simulation turns per iteration.
        sim_txt = out_dir / "sales_simulations.txt"
        sim_lines: list[str] = []
        for sim in sims:
            sim_lines.append(f"=== Scenario: {sim.scenario_key} ({sim.persona}) ===")
            sim_lines.append(
                f"scores: close={sim.score_close:.1f} trust={sim.score_trust:.1f} objection={sim.score_objection:.1f}"
            )
            for turn in sim.turns:
                role = str(turn.get("role") or "").upper()
                text = str(turn.get("text") or "").strip()
                sim_lines.append(f"{role}: {text}")
            sim_lines.append("")
        sim_txt.write_text("\n".join(sim_lines).strip() + "\n", encoding="utf-8")

        # Compute sales sim summary scores for strategy adaptation.
        sim_avg_close = sum(s.score_close for s in sims) / len(sims) if sims else 0.0
        sim_avg_trust = sum(s.score_trust for s in sims) / len(sims) if sims else 0.0
        sim_avg_objection = sum(s.score_objection for s in sims) / len(sims) if sims else 0.0
        worst_scenario = ""
        worst_total = 0.0
        if sims:
            scored = sorted(
                (
                    (
                        (float(s.score_close) + float(s.score_trust) + float(s.score_objection)) / 3.0,
                        s.scenario_key,
                    )
                    for s in sims
                ),
                key=lambda item: item[0],
            )
            worst_total, worst_scenario = scored[0]
        sales_score_summary: dict[str, float] | None = None
        if sims:
            sales_score_summary = {
                "avg_close": sim_avg_close,
                "avg_trust": sim_avg_trust,
                "avg_objection": sim_avg_objection,
                "worst_scenario_key": worst_scenario,
                "worst_scenario_total": worst_total,
            }

        # Write local summary markdown for quick morning review.
        summary_md = out_dir / "summary.md"
        value_model = dict(report.get("value_model") or {})
        base_scenario = next(
            (row for row in list(value_model.get("scenarios") or []) if str(row.get("name") or "").lower() == "base"),
            None,
        )
        summary_md.write_text(
            "\n".join(
                [
                    f"# Iteration {iteration_id}",
                    "",
                    f"Business: {business.business_name}",
                    f"Website: {business.website}",
                    f"Value score: {score.value_score:.1f}",
                    f"Accuracy score: {score.accuracy_score:.1f}",
                    f"Aesthetic score: {score.aesthetic_score:.1f}",
                    f"Pass gate: {'PASS' if score.pass_gate else 'FAIL'}",
                    f"Findings: {len(findings)} unique ({sum(1 for f in findings if f.severity in {'high', 'critical'})} high/critical)",
                    f"Report depth: {pdf_info['report_depth_level']}/5 | Approx words: {pdf_info['report_word_count']}",
                    f"Report generation attempts: {len(attempt_rows)} (value delta: {attempt_delta:+.1f})",
                    f"Final attempt path: {chosen_attempt_dir}",
                    (
                        "Estimated base-case upside: "
                        f"${int(base_scenario.get('incremental_revenue_monthly_usd') or 0):,}/month, "
                        f"payback {int(base_scenario.get('payback_days_for_report_fee') or 0)} day(s)"
                        if isinstance(base_scenario, dict)
                        else "Estimated base-case upside: unavailable"
                    ),
                    (
                        f"Resampled businesses: {len(attempted_ids)} due to blocked/unreachable scan targets"
                        if attempted_ids
                        else "Resampled businesses: 0"
                    ),
                    "",
                    "Sales sim scores:",
                    f"  Close: {sim_avg_close:.1f} | Trust: {sim_avg_trust:.1f} | Objection: {sim_avg_objection:.1f}",
                    f"  Scenarios: {len(sims)} (target: {sales_sim_target_count})",
                    "",
                    "Top findings:",
                ]
                + [f"- [{f.severity.upper()}] {f.title} ({f.category})" for f in findings[:12]]
                + (["", "Reasons for gate failure:"] + [f"- {r}" for r in score.reasons] if not score.pass_gate and score.reasons else [])
            ),
            encoding="utf-8",
        )

        iter_status = "completed" if score.pass_gate else "needs_improvement"
        result = IterationResult(
            iteration_id=iteration_id,
            entity_detail_id=int(business.entity_detail_id),
            business_name=str(business.business_name),
            website=str(business.website),
            status=iter_status,
            findings=findings,
            report_json_path=str(Path(pdf_info["json_path"])),
            report_html_path=str(Path(pdf_info["html_path"])),
            report_pdf_path=str(Path(pdf_info["pdf_path"])),
            score=score,
            sales_scenarios=sims,
            report_word_count=int(pdf_info.get("report_word_count") or 0),
            report_depth_level=int(pdf_info.get("report_depth_level") or 1),
            sales_avg_close=float(sim_avg_close),
            sales_avg_trust=float(sim_avg_trust),
            sales_avg_objection=float(sim_avg_objection),
            roi_base_monthly_upside=int(base_scenario.get("incremental_revenue_monthly_usd") or 0)
            if isinstance(base_scenario, dict)
            else 0,
            roi_base_payback_days=int(base_scenario.get("payback_days_for_report_fee") or 0)
            if isinstance(base_scenario, dict)
            else 0,
            report_attempt_count=max(1, len(attempt_rows)),
        )
        research_db.record_iteration_result(result)

        next_memory = adapt_strategy(previous_memory=strategy, score=score, sales_scores=sales_score_summary)
        # Update persona coverage so the next iteration can rebalance scenario selection
        updated_coverage = dict(next_memory.get("persona_coverage") or {})
        for sim in sims:
            updated_coverage[sim.scenario_key] = updated_coverage.get(sim.scenario_key, 0) + 1
        next_memory["persona_coverage"] = updated_coverage
        research_db.write_strategy(next_memory)
        research_db.finish_iteration(iteration_id=iteration_id, status=iter_status)

        # Extra machine-readable summary.
        (out_dir / "iteration_result.json").write_text(
            json.dumps(
                {
                    "iteration": asdict(result),
                    "pdf_info": pdf_info,
                    "report_attempts": attempt_rows,
                    "strategy_next": next_memory,
                },
                indent=2,
                ensure_ascii=True,
                default=str,
            ),
            encoding="utf-8",
        )
        return result
    except Exception as exc:
        research_db.finish_iteration(iteration_id=iteration_id, status="failed")
        (out_dir / "error.txt").write_text(str(exc), encoding="utf-8")
        raise
