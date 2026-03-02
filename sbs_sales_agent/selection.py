from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import uuid4

from .db import OpsDB
from .features import features_from_sbs_row, is_valid_email, normalize_email, normalize_website, prospect_snapshot
from .models import Offer, ProspectSelection
from .scoring import score_for_offer
from .source_sbs import SourceProspectRepository


COOLDOWN_DAYS_NONRESPONDER = 183


def eligible_for_initial_outreach(*, ops_db: OpsDB, entity_id: int, email_normalized: str) -> tuple[bool, str]:
    if ops_db.is_suppressed(email_normalized):
        return False, "suppressed"
    if ops_db.recent_nonresponse_cooldown_hit(entity_id, email_normalized):
        return False, "cooldown_active"
    return True, "eligible"


def _offer_variant_cycle(ops_db: OpsDB, offer_key: str) -> list[str]:
    variants = ops_db.list_active_offer_variants(offer_key)
    if not variants:
        return []
    return [str(row["variant_key"]) for row in variants]


def _has_capability_statement_listing(features) -> bool:
    # Use SBA source data only here (cheap + deterministic). If anything indicates an existing capability
    # statement, skip this offer to avoid pitching a solved problem.
    raw = features.raw_json or {}
    for key, value in raw.items():
        k = str(key).lower()
        if "capability" in k and "statement" in k:
            if isinstance(value, bool):
                if value:
                    return True
            elif isinstance(value, str):
                v = value.strip().lower()
                if v and v not in {"none", "null", "n/a", "na", "false", "0"}:
                    return True
            elif value is not None:
                return True
    # Some records place this in free text/description fields.
    narrative = (features.capabilities_narrative or "").lower()
    if "capability statement" in narrative:
        return True
    for kw in features.keywords:
        if "capability statement" in kw.lower():
            return True
    return False


def select_prospects_for_offer(
    *,
    source_repo: SourceProspectRepository,
    ops_db: OpsDB,
    offer: Offer,
    limit: int,
    scan_limit: int = 10000,
) -> list[ProspectSelection]:
    selections: list[ProspectSelection] = []
    variants = _offer_variant_cycle(ops_db, offer.offer_key) or ["default"]
    variant_idx = 0
    scanned = 0
    offset = 0
    seen_emails: set[str] = set()

    while len(selections) < limit and scanned < scan_limit:
        rows = source_repo.select_candidates(limit=min(500, scan_limit - scanned), offset=offset)
        if not rows:
            break
        offset += len(rows)
        scanned += len(rows)
        for row in rows:
            features = features_from_sbs_row(row)
            if not is_valid_email(features.email):
                continue
            email_normalized = normalize_email(features.email)
            if not email_normalized:
                continue
            if email_normalized in seen_emails:
                continue
            ok, reason = eligible_for_initial_outreach(
                ops_db=ops_db,
                entity_id=features.entity_detail_id,
                email_normalized=email_normalized,
            )
            ops_db.upsert_prospect_state(
                {
                    "source_entity_detail_id": features.entity_detail_id,
                    "email_normalized": email_normalized,
                    "contact_name_raw": features.contact_name_raw,
                    "contact_name_normalized": features.contact_name_normalized,
                    "business_name": features.business_name,
                    "website_normalized": normalize_website(features.website),
                    "state": features.state,
                    "source_snapshot_json": prospect_snapshot(features),
                    "eligible_flag": ok,
                    "eligibility_reason": reason,
                }
            )
            if not ok:
                continue
            if ops_db.recent_offer_contact_hit(
                source_entity_detail_id=features.entity_detail_id,
                email_normalized=email_normalized,
                offer_key=offer.offer_key,
                lookback_days=COOLDOWN_DAYS_NONRESPONDER,
            ):
                continue
            if offer.offer_key == "capability_statement_v1" and _has_capability_statement_listing(features):
                continue
            score = score_for_offer(features, offer)
            if score.total < 0:
                continue
            variant_key = variants[variant_idx % len(variants)]
            variant_idx += 1
            selections.append(
                ProspectSelection(features=features, offer_key=offer.offer_key, variant_key=variant_key, score=score)
            )
            seen_emails.add(email_normalized)
            if len(selections) >= limit:
                break
    selections.sort(key=lambda item: item.score.total, reverse=True)
    return selections[:limit]


def record_selected_attempts(
    *,
    ops_db: OpsDB,
    run_id: str,
    selections: Iterable[ProspectSelection],
    local_send_date: str,
) -> list[str]:
    now = datetime.now(timezone.utc)
    cooldown_until = (now + timedelta(days=COOLDOWN_DAYS_NONRESPONDER)).isoformat()
    attempt_ids: list[str] = []
    for item in selections:
        attempt_id = str(uuid4())
        attempt_ids.append(attempt_id)
        ops_db.create_attempt(
            {
                "attempt_id": attempt_id,
                "source_entity_detail_id": item.features.entity_detail_id,
                "email_normalized": item.features.email.lower(),
                "offer_key": item.offer_key,
                "variant_key": item.variant_key,
                "run_id": run_id,
                "status": "selected",
                "send_window_local_date": local_send_date,
                "cooldown_until": cooldown_until,
                "score_json": {
                    "total": item.score.total,
                    "components": item.score.components,
                },
                "selection_reasons_json": {"reasons": item.score.reasons},
            }
        )
    return attempt_ids
