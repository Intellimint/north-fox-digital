from __future__ import annotations

from uuid import uuid4

from ..config import AgentSettings
from ..db import OpsDB
from ..integrations.square_client import SquareClient


def reconcile_square_payments(settings: AgentSettings, ops_db: OpsDB, dry_run: bool = False) -> dict:
    if dry_run:
        return {"ok": True, "dry_run": True, "updated": 0}
    square = SquareClient(settings)
    updated = 0
    checked = 0
    fulfillment_jobs_created = 0
    errors: list[str] = []
    for row in ops_db.list_open_payments():
        checked += 1
        payment_id = str(row["payment_id"])
        is_paid = False
        try:
            if row["square_invoice_id"]:
                invoice_payload = square.get_invoice(str(row["square_invoice_id"]))
                invoice = (invoice_payload or {}).get("invoice") or {}
                if str(invoice.get("status") or "").upper() == "PAID":
                    is_paid = True
            if not is_paid and row["square_order_id"]:
                payments_payload = square.list_payments_for_order(str(row["square_order_id"]))
                payments = (payments_payload or {}).get("payments") or []
                if any(str(item.get("status") or "").upper() == "COMPLETED" for item in payments):
                    is_paid = True
        except Exception as exc:
            errors.append(f"{payment_id}:{exc}")
            continue
        if not is_paid:
            continue
        ops_db.mark_payment_paid(payment_id)
        updated += 1
        conversation_id = str(row["conversation_id"])
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
                fulfillment_jobs_created += 1
        ops_db.record_reward_event(
            {
                "reward_id": str(uuid4()),
                "attempt_id": str(row["attempt_id"]) if row["attempt_id"] else None,
                "conversation_id": conversation_id,
                "event_type": "cash_collected_reconcile",
                "value": float(int(row["amount_cents"]) / 100.0),
                "details_json": {
                    "square_invoice_id": row["square_invoice_id"],
                    "square_order_id": row["square_order_id"],
                },
            }
        )
    return {
        "ok": True,
        "checked": checked,
        "updated": updated,
        "fulfillment_jobs_created": fulfillment_jobs_created,
        "errors": errors[:20],
    }
