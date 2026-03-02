from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import randint
import re
from uuid import uuid4

from ..config import AgentSettings
from ..db import OpsDB, utcnow_iso
from ..features import normalize_email
from ..integrations.agentmail import AgentMailClient
from ..offers.catalog import default_offers
from .classifier import InboundClassifier
from .reply_agent import SalesReplyAgent

INVOICE_REQUEST_RE = re.compile(r"\b(send|share|issue|create)\b.{0,40}\binvoice\b|\binvoice\b.{0,40}\b(send|share|issue|create)\b", re.IGNORECASE)


def _message_sort_ts(msg: dict) -> str | None:
    for key in ("received_at", "created_at", "date", "updated_at"):
        raw = msg.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return None


def poll_agentmail_inbox(settings: AgentSettings, ops_db: OpsDB, dry_run: bool = False) -> dict:
    client = AgentMailClient(settings)
    classifier = InboundClassifier(settings)
    reply_agent = SalesReplyAgent(settings)
    offer_prices = {o.offer_key: o.price_cents for o in default_offers()}
    offer_types = {o.offer_key: o.offer_type for o in default_offers()}
    processed = 0
    suppressed = 0
    queued_replies = 0
    inboxes = [settings.agentmail_sales_inbox]

    for inbox in inboxes:
        cursor_key = f"poller_cursor:agentmail:{inbox}"
        since_ts = ops_db.get_runtime_kv(cursor_key)
        if dry_run:
            payload = {"items": []}
        else:
            try:
                payload = client.list_messages(inbox, limit=200)
            except Exception as exc:
                return {"ok": False, "reason": "list_messages_failed", "inbox": inbox, "error": str(exc)}
        items = payload.get("items") or payload.get("messages") or payload.get("data") or []
        newest_seen_ts = since_ts
        for msg in items:
            provider_message_id = str(msg.get("message_id") or msg.get("id") or "")
            if provider_message_id and ops_db.provider_message_seen(provider_message_id):
                continue
            msg_ts = _message_sort_ts(msg)
            if since_ts and msg_ts and msg_ts <= since_ts:
                continue
            if msg_ts and (newest_seen_ts is None or msg_ts > newest_seen_ts):
                newest_seen_ts = msg_ts
            from_addr = normalize_email(str(msg.get("from") or ""))
            if not from_addr:
                continue
            thread_id = str(msg.get("thread_id") or msg.get("conversation_id") or "")
            subject = str(msg.get("subject") or "")
            body = str(msg.get("text") or msg.get("body") or msg.get("preview") or "")
            conv = ops_db.find_conversation_by_provider_thread(thread_id) if thread_id else None
            if conv is None:
                conv = ops_db.find_conversation_by_email(from_addr)
            # In production, ignore platform/system chatter from agentmail.to unless it maps to an active test conversation.
            if from_addr.endswith("@agentmail.to") and conv is None and not settings.test_mode:
                continue
            if conv is None:
                # Unknown conversation; store only raw inbound for audit.
                ops_db.insert_email_message(
                    {
                        "channel": "agentmail",
                        "direction": "inbound",
                        "mailbox": inbox,
                        "provider_message_id": provider_message_id,
                        "provider_thread_id": thread_id or None,
                        "subject": subject,
                        "body_text": body,
                        "recipient_email": inbox,
                        "sender_email": from_addr,
                        "received_at": utcnow_iso(),
                        "delivery_status": "received",
                        "metadata_json": {"source": "poller", "unknown_conversation": True},
                    }
                )
                processed += 1
                continue

            internal_message_id = str(uuid4())
            ops_db.insert_email_message(
                {
                    "message_id": internal_message_id,
                    "channel": "agentmail",
                    "direction": "inbound",
                    "mailbox": inbox,
                    "provider_message_id": provider_message_id,
                    "provider_thread_id": thread_id or None,
                    "subject": subject,
                    "body_text": body,
                    "recipient_email": inbox,
                    "sender_email": from_addr,
                    "attempt_id": conv["attempt_id"],
                    "conversation_id": conv["conversation_id"],
                    "received_at": utcnow_iso(),
                    "delivery_status": "received",
                    "metadata_json": {"source": "poller"},
                }
            )
            bundle = classifier.classify(body, subject)
            for stage in bundle.stages:
                ops_db.record_classification(
                    conversation_id=str(conv["conversation_id"]),
                    email_message_id=internal_message_id,
                    stage=stage.stage,
                    model="rule_classifier_v1",
                    prompt_version="v1",
                    raw_output=stage.raw,
                    normalized_output={"label": stage.label},
                    confidence=stage.confidence,
                    latency_ms=stage.raw.get("latency_ms"),
                )

            action = reply_agent.next_action(
                classifications=bundle,
                offer_price_cents=offer_prices.get(str(conv["offer_key"])),
                offer_key=str(conv["offer_key"]),
                offer_type=offer_types.get(str(conv["offer_key"])),
                inbound_subject=subject,
                inbound_body=body,
            )
            close = action.action in {"suppress", "close"}
            ops_db.update_conversation_after_inbound(
                str(conv["conversation_id"]),
                latest_intent=bundle.label_for("intent") or "needs_info",
                is_closed=close,
            )
            if action.action == "suppress":
                ops_db.suppress_email(
                    suppression_id=str(uuid4()),
                    email_normalized=from_addr,
                    reason=action.reason,
                    source_entity_detail_id=int(conv["source_entity_detail_id"]),
                    source_event_id=provider_message_id or None,
                )
                suppressed += 1
            elif action.action == "reply" and action.reply_body:
                payment_row = ops_db.get_open_payment_for_conversation(str(conv["conversation_id"]))
                if (
                    (
                        bundle.label_for("intent") == "positive_interest"
                        or INVOICE_REQUEST_RE.search(body) is not None
                    )
                    and bundle.label_for("payment") == "payment_related"
                    and payment_row is None
                ):
                    from ..worker import trigger_invoice_for_conversation

                    invoice_result = trigger_invoice_for_conversation(
                        settings,
                        conversation_id=str(conv["conversation_id"]),
                        customer_email=from_addr,
                        customer_name=from_addr.split("@", 1)[0],
                        offer_key=str(conv["offer_key"]),
                        amount_cents=int(offer_prices.get(str(conv["offer_key"]), 0)),
                        dry_run=dry_run,
                    )
                    invoice = invoice_result.get("invoice") or {}
                    public_url = str(invoice.get("public_url") or "")
                    if public_url:
                        action.reply_body = (
                            f"Perfect. Here’s the invoice link: {public_url}\n\n"
                            "As soon as it’s paid, I’ll get started and send the deliverable over email."
                        )
                    ops_db.update_conversation_state(str(conv["conversation_id"]), "invoice_sent", latest_intent="positive_interest")
                if settings.test_mode:
                    delay_minutes = 0
                else:
                    delay_minutes = randint(settings.reply_delay_min_minutes, settings.reply_delay_max_minutes)
                scheduled_for = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat()
                ops_db.queue_outbound_reply(
                    conversation_id=str(conv["conversation_id"]),
                    attempt_id=str(conv["attempt_id"]) if conv["attempt_id"] else None,
                    mailbox=settings.agentmail_sales_inbox,
                    recipient_email=from_addr,
                    subject=action.reply_subject or "Re: quick question",
                    body_text=action.reply_body,
                    scheduled_for=scheduled_for,
                    in_reply_to_provider_message_id=provider_message_id or None,
                    provider_thread_id=thread_id or None,
                )
                queued_replies += 1
            processed += 1
        if newest_seen_ts and newest_seen_ts != since_ts:
            ops_db.set_runtime_kv(cursor_key, newest_seen_ts)

    return {"ok": True, "processed": processed, "suppressed": suppressed, "queued_replies": queued_replies}
