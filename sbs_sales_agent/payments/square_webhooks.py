from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any
from uuid import uuid4

from ..db import OpsDB


def verify_square_signature(url: str, body: str, signature: str, signature_key: str) -> bool:
    payload = f"{url}{body}".encode("utf-8")
    digest = hmac.new(signature_key.encode("utf-8"), payload, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(signature, computed)


def process_square_webhook_payload(ops_db: OpsDB, payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(payload.get("type") or "")
    data = payload.get("data") or {}
    obj = data.get("object") or {}
    invoice = obj.get("invoice") or {}
    payment = obj.get("payment") or {}
    invoice_id = invoice.get("id")
    invoice_status = str(invoice.get("status") or "")
    order_id = invoice.get("order_id") or payment.get("order_id")
    payment_status = str(payment.get("status") or "")

    marked_paid = False
    fulfillment_job_created = False
    payment_row = None
    if invoice_id or order_id:
        payment_row = ops_db.get_payment_by_square_ids(
            square_invoice_id=str(invoice_id) if invoice_id else None,
            square_order_id=str(order_id) if order_id else None,
        )

    should_mark_paid = (
        (event_type == "invoice.payment_made")
        or invoice_status == "PAID"
        or (event_type == "payment.updated" and payment_status == "COMPLETED")
    )
    if payment_row is not None and should_mark_paid and str(payment_row["status"]) != "paid":
        ops_db.mark_payment_paid(str(payment_row["payment_id"]))
        marked_paid = True
        conversation_id = str(payment_row["conversation_id"])
        conv = ops_db.get_conversation(conversation_id)
        if conv is not None:
            ops_db.update_conversation_state(conversation_id, "paid", latest_intent="paid")
            offer_key = str(conv["offer_key"])
            if not ops_db.fulfillment_job_exists(conversation_id, offer_key):
                ops_db.create_fulfillment_job(
                    {
                        "job_id": str(uuid4()),
                        "conversation_id": conversation_id,
                        "offer_key": offer_key,
                        "status": "queued",
                        "inputs_json": {
                            "source_entity_detail_id": int(conv["source_entity_detail_id"]),
                            "conversation_id": conversation_id,
                            "attempt_id": conv["attempt_id"],
                        },
                    }
                )
                fulfillment_job_created = True
        ops_db.record_reward_event(
            {
                "reward_id": str(uuid4()),
                "attempt_id": str(payment_row["attempt_id"]) if payment_row["attempt_id"] else None,
                "conversation_id": str(payment_row["conversation_id"]),
                "event_type": "cash_collected",
                "value": float(int(payment_row["amount_cents"]) / 100.0),
                "details_json": {"square_invoice_id": invoice_id, "event_type": event_type},
            }
        )

    return {
        "ok": True,
        "type": event_type,
        "invoice_id": invoice_id,
        "invoice_status": invoice_status or None,
        "payment_id": payment.get("id"),
        "payment_status": payment_status or None,
        "matched_payment": str(payment_row["payment_id"]) if payment_row else None,
        "marked_paid": marked_paid,
        "fulfillment_job_created": fulfillment_job_created,
    }
