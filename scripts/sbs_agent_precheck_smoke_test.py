from __future__ import annotations

import argparse
import json
import random
import sqlite3
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from sbs_sales_agent.config import AgentSettings
from sbs_sales_agent.deliverability.precheck_pipeline import precheck_email_template
from sbs_sales_agent.features import features_from_sbs_row, is_valid_email
from sbs_sales_agent.integrations.agentmail import AgentMailClient
from sbs_sales_agent.deliverability.local_mail_api import LocalMailApiClient


def sample_sbs_rows(db_path: Path, count: int, seed: int | None = None) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT *
        FROM sbs_entities
        WHERE email IS NOT NULL AND TRIM(email) <> ''
          AND display_email = 1
          AND public_display = 1
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (count * 3,),
    ).fetchall()
    conn.close()
    items = [dict(r) for r in rows]
    if seed is not None:
        rnd = random.Random(seed)
        rnd.shuffle(items)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in items:
        feat = features_from_sbs_row(row)
        if not is_valid_email(feat.email):
            continue
        email = feat.email.lower()
        if email in seen:
            continue
        seen.add(email)
        selected.append(row)
        if len(selected) >= count:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--commit", action="store_true", help="Actually send emails. Without this, perform dry run only.")
    parser.add_argument("--check-agentmail-feedback", action="store_true", help="List recent feedback inbox messages after sends (read-only).")
    parser.add_argument("--transport", choices=["http", "smtp"], default="http")
    parser.add_argument("--smtp-host", default="127.0.0.1")
    parser.add_argument("--smtp-port", type=int, default=2525)
    args = parser.parse_args()

    settings = AgentSettings.from_env()
    settings.ensure_dirs()
    rows = sample_sbs_rows(settings.sbs_db_path, args.count, seed=args.seed)
    local_mail = LocalMailApiClient(settings)
    agentmail = AgentMailClient(settings)
    smtp_client = None
    if args.transport == "smtp" and args.commit:
        smtp_client = smtplib.SMTP(args.smtp_host, args.smtp_port, timeout=20)

    results: list[dict[str, Any]] = []
    accepted = 0
    failed = 0
    for row in rows:
        feat = features_from_sbs_row(row)
        subject, body = precheck_email_template(feat.first_name_for_greeting)
        if not args.commit:
            resp = {"ok": True, "dry_run": True, "rcpt": {feat.email: {"code": 250, "response": "dry-run accepted"}}}
        else:
            try:
                if args.transport == "http":
                    resp = local_mail.send(to=feat.email, subject=subject, text=body)
                else:
                    assert smtp_client is not None
                    msg = EmailMessage()
                    msg["From"] = settings.local_mail_from
                    msg["To"] = feat.email
                    msg["Subject"] = subject
                    msg.set_content(body)
                    refused = smtp_client.sendmail(settings.local_mail_from, [feat.email], msg.as_string())
                    rcpt_ok = feat.email not in refused
                    resp = {
                        "ok": rcpt_ok,
                        "message_id": msg.get("Message-ID"),
                        "rcpt": {feat.email: {"code": 250 if rcpt_ok else 550, "response": "accepted" if rcpt_ok else str(refused.get(feat.email))}},
                        "smtp": {"code": 250 if rcpt_ok else 550, "response": "queued_or_accepted" if rcpt_ok else "rejected"},
                    }
            except Exception as exc:  # pragma: no cover - network dependent
                resp = {"ok": False, "error": str(exc)}
        rcpt = (resp.get("rcpt") or {}).get(feat.email) or {}
        code = rcpt.get("code")
        if resp.get("ok") and int(code or 0) == 250:
            accepted += 1
        else:
            failed += 1
        results.append(
            {
                "entity_detail_id": feat.entity_detail_id,
                "email": feat.email,
                "business_name": feat.business_name,
                "contact_name": feat.contact_name_normalized,
                "ok": bool(resp.get("ok")),
                "rcpt_code": code,
                "rcpt_response": rcpt.get("response"),
                "smtp_response": (resp.get("smtp") or {}).get("response"),
                "message_id": resp.get("message_id"),
                "error": resp.get("error"),
            }
        )

    feedback_preview: list[dict[str, Any]] = []
    if args.check_agentmail_feedback and settings.agentmail_api_key:
        try:
            payload = agentmail.list_messages(settings.agentmail_precheck_feedback_inbox, limit=20)
            items = payload.get("items") or payload.get("messages") or payload.get("data") or []
            for msg in items[:10]:
                feedback_preview.append(
                    {
                        "message_id": msg.get("message_id") or msg.get("id"),
                        "from": msg.get("from"),
                        "subject": msg.get("subject"),
                        "preview": msg.get("preview"),
                    }
                )
        except Exception as exc:  # pragma: no cover
            feedback_preview.append({"error": str(exc)})

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count_requested": args.count,
        "count_sampled": len(rows),
        "commit": args.commit,
        "accepted_local_smtp_250": accepted,
        "not_accepted_or_failed": failed,
        "acceptance_rate": round((accepted / len(rows)), 4) if rows else 0.0,
        "feedback_preview": feedback_preview,
        "transport": args.transport,
        "results": results,
    }
    out_path = settings.logs_dir / f"precheck_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if smtp_client is not None:
        try:
            smtp_client.quit()
        except Exception:
            pass
    print(json.dumps({"ok": True, "summary_path": str(out_path), **{k: summary[k] for k in ['count_sampled','accepted_local_smtp_250','not_accepted_or_failed','acceptance_rate']}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
