from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from random import randint
from typing import Any
from uuid import uuid4

from ..config import AgentSettings
from ..db import OpsDB
from ..models import ProspectFeatures
from ..integrations.agentmail import AgentMailClient
from .email_verification import EmailVerificationClient

BOUNCE_SENDER_RE = re.compile(r"(mailer-daemon|postmaster|no-?reply)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def precheck_email_template(first_name: str) -> tuple[str, str]:
    subject = "One question - would love your take"
    body = (
        f"Hi {first_name or 'there'},\n\n"
        "I’m reaching out because we’re interviewing small businesses to understand what’s genuinely frustrating right now - so we can build tools/services that actually help.\n\n"
        "What’s the biggest headache in your business these days? If you have a second, what have you tried so far?\n\n"
        "Even a one-line reply helps a lot.\n\n"
        "Thanks,\n"
        "Jeffery Wacaster"
    )
    return subject, body


class DeliverabilityVerifier:
    def __init__(self, settings: AgentSettings, ops_db: OpsDB) -> None:
        self.settings = settings
        self.ops_db = ops_db
        self.verifier = EmailVerificationClient()
        self.agentmail = AgentMailClient(settings)

    def send_precheck(self, *, prospect: ProspectFeatures, attempt_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        hold_minutes = randint(self.settings.precheck_hold_min_minutes, self.settings.precheck_hold_max_minutes)
        hold_until = (datetime.now(timezone.utc) + timedelta(minutes=hold_minutes)).isoformat()
        precheck_id = str(uuid4())
        if dry_run:
            result = {
                "ok": True,
                "dry_run": True,
                "mode": "email_validator_dns",
                "decision": "safe_to_send_main",
                "reason": "dry_run",
                "details": {},
            }
        else:
            verified = self.verifier.verify(prospect.email)
            result = {
                "ok": verified.ok,
                "mode": "email_validator_dns",
                "decision": verified.decision,
                "reason": verified.reason,
                "normalized_email": verified.normalized_email,
                "details": verified.details,
                "deprecated_contact_pipeline": True,
            }
        self.ops_db.queue_precheck(
            {
                "precheck_id": precheck_id,
                "source_entity_detail_id": prospect.entity_detail_id,
                "email_normalized": prospect.email.lower(),
                "attempt_id": attempt_id,
                "state": "sent",
                "local_message_id": f"verify-{precheck_id}",
                "local_queue_id": str(result.get("reason") or ""),
                "local_response_json": result,
                "hold_until": hold_until,
            }
        )
        return {"precheck_id": precheck_id, "hold_until": hold_until, "result": result}

    def _classify_feedback_message(self, msg: dict[str, Any]) -> tuple[str | None, str | None]:
        sender = str(msg.get("from") or "").lower()
        subject = str(msg.get("subject") or "")
        preview = str(msg.get("preview") or "")
        combined = f"{subject}\n{preview}"
        emails = [e.lower() for e in EMAIL_RE.findall(combined)]
        target = next((e for e in emails if not e.endswith("@agentmail.to")), None)
        if not target:
            return None, None
        lowered = combined.lower()
        if BOUNCE_SENDER_RE.search(sender) or "undeliverable" in lowered or "delivery status notification" in lowered:
            return target, "hard_bounce"
        if "out of office" in lowered or "auto-reply" in lowered:
            return target, "out_of_office"
        if "unsubscribe" in lowered or "do not contact" in lowered:
            return target, "unsubscribe"
        return target, None

    def evaluate_precheck_window(self, precheck_id: str, dry_run: bool = False) -> dict[str, Any]:
        due_rows = [row for row in self.ops_db.due_prechecks() if str(row["precheck_id"]) == precheck_id]
        if not due_rows:
            return {"ok": False, "reason": "precheck_not_due_or_missing"}
        row = due_rows[0]
        email = str(row["email_normalized"])
        local_response = row["local_response_json"] or "{}"
        import json
        try:
            local_json = json.loads(local_response) if isinstance(local_response, str) else dict(local_response)
        except Exception:
            local_json = {}
        if dry_run:
            decision = "safe_to_send_main"
            reason = "dry_run"
        else:
            # New authoritative path: decision from local verifier.
            verifier_decision = str(local_json.get("decision") or "")
            verifier_reason = str(local_json.get("reason") or "")
            if verifier_decision in {"safe_to_send_main", "suppress"}:
                decision, reason = verifier_decision, verifier_reason or "verifier_decision"
                self.ops_db.update_precheck_decision(precheck_id, decision, reason)
                return {"ok": True, "precheck_id": precheck_id, "decision": decision, "reason": reason}

            # Legacy fallback (deprecated): inbox feedback parsing.
            inbox_payload = self.agentmail.list_messages(self.settings.agentmail_precheck_feedback_inbox, limit=200)
            items = inbox_payload.get("items") or inbox_payload.get("messages") or inbox_payload.get("data") or []
            signal = None
            for msg in items:
                msg_email, msg_signal = self._classify_feedback_message(msg)
                if msg_email == email and msg_signal:
                    signal = msg_signal
                    break
            if signal == "hard_bounce":
                decision, reason = "suppress", "hard_bounce"
            elif signal == "unsubscribe":
                decision, reason = "suppress", "unsubscribe"
            elif signal == "out_of_office":
                decision, reason = "safe_to_send_main", "ooo_ignored"
            else:
                decision, reason = "safe_to_send_main", "no_negative_signal"
        self.ops_db.update_precheck_decision(precheck_id, decision, reason)
        return {"ok": True, "precheck_id": precheck_id, "decision": decision, "reason": reason}
