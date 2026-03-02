from __future__ import annotations

import json
import datetime as dt
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import AgentSettings
from .db import OpsDB, utcnow_iso
from .deliverability.precheck_pipeline import DeliverabilityVerifier
from .features import features_from_sbs_row
from .fulfillment.context_enrichment import fetch_website_context
from .fulfillment.capability_statement import build_capability_statement_artifacts
from .fulfillment.dsbs_rewrite import build_dsbs_rewrite_artifacts
from .fulfillment.quality import validate_capability_artifacts, validate_dsbs_artifacts
from .integrations.agentmail import AgentMailClient
from .integrations.square_client import SquareClient
from .offers.catalog import default_offers
from .offers.generator import build_initial_outreach
from .research_loop.business_sampler import SampledBusiness
from .research_loop.report_builder import build_report_payload
from .research_loop.report_pdf import build_pdf_report
from .research_loop.scan_pipeline import run_scan_pipeline
from .payments.reconcile import reconcile_square_payments
from .source_sbs import SourceProspectRepository
from .surveys.email_survey import survey_body, survey_subject


def _within_initial_send_window(settings: AgentSettings, when_utc: datetime) -> bool:
    local = when_utc.astimezone(ZoneInfo(settings.timezone_name))
    return settings.initial_send_start_hour_local <= local.hour < settings.initial_send_end_hour_local


def _light_scan_cache_key(entity_id: int) -> str:
    return f"light_scan:{entity_id}"


def _extract_light_findings(scan_payload: dict[str, Any], *, max_items: int = 3) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    for item in scan_payload.get("findings") or []:
        if hasattr(item, "title"):  # dataclass ScanFinding
            title = str(item.title or "").strip()
            severity = str(item.severity or "medium").lower()
            category = str(item.category or "")
        elif isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            severity = str(item.get("severity") or "medium").lower()
            category = str(item.get("category") or "")
        else:
            continue
        if not title:
            continue
        rows.append({"title": title, "severity": severity, "category": category})
    dedup: dict[str, dict[str, str]] = {}
    for r in rows:
        key = str(r["title"]).lower()
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = r
            continue
        if sev_rank.get(str(r.get("severity") or ""), 0) > sev_rank.get(str(prev.get("severity") or ""), 0):
            dedup[key] = r
    total_findings = len(rows)
    picked = sorted(list(dedup.values()), key=lambda r: sev_rank.get(r["severity"], 0), reverse=True)
    out = picked[:max_items]
    for item in out:
        item["total_findings"] = str(total_findings)
    return out


def _existing_file_paths(values: list[Any]) -> list[Path]:
    files: list[Path] = []
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        p = Path(text)
        if p.exists() and p.is_file():
            files.append(p)
    return files


def _get_or_run_light_scan(
    *,
    settings: AgentSettings,
    ops_db: OpsDB,
    prospect,
) -> list[dict[str, str]]:
    if not prospect.website:
        return []
    key = _light_scan_cache_key(int(prospect.entity_detail_id))
    raw = ops_db.get_runtime_kv(key)
    if raw:
        try:
            payload = json.loads(raw)
            ts = str(payload.get("ts") or "")
            cached_findings = payload.get("findings") or []
            if ts:
                age = datetime.now(timezone.utc) - dt.datetime.fromisoformat(ts)
                if age.total_seconds() < 14 * 24 * 3600 and isinstance(cached_findings, list):
                    return cached_findings
        except Exception:
            pass
    try:
        out_dir = settings.logs_dir / "light_scans" / datetime.now(timezone.utc).strftime("%Y-%m-%d") / f"entity_{prospect.entity_detail_id}"
        scan = run_scan_pipeline(settings=settings, website=str(prospect.website), out_dir=out_dir, mode="light")
        findings = _extract_light_findings(scan, max_items=3)
        ops_db.set_runtime_kv(
            key,
            json.dumps(
                {"ts": datetime.now(timezone.utc).isoformat(), "findings": findings},
                ensure_ascii=True,
            ),
        )
        return findings
    except Exception:
        return []


def process_due_prechecks(settings: AgentSettings, dry_run: bool = False) -> dict[str, Any]:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    verifier = DeliverabilityVerifier(settings, ops_db)
    due = ops_db.due_prechecks()
    safe = 0
    suppressed = 0
    for row in due:
        result = verifier.evaluate_precheck_window(str(row["precheck_id"]), dry_run=dry_run)
        if not result.get("ok"):
            continue
        if result["decision"] == "safe_to_send_main":
            safe += 1
            if row["attempt_id"]:
                ops_db.update_attempt_status(str(row["attempt_id"]), "precheck_passed")
        elif result["decision"] == "suppress":
            suppressed += 1
            ops_db.suppress_email(
                suppression_id=str(uuid4()),
                email_normalized=str(row["email_normalized"]),
                reason=str(result["reason"]),
                source_entity_detail_id=int(row["source_entity_detail_id"]),
                source_event_id=str(row["precheck_id"]),
            )
            if row["attempt_id"]:
                ops_db.update_attempt_status(str(row["attempt_id"]), "suppressed")
    return {"ok": True, "due": len(due), "safe": safe, "suppressed": suppressed}


def dispatch_scheduled_messages(settings: AgentSettings, dry_run: bool = False) -> dict[str, Any]:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    mail = AgentMailClient(settings)
    sent = 0
    skipped = 0
    failed = 0
    for row in ops_db.due_outbound_messages():
        msg_id = str(row["message_id"])
        try:
            meta = json.loads(row["metadata_json"] or "{}")
            queued_type = str(meta.get("queued_type") or "")
            if queued_type == "initial" and not _within_initial_send_window(settings, datetime.now(timezone.utc)):
                skipped += 1
                continue
            if dry_run:
                result = {"message_id": f"dry-{msg_id}", "thread_id": meta.get("provider_thread_id")}
            else:
                reply_to_id = meta.get("reply_to_message_id")
                if reply_to_id:
                    try:
                        result = mail.reply_message(
                            inbox_id=str(row["mailbox"]),
                            message_id=str(reply_to_id),
                            text=str(row["body_text"]),
                        )
                    except Exception:
                        result = mail.send_message(
                            inbox_id=str(row["mailbox"]),
                            to=[str(row["recipient_email"])],
                            subject=str(row["subject"] or ""),
                            text=str(row["body_text"]),
                            thread_id=meta.get("provider_thread_id"),
                        )
                else:
                    result = mail.send_message(
                        inbox_id=str(row["mailbox"]),
                        to=[str(row["recipient_email"])],
                        subject=str(row["subject"] or ""),
                        text=str(row["body_text"]),
                        thread_id=meta.get("provider_thread_id"),
                    )
            ops_db.mark_email_sent(
                message_id=msg_id,
                provider_message_id=str(result.get("message_id") or result.get("id") or ""),
                provider_thread_id=str(result.get("thread_id") or ""),
            )
            sent += 1
        except Exception:
            ops_db.update_email_delivery_status(message_id=msg_id, delivery_status="failed")
            failed += 1
    return {"ok": True, "sent": sent, "skipped": skipped, "failed": failed}


def send_main_outreach_from_passed_prechecks(settings: AgentSettings, *, run_id: str, dry_run: bool = False) -> dict[str, Any]:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    source_repo = SourceProspectRepository(settings.sbs_db_path)
    mail = AgentMailClient(settings)
    offers = {o.offer_key: o for o in default_offers()}
    sent = 0
    queued = 0
    failed = 0
    if not _within_initial_send_window(settings, datetime.now(timezone.utc)):
        return {"ok": True, "sent": 0, "processed_attempts": 0, "reason": "outside_initial_send_window"}
    for attempt in ops_db.list_attempts_for_run(run_id):
        message_id: str | None = None
        try:
            if str(attempt["status"]) != "precheck_passed":
                continue
            existing_initial = ops_db.find_initial_outbound_for_attempt(str(attempt["attempt_id"]))
            if existing_initial is not None:
                existing_status = str(existing_initial["delivery_status"] or "")
                if existing_status in {"sending", "sent", "queued"}:
                    if existing_status == "sent":
                        ops_db.update_attempt_status(str(attempt["attempt_id"]), "main_sent")
                    continue
            source_row = source_repo.get_prospect(int(attempt["source_entity_detail_id"]))
            if source_row is None:
                continue
            prospect = features_from_sbs_row(source_row)
            variant_rows = ops_db.list_active_offer_variants(str(attempt["offer_key"]))
            variant_row = next((r for r in variant_rows if str(r["variant_key"]) == str(attempt["variant_key"])), None)
            if variant_row is None:
                continue
            from .models import OfferVariant

            variant = OfferVariant(
                variant_key=str(variant_row["variant_key"]),
                offer_key=str(attempt["offer_key"]),
                subject_template=str(variant_row["subject_template"]),
                body_template=str(variant_row["body_template"]),
                style_tags=[],
                status=str(variant_row["status"]),
            )
            light_findings = _get_or_run_light_scan(settings=settings, ops_db=ops_db, prospect=prospect)
            subject, body = build_initial_outreach(
                settings=settings,
                offer=offers[str(attempt["offer_key"])],
                variant=variant,
                prospect=prospect,
                light_findings=light_findings,
            )
            existing_conv = ops_db.find_conversation_by_attempt(str(attempt["attempt_id"]))
            conversation_id = str(existing_conv["conversation_id"]) if existing_conv else str(uuid4())
            if existing_conv is None:
                ops_db.upsert_conversation(
                    conversation_id=conversation_id,
                    source_entity_detail_id=prospect.entity_detail_id,
                    email_normalized=prospect.email.lower(),
                    offer_key=str(attempt["offer_key"]),
                    attempt_id=str(attempt["attempt_id"]),
                    agentmail_inbox=settings.agentmail_sales_inbox,
                    state="awaiting_reply",
                    thread_metadata={},
                )
            message_id = str(uuid4())
            ops_db.insert_email_message(
                {
                    "message_id": message_id,
                    "channel": "agentmail",
                    "direction": "outbound",
                    "mailbox": settings.agentmail_sales_inbox,
                    "subject": subject,
                    "body_text": body,
                    "recipient_email": prospect.email,
                    "sender_email": settings.agentmail_sales_inbox,
                    "attempt_id": str(attempt["attempt_id"]),
                    "conversation_id": conversation_id,
                    "delivery_status": "sending",
                    "metadata_json": {"queued_type": "initial", "light_scan_findings": light_findings},
                }
            )
            if dry_run:
                result = {"message_id": f"dry-{conversation_id}", "thread_id": f"dry-thread-{conversation_id}"}
            else:
                result = mail.send_message(
                    inbox_id=settings.agentmail_sales_inbox,
                    to=[prospect.email],
                    subject=subject,
                    text=body,
                )
            ops_db.mark_email_sent(
                message_id=message_id,
                provider_message_id=str(result.get("message_id") or result.get("id") or ""),
                provider_thread_id=str(result.get("thread_id") or ""),
            )
            ops_db.update_attempt_status(str(attempt["attempt_id"]), "main_sent")
            sent += 1
            queued += 1
        except Exception:
            if message_id is not None:
                ops_db.update_email_delivery_status(message_id=message_id, delivery_status="failed")
            failed += 1
            continue
    return {"ok": True, "sent": sent, "processed_attempts": queued, "failed": failed}


def trigger_invoice_for_conversation(settings: AgentSettings, *, conversation_id: str, customer_email: str, customer_name: str, offer_key: str, amount_cents: int, dry_run: bool = False) -> dict[str, Any]:
    ops_db = OpsDB(settings.ops_db_path)
    offer_labels = {
        "dsbs_rewrite_v1": "Web Presence Risk + Revenue Growth Report",
        "capability_statement_v1": "Web Presence Risk + Revenue Growth Report",
        "web_presence_report_v1": "Web Presence Risk + Revenue Growth Report",
        "web_presence_report_fastfix_v1": "Web Presence Risk + Revenue Growth Report",
        "supplier_diversity_kit_v1": "Supplier Diversity Inbound Kit",
        "naics_audit_v1": "NAICS Cleanup & Positioning Audit",
        "dmarc_trust_fix_v1": "Website Trust & Email Deliverability Fix",
    }
    line_item_name = offer_labels.get(offer_key, offer_key.replace("_", " ").title())
    invoice_description = f"{line_item_name} | fixed-price service"
    if dry_run:
        invoice_obj = {"id": f"dry-inv-{conversation_id[:8]}", "invoice_number": "DRY-001", "public_url": "https://example.com/invoice"}
        order_obj = {"id": f"dry-order-{conversation_id[:8]}"}
        customer_obj = {"id": f"dry-cust-{conversation_id[:8]}"}
    else:
        square = SquareClient(settings)
        bundle = square.create_and_publish_invoice(
            customer_email=customer_email,
            customer_name=customer_name,
            title=f"{settings.sender_company_name} - {offer_key}",
            amount_cents=amount_cents,
            line_item_name=line_item_name,
            description=invoice_description,
            reference=conversation_id[:8],
        )
        invoice_obj = bundle.get("invoice") or {}
        order_obj = bundle.get("order") or {}
        customer_obj = bundle.get("customer") or {}
    payment_id = str(uuid4())
    ops_db.create_payment_record(
        {
            "payment_id": payment_id,
            "conversation_id": conversation_id,
            "amount_cents": amount_cents,
            "status": "requested",
            "invoice_sent_at": utcnow_iso(),
            "square_customer_id": customer_obj.get("id"),
            "square_order_id": order_obj.get("id"),
            "square_invoice_id": invoice_obj.get("id"),
            "square_invoice_number": invoice_obj.get("invoice_number"),
            "square_public_url": invoice_obj.get("public_url"),
            "metadata_json": {"offer_key": offer_key},
        }
    )
    return {"ok": True, "payment_id": payment_id, "invoice": invoice_obj}


def run_fulfillment_jobs(settings: AgentSettings) -> dict[str, Any]:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    jobs = ops_db.pending_fulfillment_jobs()
    completed = 0
    failed = 0
    for row in jobs:
        job_id = str(row["job_id"])
        ops_db.update_fulfillment_job(job_id, status="running")
        try:
            inputs = json.loads(row["inputs_json"] or "{}")
            offer_key = str(row["offer_key"])
            out_dir = settings.artifacts_dir / job_id
            source_repo = SourceProspectRepository(settings.sbs_db_path)
            prospect_row = source_repo.get_prospect(int(inputs["source_entity_detail_id"]))
            if prospect_row is None:
                ops_db.update_fulfillment_job(job_id, status="failed", artifacts_json={"reason": "source_row_missing"})
                failed += 1
                continue
            prospect = features_from_sbs_row(prospect_row)
            website_context = fetch_website_context(settings, prospect)
            with ops_db.session() as conn:
                offer_row = conn.execute(
                    "SELECT offer_type FROM offers WHERE offer_key = ? LIMIT 1",
                    (offer_key,),
                ).fetchone()
            offer_type = str((offer_row["offer_type"] if offer_row else "") or "")
            if offer_type == "WEB_PRESENCE_REPORT":
                if not prospect.website:
                    ops_db.update_fulfillment_job(
                        job_id,
                        status="failed",
                        artifacts_json={"reason": "missing_website_for_web_report"},
                    )
                    failed += 1
                    continue
                scan_payload = run_scan_pipeline(
                    settings=settings,
                    website=str(prospect.website),
                    out_dir=out_dir / "scan",
                    mode="deep",
                )
                report = build_report_payload(
                    settings=settings,
                    business=SampledBusiness(
                        entity_detail_id=int(prospect.entity_detail_id),
                        business_name=str(prospect.business_name),
                        website=str(prospect.website),
                        contact_name=str(prospect.contact_name_normalized or prospect.first_name_for_greeting or ""),
                        email=str(prospect.email or ""),
                    ),
                    scan_payload=scan_payload,
                    out_dir=out_dir,
                    strategy={"report_depth_level": 2, "report_word_target": 1600, "min_findings": {}},
                )
                pdf_info = build_pdf_report(report, out_dir)
                pdf_path = Path(str(pdf_info.get("pdf_path") or ""))
                if not pdf_path.exists():
                    ops_db.update_fulfillment_job(
                        job_id,
                        status="failed",
                        artifacts_json={"reason": "web_report_pdf_missing"},
                    )
                    failed += 1
                    continue
                result = {
                    "artifacts": [
                        str(p)
                        for p in _existing_file_paths(
                            [
                                str(pdf_path),
                                pdf_info.get("html_path"),
                                pdf_info.get("json_path"),
                            ]
                        )
                    ],
                    "renderer": str(pdf_info.get("renderer") or ""),
                }
                quality = {"ok": True}
            elif offer_key.startswith("dsbs_rewrite"):
                result = build_dsbs_rewrite_artifacts(
                    prospect=prospect, out_dir=out_dir, settings=settings, website_context=website_context
                )
                quality = validate_dsbs_artifacts(result)
                if not quality.get("ok"):
                    # Redundancy fallback: deterministic local generation if Codex output is invalid.
                    result = build_dsbs_rewrite_artifacts(
                        prospect=prospect, out_dir=out_dir, settings=None, website_context=website_context
                    )
                    quality = validate_dsbs_artifacts(result)
            else:
                result = build_capability_statement_artifacts(
                    prospect=prospect, out_dir=out_dir, settings=settings, website_context=website_context
                )
                quality = validate_capability_artifacts(result)
                if not quality.get("ok"):
                    result = build_capability_statement_artifacts(
                        prospect=prospect, out_dir=out_dir, settings=None, website_context=website_context
                    )
                    quality = validate_capability_artifacts(result)
            result["website_context"] = website_context
            result["quality"] = quality
            if not quality.get("ok"):
                ops_db.update_fulfillment_job(job_id, status="failed", artifacts_json=result)
                failed += 1
                continue
            ops_db.update_fulfillment_job(job_id, status="completed", artifacts_json=result)
            completed += 1
        except Exception as exc:
            ops_db.update_fulfillment_job(
                job_id,
                status="failed",
                artifacts_json={"reason": "fulfillment_exception", "error": str(exc)[:500]},
            )
            failed += 1
    return {"ok": True, "completed": completed, "failed": failed, "scanned": len(jobs)}


def reconcile_payments(settings: AgentSettings, dry_run: bool = False) -> dict[str, Any]:
    return reconcile_square_payments(settings, OpsDB(settings.ops_db_path), dry_run=dry_run)


def send_fulfillment_and_survey(settings: AgentSettings, *, dry_run: bool = False) -> dict[str, Any]:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    mail = AgentMailClient(settings)
    sent_delivery = 0
    sent_survey = 0
    failed = 0
    with ops_db.session() as conn:
        rows = conn.execute(
            """
            SELECT j.job_id, j.conversation_id, j.offer_key, j.artifacts_json,
                   c.email_normalized, c.agentmail_inbox, c.conversation_state
            FROM fulfillment_jobs j
            JOIN conversations c ON c.conversation_id = j.conversation_id
            WHERE j.status IN ('completed','delivery_sent')
            ORDER BY j.created_at
            LIMIT 100
            """
        ).fetchall()
    for row in rows:
        artifacts: dict[str, Any] = {}
        try:
            artifacts = json.loads(row["artifacts_json"] or "{}")
            if not isinstance(artifacts, dict):
                artifacts = {}
            delivery_done = bool(str(artifacts.get("delivery_email_message_id") or "").strip())
            survey_done = bool(str(artifacts.get("survey_email_message_id") or "").strip())
            artifact_list = _existing_file_paths(list(artifacts.get("artifacts") or []))
            pdf_attachments = [p for p in artifact_list if p.suffix.lower() == ".pdf"]
            body = (
                "Done — I finished your deliverable and attached/summarized the outputs below.\n\n"
                + "\n".join(f"- {p.name}" for p in (pdf_attachments or artifact_list)[:10])
                + "\n\nIf you want any tweaks, reply here and I can adjust it."
            )
            latest_inbound_provider_message_id: str | None = None
            latest_provider_thread_id: str | None = None
            with ops_db.session() as conn:
                inbound = conn.execute(
                    """
                    SELECT provider_message_id, provider_thread_id
                    FROM email_messages
                    WHERE conversation_id = ?
                      AND direction = 'inbound'
                      AND sender_email = ?
                      AND provider_message_id IS NOT NULL
                      AND TRIM(provider_message_id) <> ''
                    ORDER BY COALESCE(received_at, created_at) DESC
                    LIMIT 1
                    """,
                    (row["conversation_id"], row["email_normalized"]),
                ).fetchone()
                if inbound:
                    latest_inbound_provider_message_id = str(inbound["provider_message_id"])
                    latest_provider_thread_id = str(inbound["provider_thread_id"] or "") or None
                else:
                    outbound = conn.execute(
                        """
                        SELECT provider_thread_id
                        FROM email_messages
                        WHERE conversation_id = ?
                          AND provider_thread_id IS NOT NULL
                          AND TRIM(provider_thread_id) <> ''
                        ORDER BY COALESCE(sent_at, received_at, created_at) DESC
                        LIMIT 1
                        """,
                        (row["conversation_id"],),
                    ).fetchone()
                    latest_provider_thread_id = str(outbound["provider_thread_id"]) if outbound else None
            delivery_thread_id = latest_provider_thread_id
            if not delivery_done:
                if dry_run:
                    delivery_result = {"message_id": f"dry-delivery-{row['job_id']}", "thread_id": ""}
                else:
                    if latest_inbound_provider_message_id:
                        delivery_result = mail.reply_message(
                            inbox_id=str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                            message_id=latest_inbound_provider_message_id,
                            text=body,
                            attachments=pdf_attachments[:1],
                        )
                    else:
                        delivery_result = mail.send_message(
                            inbox_id=str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                            to=[str(row["email_normalized"])],
                            subject="Re: your deliverable is ready",
                            text=body,
                            thread_id=latest_provider_thread_id,
                            attachments=pdf_attachments[:1],
                        )
                delivery_provider_message_id = str(delivery_result.get("message_id") or delivery_result.get("id") or "")
                delivery_thread_id = str(delivery_result.get("thread_id") or "") or latest_provider_thread_id
                ops_db.insert_email_message(
                    {
                        "channel": "agentmail",
                        "direction": "outbound",
                        "mailbox": str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                        "provider_message_id": delivery_provider_message_id,
                        "provider_thread_id": str(delivery_thread_id or ""),
                        "subject": "Re: your deliverable is ready",
                        "body_text": body,
                        "recipient_email": str(row["email_normalized"]),
                        "sender_email": str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                        "conversation_id": str(row["conversation_id"]),
                        "sent_at": utcnow_iso(),
                        "delivery_status": "sent",
                        "metadata_json": {"send_phase": "fulfillment_delivery", "job_id": str(row["job_id"])},
                    }
                )
                sent_delivery += 1
                artifacts["delivery_email_message_id"] = delivery_provider_message_id or f"delivery:{row['job_id']}"
                ops_db.update_fulfillment_job(str(row["job_id"]), status="delivery_sent", artifacts_json=artifacts)

            survey_text = survey_body()
            if not survey_done:
                if dry_run:
                    survey_result = {"message_id": f"dry-survey-{row['job_id']}", "thread_id": ""}
                else:
                    if latest_inbound_provider_message_id:
                        survey_result = mail.reply_message(
                            inbox_id=str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                            message_id=latest_inbound_provider_message_id,
                            text=survey_text,
                        )
                    else:
                        survey_result = mail.send_message(
                            inbox_id=str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                            to=[str(row["email_normalized"])],
                            subject=survey_subject(),
                            text=survey_text,
                            thread_id=str(delivery_thread_id or latest_provider_thread_id or ""),
                        )
                survey_provider_message_id = str(survey_result.get("message_id") or survey_result.get("id") or "")
                ops_db.insert_email_message(
                    {
                        "channel": "agentmail",
                        "direction": "outbound",
                        "mailbox": str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                        "provider_message_id": survey_provider_message_id,
                        "provider_thread_id": str(survey_result.get("thread_id") or delivery_thread_id or ""),
                        "subject": survey_subject(),
                        "body_text": survey_text,
                        "recipient_email": str(row["email_normalized"]),
                        "sender_email": str(row["agentmail_inbox"] or settings.agentmail_sales_inbox),
                        "conversation_id": str(row["conversation_id"]),
                        "sent_at": utcnow_iso(),
                        "delivery_status": "sent",
                        "metadata_json": {"send_phase": "survey", "job_id": str(row["job_id"])},
                    }
                )
                sent_survey += 1
                artifacts["survey_email_message_id"] = survey_provider_message_id or f"survey:{row['job_id']}"
            if bool(str(artifacts.get("delivery_email_message_id") or "").strip()) and bool(str(artifacts.get("survey_email_message_id") or "").strip()):
                ops_db.update_fulfillment_job(str(row["job_id"]), status="delivered", artifacts_json=artifacts)
                ops_db.update_conversation_state(str(row["conversation_id"]), "survey_sent", latest_intent="delivered")
            else:
                ops_db.update_fulfillment_job(str(row["job_id"]), status="delivery_sent", artifacts_json=artifacts)
        except Exception as exc:
            artifacts["last_delivery_error"] = {
                "reason": "delivery_exception",
                "error": str(exc)[:500],
                "at": utcnow_iso(),
            }
            retry_status = "delivery_sent" if bool(str(artifacts.get("delivery_email_message_id") or "").strip()) else "completed"
            ops_db.update_fulfillment_job(str(row["job_id"]), status=retry_status, artifacts_json=artifacts)
            failed += 1
            continue
    return {"ok": True, "sent_delivery": sent_delivery, "sent_survey": sent_survey, "failed": failed}
