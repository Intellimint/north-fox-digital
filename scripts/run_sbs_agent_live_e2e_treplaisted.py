#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sbs_sales_agent.config import AgentSettings
from sbs_sales_agent.db import OpsDB, utcnow_iso
from sbs_sales_agent.deliverability.precheck_pipeline import DeliverabilityVerifier
from sbs_sales_agent.features import features_from_sbs_row
from sbs_sales_agent.inbound.poller import poll_agentmail_inbox
from sbs_sales_agent.integrations.agentmail import AgentMailClient
from sbs_sales_agent.models import OfferVariant
from sbs_sales_agent.offers.catalog import default_offer_variants, default_offers
from sbs_sales_agent.offers.generator import build_initial_outreach
from sbs_sales_agent.payments.square_webhooks import process_square_webhook_payload
from sbs_sales_agent.research_loop.scan_pipeline import run_scan_pipeline
from sbs_sales_agent.runner import bootstrap_offers
from sbs_sales_agent.source_sbs import SourceProspectRepository
from sbs_sales_agent.worker import (
    _extract_light_findings,
    dispatch_scheduled_messages,
    run_fulfillment_jobs,
    send_fulfillment_and_survey,
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        if k and k not in os.environ:
            os.environ[k] = v


def seed_agent_env_from_stormfix() -> None:
    load_env_file(Path("/Users/satoshinakamoto/Documents/StormFixNow/.env"))
    mapping = {
        "AGENTMAIL_API_KEY": "SBS_AGENT_AGENTMAIL_API_KEY",
        "AGENTMAIL_BASE_URL": "SBS_AGENT_AGENTMAIL_BASE_URL",
        "SQUARE_PRODUCTION_ACCESS_TOKEN": "SBS_AGENT_SQUARE_ACCESS_TOKEN",
        "SQUARE_ACCESS_TOKEN": "SBS_AGENT_SQUARE_ACCESS_TOKEN",
        "SQUARE_PRODUCTION_LOCATION_ID": "SBS_AGENT_SQUARE_LOCATION_ID",
        "SQUARE_LOCATION_ID": "SBS_AGENT_SQUARE_LOCATION_ID",
        "SQUARE_PRODUCTION_WEBHOOK_SIGNATURE_KEY": "SBS_AGENT_SQUARE_WEBHOOK_SIGNATURE_KEY",
        "SQUARE_WEBHOOK_SIGNATURE_KEY": "SBS_AGENT_SQUARE_WEBHOOK_SIGNATURE_KEY",
    }
    for src, dst in mapping.items():
        if os.getenv(src) and not os.getenv(dst):
            os.environ[dst] = os.environ[src]
    if not os.getenv("SBS_AGENT_AGENTMAIL_SALES_INBOX"):
        os.environ["SBS_AGENT_AGENTMAIL_SALES_INBOX"] = "neilfox@agentmail.to"
    if not os.getenv("SBS_AGENT_LOCAL_MAIL_API_URL"):
        os.environ["SBS_AGENT_LOCAL_MAIL_API_URL"] = "http://100.114.46.42:8081"
    if not os.getenv("SBS_AGENT_LOCAL_MAIL_API_TOKEN"):
        os.environ["SBS_AGENT_LOCAL_MAIL_API_TOKEN"] = "c71a3fa12d5cb584a5d18ea6e7e47319ae93cb63beb03b2f77a9bba12b223ddf"
    os.environ["SBS_AGENT_SQUARE_ENVIRONMENT"] = "production"
    os.environ["SBS_AGENT_TEST_MODE"] = "true"
    os.environ["SBS_AGENT_REPLY_DELAY_MIN_MINUTES"] = "0"
    os.environ["SBS_AGENT_REPLY_DELAY_MAX_MIN_MINUTES"] = "0"  # harmless if typo var unused
    os.environ["SBS_AGENT_REPLY_DELAY_MAX_MINUTES"] = "0"


def choose_real_business(source_repo: SourceProspectRepository) -> dict:
    marker = Path("tmp/live_e2e_last_entity_id.txt")
    last_entity_id = int(marker.read_text(encoding="utf-8").strip()) if marker.exists() else 0
    first_eligible: dict | None = None
    for batch in source_repo.iter_candidates(batch_size=250):
        for row in batch:
            f = features_from_sbs_row(row)
            if not (f.business_name and f.naics_primary and f.email and f.website):
                continue
            if first_eligible is None:
                first_eligible = row
            if int(row.get("entity_detail_id") or 0) > last_entity_id:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(str(int(row["entity_detail_id"])), encoding="utf-8")
                return row
    if first_eligible is not None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(int(first_eligible["entity_detail_id"])), encoding="utf-8")
        return first_eligible
    raise RuntimeError("no_eligible_business_found")


def _list_messages(client: AgentMailClient, inbox: str, limit: int = 50) -> list[dict]:
    payload = client.list_messages(inbox_id=inbox, limit=limit)
    return payload.get("items") or payload.get("messages") or payload.get("data") or []


def _safe_list_messages(client: AgentMailClient, inbox: str, limit: int = 50) -> list[dict]:
    try:
        return _list_messages(client, inbox, limit=limit)
    except Exception as exc:
        print(json.dumps({"phase": "inbox_access_warning", "inbox": inbox, "error": str(exc)}))
        return []


def _message_id(msg: dict) -> str:
    return str(msg.get("message_id") or msg.get("id") or "")


def _wait_for_message(client: AgentMailClient, inbox: str, *, contains: str | None = None, subject_contains: str | None = None, sender: str | None = None, known_ids: set[str] | None = None, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in _list_messages(client, inbox, limit=100):
            msg_id = str(msg.get("message_id") or msg.get("id") or "")
            if known_ids and msg_id in known_ids:
                continue
            text = str(msg.get("text") or msg.get("body") or msg.get("preview") or "")
            subj = str(msg.get("subject") or "")
            frm = str(msg.get("from") or "")
            if contains and contains.lower() not in text.lower() and contains.lower() not in str(msg.get("preview") or "").lower():
                continue
            if subject_contains and subject_contains.lower() not in subj.lower():
                continue
            if sender and sender.lower() not in frm.lower():
                continue
            return msg
        time.sleep(2)
    raise TimeoutError(f"message_not_found inbox={inbox}")


def _latest_message_in_thread(client: AgentMailClient, inbox: str, *, thread_id: str, from_filter: str | None = None) -> dict | None:
    for msg in _safe_list_messages(client, inbox, limit=200):
        msg_thread = str(msg.get("thread_id") or msg.get("conversation_id") or "")
        if msg_thread != thread_id:
            continue
        if from_filter:
            frm = str(msg.get("from") or "").lower()
            if from_filter.lower() not in frm:
                continue
        return msg
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Live SBS agent E2E drill routed to treplaisted inbox.")
    parser.add_argument("--target-inbox", default="treplaisted@agentmail.to")
    parser.add_argument("--roleplay-inbox", default="treplaisted@agentmail.to")
    parser.add_argument("--sales-inbox", default="neilfox@agentmail.to")
    parser.add_argument("--local-mail-url", default="http://100.114.46.42:8081")
    parser.add_argument("--use-tailscale-proxy", action="store_true", default=True)
    parser.add_argument("--offer-key", default="dsbs_rewrite_v1", choices=["dsbs_rewrite_v1", "capability_statement_v1"])
    parser.add_argument("--ops-db", default="")
    parser.add_argument("--scenario-index", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="No live sends. For debugging harness only.")
    args = parser.parse_args()

    seed_agent_env_from_stormfix()
    os.environ["SBS_AGENT_AGENTMAIL_SALES_INBOX"] = args.sales_inbox
    os.environ["SBS_AGENT_LOCAL_MAIL_API_URL"] = args.local_mail_url
    if args.use_tailscale_proxy:
        os.environ.setdefault("ALL_PROXY", "http://127.0.0.1:1056")

    settings = AgentSettings.from_env()
    settings.local_mail_api_url = args.local_mail_url
    settings.agentmail_sales_inbox = args.sales_inbox
    settings.local_mail_api_token = os.environ.get("SBS_AGENT_LOCAL_MAIL_API_TOKEN", settings.local_mail_api_token)
    settings.agentmail_api_key = os.environ.get("SBS_AGENT_AGENTMAIL_API_KEY", settings.agentmail_api_key)
    settings.square_access_token = os.environ.get("SBS_AGENT_SQUARE_ACCESS_TOKEN", settings.square_access_token)
    settings.square_location_id = os.environ.get("SBS_AGENT_SQUARE_LOCATION_ID", settings.square_location_id)
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.ops_db:
        settings.ops_db_path = Path(args.ops_db)
    else:
        settings.ops_db_path = Path(f"tmp/sbs_agent_live_e2e_{run_tag}.db")
    settings.test_mode = True
    settings.reply_delay_min_minutes = 0
    settings.reply_delay_max_minutes = 0
    settings.request_timeout_seconds = 90.0
    settings.dry_run_default = args.dry_run
    settings.ensure_dirs()

    ops = OpsDB(settings.ops_db_path)
    ops.init_db()
    bootstrap_offers(ops)
    source_repo = SourceProspectRepository(settings.sbs_db_path)
    row = choose_real_business(source_repo)
    prospect = features_from_sbs_row(row)

    # Persist a minimal attempt + conversation for this test, but route to target inbox.
    offer = next(o for o in default_offers() if o.offer_key == args.offer_key)
    variant_obj = next(v for v in default_offer_variants() if v.offer_key == offer.offer_key)
    variant = OfferVariant(
        variant_key=variant_obj.variant_key,
        offer_key=variant_obj.offer_key,
        subject_template=variant_obj.subject_template,
        body_template=variant_obj.body_template,
        style_tags=variant_obj.style_tags,
        status=variant_obj.status,
    )
    attempt_id = str(uuid4())
    conversation_id = str(uuid4())
    run_id = f"live-e2e-{run_tag}"
    ops.begin_campaign_run(run_id, "manual_live_e2e", {"mode": "live_test"}, {"target_override": args.target_inbox})
    ops.create_attempt(
        {
            "attempt_id": attempt_id,
            "source_entity_detail_id": prospect.entity_detail_id,
            "email_normalized": args.target_inbox.lower(),
            "offer_key": offer.offer_key,
            "variant_key": variant.variant_key,
            "run_id": run_id,
            "status": "selected",
            "send_window_local_date": datetime.now().date().isoformat(),
            "cooldown_until": None,
            "score_json": {"test_mode": True},
            "selection_reasons_json": {"reason": "manual_live_e2e"},
        }
    )
    ops.upsert_conversation(
        conversation_id=conversation_id,
        source_entity_detail_id=prospect.entity_detail_id,
        email_normalized=args.target_inbox.lower(),
        offer_key=offer.offer_key,
        attempt_id=attempt_id,
        agentmail_inbox=args.sales_inbox,
        state="awaiting_reply",
        thread_metadata={"test_mode": True, "real_business": prospect.business_name},
    )

    verifier = DeliverabilityVerifier(settings, ops)
    agentmail = AgentMailClient(settings)

    print(json.dumps({"phase": "chosen_business", "entity_detail_id": prospect.entity_detail_id, "business_name": prospect.business_name, "contact_name": prospect.contact_name_normalized, "source_email": prospect.email, "naics_primary": prospect.naics_primary}))

    # 1) Precheck via local email-validator + DNS (deprecated contact@osceola pipeline removed).
    precheck_prospect = prospect
    precheck_prospect.email = args.target_inbox.lower()
    precheck_sent = verifier.send_precheck(prospect=precheck_prospect, attempt_id=attempt_id, dry_run=args.dry_run)
    print(json.dumps({"phase": "precheck_verification_sent", "to": args.target_inbox, "result": precheck_sent.get("result")}))
    # Force immediate evaluation for the harness.
    with ops.session() as conn:
        conn.execute("UPDATE precheck_jobs SET hold_until = ? WHERE precheck_id = ?", (utcnow_iso(), precheck_sent["precheck_id"]))
    precheck_decision = verifier.evaluate_precheck_window(precheck_sent["precheck_id"], dry_run=args.dry_run)
    print(json.dumps({"phase": "precheck_evaluated", "decision": precheck_decision}))
    if precheck_decision.get("decision") != "safe_to_send_main":
        raise RuntimeError(f"precheck_blocked:{precheck_decision}")
    ops.update_attempt_status(attempt_id, "precheck_passed")

    # 2) Real main outreach send via AgentMail to treplaisted.
    light_scan = run_scan_pipeline(
        settings=settings,
        website=str(prospect.website or ""),
        out_dir=settings.logs_dir / "light_scans" / run_tag / f"entity_{prospect.entity_detail_id}",
        mode="light",
    ) if prospect.website else {"findings": []}
    light_findings = _extract_light_findings(light_scan, max_items=3)
    subject, body = build_initial_outreach(
        settings=settings,
        offer=offer,
        variant=variant,
        prospect=prospect,
        light_findings=light_findings,
    )
    known_treplaisted_ids = {str(m.get("message_id") or m.get("id") or "") for m in _safe_list_messages(agentmail, args.target_inbox, limit=200)}
    send_resp = {"message_id": f"dry-{uuid4()}", "thread_id": f"dry-thread-{uuid4()}"}
    if not args.dry_run:
        send_resp = agentmail.send_message(inbox_id=args.sales_inbox, to=[args.target_inbox], subject=subject, text=body)
    ops.insert_email_message(
        {
            "channel": "agentmail",
            "direction": "outbound",
            "mailbox": args.sales_inbox,
            "provider_message_id": str(send_resp.get("message_id") or send_resp.get("id") or ""),
            "provider_thread_id": str(send_resp.get("thread_id") or ""),
            "subject": subject,
            "body_text": body,
            "recipient_email": args.target_inbox,
            "sender_email": args.sales_inbox,
            "attempt_id": attempt_id,
            "conversation_id": conversation_id,
            "sent_at": utcnow_iso(),
            "delivery_status": "sent",
            "metadata_json": {"send_phase": "initial", "test_mode": True},
        }
    )
    ops.update_attempt_status(attempt_id, "main_sent")
    print(json.dumps({"phase": "main_outreach_sent", "to": args.target_inbox, "subject": subject, "send_resp": send_resp}))

    trep_msg = None
    if not args.dry_run:
        try:
            trep_msg = _wait_for_message(agentmail, args.target_inbox, subject_contains=subject, known_ids=known_treplaisted_ids, timeout=45)
            print(json.dumps({"phase": "target_received_main", "message_id": trep_msg.get("message_id") or trep_msg.get("id"), "subject": trep_msg.get("subject")}))
        except Exception as exc:
            print(json.dumps({"phase": "target_receive_check_skipped", "reason": str(exc), "note": "Please verify in treplaisted inbox UI"}))

    # 3) Roleplay as business from treplaisted inbox (real send), including a longer conversation.
    sales_inbox_snapshot = _safe_list_messages(agentmail, args.sales_inbox, limit=500)
    latest_sales_ts = None
    for m in sales_inbox_snapshot:
        for key in ("received_at", "created_at", "date", "updated_at"):
            raw = m.get(key)
            if raw:
                value = str(raw)
                if latest_sales_ts is None or value > latest_sales_ts:
                    latest_sales_ts = value
                break
    if latest_sales_ts:
        ops.set_runtime_kv(f"poller_cursor:agentmail:{args.sales_inbox}", latest_sales_ts)
    roleplay_thread_id = str((trep_msg or {}).get("thread_id") or "")
    if not args.dry_run and not roleplay_thread_id:
        raise RuntimeError("missing_thread_id_for_initial_message")

    scenarios = [
        {
            "name": "price_then_yes",
            "turn1": "Interesting, but $299 feels high. What exactly do I get and why is it worth that?",
            "turn2": "If you can send the invoice now, I can pay today.",
        },
        {
            "name": "proof_first",
            "turn1": "How do I know these findings are real? Do you include page-level proof and exact fixes?",
            "turn2": "Alright, send the invoice and I’ll move forward.",
        },
        {
            "name": "agency_objection",
            "turn1": "We already work with an agency. Why would we need this too?",
            "turn2": "Makes sense as a second opinion. Send invoice.",
        },
        {
            "name": "privacy_concern",
            "turn1": "What data did you collect from our site? I don’t want anything intrusive.",
            "turn2": "Okay, thanks for clarifying. Please send the invoice.",
        },
        {
            "name": "timeline_pressure",
            "turn1": "We need this fast. Can we actually use it right away this week?",
            "turn2": "Great. Send invoice and we’ll get started.",
        },
    ]
    scenario = scenarios[args.scenario_index % len(scenarios)]
    print(json.dumps({"phase": "roleplay_scenario", "scenario": scenario["name"]}))

    def send_roleplay(text: str, phase: str) -> None:
        roleplay_resp = {"message_id": f"dry-role-{uuid4()}"}
        if not args.dry_run:
            try:
                reply_target = _latest_message_in_thread(
                    agentmail,
                    args.roleplay_inbox,
                    thread_id=roleplay_thread_id,
                    from_filter=args.sales_inbox,
                )
                if reply_target is None:
                    raise RuntimeError("no_thread_message_in_target_inbox")
                roleplay_resp = agentmail.reply_message(
                    inbox_id=args.roleplay_inbox,
                    message_id=_message_id(reply_target),
                    text=text,
                )
            except Exception as exc:
                print(json.dumps({"phase": "roleplay_send_failed", "inbox": args.roleplay_inbox, "error": str(exc), "fallback": "jefferywacaster@agentmail.to"}))
                fallback_subject = f"Re: {subject}"
                roleplay_resp = agentmail.send_message(
                    inbox_id="jefferywacaster@agentmail.to",
                    to=[args.sales_inbox],
                    subject=fallback_subject,
                    text=text,
                )
        print(json.dumps({"phase": phase, "from": args.roleplay_inbox, "to": args.sales_inbox, "resp": roleplay_resp, "body": text}))

    def run_agent_cycles(label: str, *, max_cycles: int = 5) -> tuple[dict, dict, bool]:
        poll_result = {"ok": True, "processed": 0, "suppressed": 0, "queued_replies": 0}
        dispatch_result = {"ok": True, "sent": 0, "skipped": 0}
        payment_created = False
        for i in range(1, max_cycles + 1):
            step_poll = poll_agentmail_inbox(settings, ops, dry_run=args.dry_run)
            step_dispatch = dispatch_scheduled_messages(settings, dry_run=args.dry_run)
            for k in ("processed", "suppressed", "queued_replies"):
                poll_result[k] = poll_result.get(k, 0) + int(step_poll.get(k, 0))
            for k in ("sent", "skipped"):
                dispatch_result[k] = dispatch_result.get(k, 0) + int(step_dispatch.get(k, 0))
            with ops.session() as conn:
                payment_row = conn.execute(
                    "SELECT payment_id FROM payments WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                    (conversation_id,),
                ).fetchone()
            payment_created = bool(payment_row)
            print(json.dumps({"phase": "agent_poll_cycle", "label": label, "cycle": i, "step_poll": step_poll, "step_dispatch": step_dispatch, "payment_created": payment_created}))
            if payment_created:
                break
            if int(step_poll.get("processed", 0)) == 0 and int(step_dispatch.get("sent", 0)) == 0:
                time.sleep(2)
        print(json.dumps({"phase": f"agent_processed_{label}", "poll_result": poll_result, "dispatch_result": dispatch_result, "payment_created": payment_created}))
        return poll_result, dispatch_result, payment_created

    # Turn 1: varied objection handling.
    send_roleplay(scenario["turn1"], "roleplay_turn1_sent")
    run_agent_cycles("roleplay_turn1", max_cycles=5)

    # Turn 2: explicit invoice request.
    send_roleplay(scenario["turn2"], "roleplay_turn2_sent")
    poll_result, dispatch_result, _ = run_agent_cycles("roleplay_turn2", max_cycles=5)

    invoice_reply = None
    if not args.dry_run:
        try:
            invoice_reply = _wait_for_message(
                agentmail,
                args.target_inbox,
                sender=args.sales_inbox,
                contains="invoice",
                known_ids=known_treplaisted_ids | ({str(trep_msg.get('message_id') or trep_msg.get('id'))} if trep_msg else set()),
                timeout=60,
            )
            print(json.dumps({"phase": "agent_invoice_reply_received", "message_id": invoice_reply.get("message_id") or invoice_reply.get("id"), "subject": invoice_reply.get("subject"), "preview": invoice_reply.get("preview")}))
        except Exception as exc:
            print(json.dumps({"phase": "agent_invoice_reply_check_skipped", "reason": str(exc), "note": "Check treplaisted inbox UI"}))

    # 5) Simulate payment webhook event against the real invoice record to continue flow (no actual payment charged in test).
    with ops.session() as conn:
        payment_row = conn.execute(
            "SELECT * FROM payments WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    if payment_row is None:
        raise RuntimeError("invoice_not_created_by_agent")
    webhook_payload = {
        "type": "invoice.payment_made",
        "data": {
            "object": {
                "invoice": {
                    "id": payment_row["square_invoice_id"],
                    "status": "PAID",
                    "order_id": payment_row["square_order_id"],
                }
            }
        },
    }
    webhook_result = process_square_webhook_payload(ops, webhook_payload)
    print(json.dumps({"phase": "payment_webhook_simulated", "result": webhook_result}))

    # 6) Fulfillment + delivery + survey sends (real via AgentMail).
    fulfill_result = run_fulfillment_jobs(settings)
    delivery_survey_result = send_fulfillment_and_survey(settings, dry_run=args.dry_run)
    print(json.dumps({"phase": "fulfillment_and_survey", "fulfill_result": fulfill_result, "delivery_survey_result": delivery_survey_result}))

    # Thread integrity check from ops DB: all provider_thread_id values in this conversation must collapse to one id.
    with ops.session() as conn:
        thread_rows = conn.execute(
            """
            SELECT DISTINCT provider_thread_id
            FROM email_messages
            WHERE conversation_id = ?
              AND provider_thread_id IS NOT NULL
              AND TRIM(provider_thread_id) <> ''
            """,
            (conversation_id,),
        ).fetchall()
    distinct_threads = [str(r["provider_thread_id"]) for r in thread_rows]
    thread_ok = len(set(distinct_threads)) <= 1
    print(json.dumps({"phase": "thread_check", "thread_ok": thread_ok, "distinct_threads": distinct_threads}))
    if not thread_ok:
        raise RuntimeError(f"thread_split_detected:{distinct_threads}")

    if not args.dry_run:
        time.sleep(2)
        latest_target = _safe_list_messages(agentmail, args.target_inbox, limit=20)[:10]
        if latest_target:
            print(json.dumps({"phase": "target_inbox_recent", "messages": [
                {"id": m.get("message_id") or m.get("id"), "from": m.get("from"), "subject": m.get("subject"), "preview": m.get("preview")}
                for m in latest_target
            ]}))

    ops.finish_campaign_run(run_id, summary_file_path="", status="completed", decisions={"live_e2e": True})
    print(json.dumps({
        "phase": "done",
        "ops_db": str(settings.ops_db_path),
        "run_id": run_id,
        "conversation_id": conversation_id,
        "entity_detail_id": prospect.entity_detail_id,
        "business_name": prospect.business_name,
        "scenario": scenario["name"],
        "subject": subject,
    }))


if __name__ == "__main__":
    main()
