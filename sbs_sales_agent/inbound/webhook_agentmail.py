from __future__ import annotations

import base64
import hmac
import json
from hashlib import sha256
from threading import Thread
from time import time
from typing import Any

from ..config import AgentSettings
from ..db import OpsDB, utcnow_iso
from .poller import poll_agentmail_inbox


def _decode_svix_secret(secret: str) -> bytes:
    s = str(secret or "").strip()
    if not s:
        return b""
    if s.startswith("whsec_"):
        s = s.split("_", 1)[1]
    pad = "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad)
    except Exception:
        # Fallback for plain string secrets.
        return s.encode("utf-8")


def verify_agentmail_webhook_signature(
    *,
    raw_body: str,
    headers: dict[str, str],
    webhook_secret: str,
    tolerance_seconds: int = 300,
) -> bool:
    secret = _decode_svix_secret(webhook_secret)
    if not secret:
        return False

    msg_id = str(headers.get("svix-id") or headers.get("webhook-id") or "").strip()
    msg_ts = str(headers.get("svix-timestamp") or headers.get("webhook-timestamp") or "").strip()
    msg_sig = str(headers.get("svix-signature") or headers.get("webhook-signature") or "").strip()
    if not msg_id or not msg_ts or not msg_sig:
        return False

    try:
        ts = int(msg_ts)
    except Exception:
        return False
    if abs(int(time()) - ts) > max(0, int(tolerance_seconds)):
        return False

    payload = f"{msg_id}.{msg_ts}.{raw_body}".encode("utf-8")
    expected = base64.b64encode(hmac.new(secret, payload, sha256).digest()).decode("utf-8")

    candidates: list[str] = []
    for part in msg_sig.split():
        if "," in part:
            version, value = part.split(",", 1)
            if version.strip() == "v1":
                candidates.append(value.strip())
    if not candidates and msg_sig:
        candidates = [msg_sig.strip()]
    return any(hmac.compare_digest(expected, c) for c in candidates)


def process_agentmail_webhook(
    *,
    settings: AgentSettings,
    ops_db: OpsDB,
    raw_body: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    if settings.agentmail_webhook_secret:
        ok = verify_agentmail_webhook_signature(
            raw_body=raw_body,
            headers=headers,
            webhook_secret=settings.agentmail_webhook_secret,
        )
        if not ok:
            return {"ok": False, "reason": "invalid_signature"}

    event_id = str(headers.get("svix-id") or headers.get("webhook-id") or "").strip()
    if event_id:
        marker = f"agentmail_webhook_seen:{event_id}"
        if ops_db.get_runtime_kv(marker):
            return {"ok": True, "duplicate": True}
        ops_db.set_runtime_kv(marker, utcnow_iso())

    try:
        payload = json.loads(raw_body or "{}")
    except Exception:
        return {"ok": False, "reason": "invalid_json"}

    # Keep this endpoint fast: acknowledge quickly, then process inbound pipeline async.
    def _run() -> None:
        try:
            poll_agentmail_inbox(settings, ops_db, dry_run=False)
        except Exception:
            return

    Thread(target=_run, daemon=True).start()
    event_type = str(payload.get("type") or payload.get("event") or "")
    return {"ok": True, "event_type": event_type or "unknown", "queued_processing": True}

