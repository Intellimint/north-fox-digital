from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4

from .config import AgentSettings
from .db import OpsDB
from .deliverability.precheck_pipeline import DeliverabilityVerifier
from .learning.summary_writer import write_run_summary
from .offers.catalog import default_offer_variants, default_offers
from .selection import record_selected_attempts, select_prospects_for_offer
from .source_sbs import SourceProspectRepository


def bootstrap_offers(ops_db: OpsDB) -> None:
    for offer in default_offers():
        ops_db.upsert_offer(offer=asdict(offer))
    for variant in default_offer_variants():
        ops_db.upsert_offer_variant(variant=asdict(variant))


def run_orchestrator(settings: AgentSettings, *, slot: str, dry_run: bool | None = None) -> dict:
    if dry_run is None:
        dry_run = settings.dry_run_default
    started_at = datetime.now(timezone.utc)
    run_id = str(uuid4())
    run_type = f"daily_{slot}"
    ops_db = OpsDB(settings.ops_db_path)
    settings.ensure_dirs()
    ops_db.init_db()
    bootstrap_offers(ops_db)

    local_now = started_at.astimezone(ZoneInfo(settings.timezone_name))
    local_send_date = local_now.date().isoformat()
    source_repo = SourceProspectRepository(settings.sbs_db_path)
    verifier = DeliverabilityVerifier(settings, ops_db)

    decisions = {
        "slot": slot,
        "dry_run": dry_run,
        "offers": ["dsbs_rewrite_v1", "capability_statement_v1"],
        "split": "even",
        "next_run_improvements": [],
    }
    ops_db.begin_campaign_run(run_id, run_type, {"classifier": "rule_v1", "ollama_model": settings.ollama_model}, decisions)

    selections_by_offer = {}
    prechecks_created = 0
    selected_total = 0
    offer_map = {offer.offer_key: offer for offer in default_offers()}
    selected_emails_run: set[str] = set()
    for offer_key in decisions["offers"]:
        offer = offer_map[offer_key]
        total_today = ops_db.count_attempts_for_local_date(local_send_date=local_send_date)
        offer_today = ops_db.count_attempts_for_local_date(local_send_date=local_send_date, offer_key=offer_key)
        remaining_total = max(0, int(settings.daily_total_initial_cap) - int(total_today))
        remaining_offer = max(0, int(settings.daily_offer_cap) - int(offer_today))
        selection_limit = min(int(settings.per_run_offer_cap), remaining_total, remaining_offer)
        if selection_limit <= 0:
            selections_by_offer[offer_key] = []
            continue
        selections = select_prospects_for_offer(
            source_repo=source_repo,
            ops_db=ops_db,
            offer=offer,
            limit=selection_limit,
        )
        deduped_selections = []
        for sel in selections:
            key = str(sel.features.email or "").strip().lower()
            if not key:
                continue
            if key in selected_emails_run:
                continue
            selected_emails_run.add(key)
            deduped_selections.append(sel)
        selections_by_offer[offer_key] = deduped_selections
        attempt_ids = record_selected_attempts(
            ops_db=ops_db,
            run_id=run_id,
            selections=deduped_selections,
            local_send_date=local_send_date,
        )
        selected_total += len(attempt_ids)
        for sel, attempt_id in zip(deduped_selections, attempt_ids):
            verifier.send_precheck(prospect=sel.features, attempt_id=attempt_id, dry_run=dry_run)
            prechecks_created += 1

    metrics = {
        "selected_total": selected_total,
        "prechecks_created": prechecks_created,
        "slot": slot,
        "dry_run": dry_run,
    }
    summary_path = write_run_summary(
        settings=settings,
        run_id=run_id,
        slot=slot,
        started_at=started_at,
        metrics=metrics,
        decisions=decisions,
    )
    ops_db.finish_campaign_run(run_id, str(summary_path), "completed", decisions=decisions)
    return {"ok": True, "run_id": run_id, "summary_path": str(summary_path), "metrics": metrics}
