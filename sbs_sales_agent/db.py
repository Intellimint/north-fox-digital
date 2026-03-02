from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


class OpsDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        sql_path = Path(__file__).resolve().parent / "migrations" / "sqlite.sql"
        with self.session() as conn:
            conn.executescript(sql_path.read_text(encoding="utf-8"))
            # Forward-compatible lightweight migrations for older DBs created before runtime_kv existed.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get_runtime_kv(self, key: str) -> str | None:
        with self.session() as conn:
            row = conn.execute("SELECT value FROM runtime_kv WHERE key = ?", (key,)).fetchone()
            return None if row is None else str(row["value"])

    def set_runtime_kv(self, key: str, value: str) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO runtime_kv (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, utcnow_iso()),
            )

    def upsert_offer(self, *, offer: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO offers (
                    offer_key, offer_type, price_cents, fulfillment_workflow_key,
                    active_flag, targeting_rules_json, sales_constraints_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(offer_key) DO UPDATE SET
                    offer_type=excluded.offer_type,
                    price_cents=excluded.price_cents,
                    fulfillment_workflow_key=excluded.fulfillment_workflow_key,
                    active_flag=excluded.active_flag,
                    targeting_rules_json=excluded.targeting_rules_json,
                    sales_constraints_json=excluded.sales_constraints_json,
                    updated_at=excluded.updated_at
                """,
                (
                    offer["offer_key"],
                    offer["offer_type"],
                    int(offer["price_cents"]),
                    offer["fulfillment_workflow_key"],
                    1 if offer.get("active", True) else 0,
                    dumps_json(offer.get("targeting_rules", {})),
                    dumps_json(offer.get("sales_constraints", {})),
                    utcnow_iso(),
                ),
            )

    def upsert_offer_variant(self, *, variant: dict[str, Any]) -> None:
        with self.session() as conn:
            offer_id = conn.execute(
                "SELECT offer_id FROM offers WHERE offer_key = ?",
                (variant["offer_key"],),
            ).fetchone()
            if not offer_id:
                raise RuntimeError(f"offer_missing_for_variant:{variant['offer_key']}")
            conn.execute(
                """
                INSERT INTO offer_variants (
                    offer_id, variant_key, subject_template, body_template, style_tags_json, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(variant_key) DO UPDATE SET
                    offer_id=excluded.offer_id,
                    subject_template=excluded.subject_template,
                    body_template=excluded.body_template,
                    style_tags_json=excluded.style_tags_json,
                    status=excluded.status
                """,
                (
                    int(offer_id["offer_id"]),
                    variant["variant_key"],
                    variant["subject_template"],
                    variant["body_template"],
                    dumps_json(variant.get("style_tags", [])),
                    variant.get("status", "active"),
                ),
            )

    def begin_campaign_run(self, run_id: str, run_type: str, model_versions: dict[str, Any], decisions: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO campaign_runs (run_id, run_type, started_at, model_versions_json, decision_log_json, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, run_type, utcnow_iso(), dumps_json(model_versions), dumps_json(decisions), "running"),
            )

    def finish_campaign_run(self, run_id: str, summary_file_path: str, status: str, decisions: dict[str, Any] | None = None) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE campaign_runs
                SET finished_at = ?, summary_file_path = ?, status = ?,
                    decision_log_json = COALESCE(?, decision_log_json)
                WHERE run_id = ?
                """,
                (utcnow_iso(), summary_file_path, status, dumps_json(decisions) if decisions is not None else None, run_id),
            )

    def is_suppressed(self, email_normalized: str) -> bool:
        with self.session() as conn:
            row = conn.execute(
                "SELECT 1 FROM suppressions WHERE email_normalized = ? LIMIT 1",
                (email_normalized,),
            ).fetchone()
            return row is not None

    def suppress_email(
        self,
        *,
        suppression_id: str,
        email_normalized: str,
        reason: str,
        source_entity_detail_id: int | None = None,
        source_event_id: str | None = None,
        permanent_flag: bool = True,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO suppressions (
                    suppression_id, email_normalized, source_entity_detail_id, reason, source_event_id, permanent_flag
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    suppression_id,
                    email_normalized,
                    source_entity_detail_id,
                    reason,
                    source_event_id,
                    1 if permanent_flag else 0,
                ),
            )
            conn.execute(
                """
                UPDATE prospect_contact_state
                SET suppressed_flag = 1, suppressed_reason = ?, suppressed_at = ?, updated_at = ?
                WHERE email_normalized = ?
                """,
                (reason, utcnow_iso(), utcnow_iso(), email_normalized),
            )

    def upsert_prospect_state(self, row: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO prospect_contact_state (
                    source_entity_detail_id, email_normalized, contact_name_raw, contact_name_normalized,
                    business_name, website_normalized, state, source_snapshot_json, eligible_flag, eligibility_reason,
                    suppressed_flag, suppressed_reason, next_contact_eligible_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_entity_detail_id, email_normalized) DO UPDATE SET
                    contact_name_raw=excluded.contact_name_raw,
                    contact_name_normalized=excluded.contact_name_normalized,
                    business_name=excluded.business_name,
                    website_normalized=excluded.website_normalized,
                    state=excluded.state,
                    source_snapshot_json=excluded.source_snapshot_json,
                    eligible_flag=excluded.eligible_flag,
                    eligibility_reason=excluded.eligibility_reason,
                    updated_at=excluded.updated_at
                """,
                (
                    row["source_entity_detail_id"],
                    row["email_normalized"],
                    row.get("contact_name_raw"),
                    row.get("contact_name_normalized"),
                    row.get("business_name"),
                    row.get("website_normalized"),
                    row.get("state"),
                    dumps_json(row.get("source_snapshot_json", {})),
                    1 if row.get("eligible_flag", True) else 0,
                    row.get("eligibility_reason"),
                    1 if row.get("suppressed_flag", False) else 0,
                    row.get("suppressed_reason"),
                    row.get("next_contact_eligible_at"),
                    utcnow_iso(),
                ),
            )

    def recent_nonresponse_cooldown_hit(self, source_entity_detail_id: int, email_normalized: str) -> bool:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM prospect_contact_state
                WHERE source_entity_detail_id = ? AND email_normalized = ?
                  AND next_contact_eligible_at IS NOT NULL
                  AND next_contact_eligible_at > ?
                LIMIT 1
                """,
                (source_entity_detail_id, email_normalized, utcnow_iso()),
            ).fetchone()
            return row is not None

    def recent_offer_contact_hit(
        self,
        *,
        source_entity_detail_id: int,
        email_normalized: str,
        offer_key: str,
        lookback_days: int = 180,
    ) -> bool:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM prospect_offer_attempts
                WHERE source_entity_detail_id = ?
                  AND email_normalized = ?
                  AND offer_key = ?
                  AND status IN ('main_sent','replied','paid','fulfilled','closed')
                  AND created_at >= datetime('now', ?)
                LIMIT 1
                """,
                (source_entity_detail_id, email_normalized, offer_key, f"-{int(lookback_days)} days"),
            ).fetchone()
            return row is not None

    def create_attempt(self, values: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO prospect_offer_attempts (
                    attempt_id, source_entity_detail_id, email_normalized, offer_key, variant_key,
                    run_id, status, send_window_local_date, cooldown_until, score_json, selection_reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["attempt_id"],
                    values["source_entity_detail_id"],
                    values["email_normalized"],
                    values["offer_key"],
                    values["variant_key"],
                    values["run_id"],
                    values["status"],
                    values["send_window_local_date"],
                    values.get("cooldown_until"),
                    dumps_json(values.get("score_json", {})),
                    dumps_json(values.get("selection_reasons_json", {})),
                ),
            )

    def queue_precheck(self, values: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO precheck_jobs (
                    precheck_id, source_entity_detail_id, email_normalized, attempt_id, state,
                    local_message_id, local_queue_id, local_response_json, hold_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["precheck_id"],
                    values["source_entity_detail_id"],
                    values["email_normalized"],
                    values.get("attempt_id"),
                    values["state"],
                    values.get("local_message_id"),
                    values.get("local_queue_id"),
                    dumps_json(values.get("local_response_json", {})),
                    values["hold_until"],
                ),
            )

    def update_precheck_decision(self, precheck_id: str, decision: str, reason: str) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE precheck_jobs
                SET state = ?, decision = ?, decision_reason = ?, updated_at = ?
                WHERE precheck_id = ?
                """,
                ("decided", decision, reason, utcnow_iso(), precheck_id),
            )

    def due_prechecks(self) -> list[sqlite3.Row]:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT *
                FROM precheck_jobs
                WHERE state IN ('queued','sent') AND hold_until <= ?
                ORDER BY hold_until ASC
                LIMIT 500
                """,
                (utcnow_iso(),),
            ).fetchall()

    def list_attempts_for_run(self, run_id: str) -> list[sqlite3.Row]:
        with self.session() as conn:
            return conn.execute(
                "SELECT * FROM prospect_offer_attempts WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()

    def count_attempts_for_local_date(self, *, local_send_date: str, offer_key: str | None = None) -> int:
        with self.session() as conn:
            if offer_key:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM prospect_offer_attempts
                    WHERE send_window_local_date = ? AND offer_key = ?
                    """,
                    (local_send_date, offer_key),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM prospect_offer_attempts
                    WHERE send_window_local_date = ?
                    """,
                    (local_send_date,),
                ).fetchone()
        return int(row["n"] or 0) if row is not None else 0

    def get_attempt(self, attempt_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                "SELECT * FROM prospect_offer_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()

    def find_conversation_by_attempt(self, attempt_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT * FROM conversations
                WHERE attempt_id = ?
                ORDER BY COALESCE(last_inbound_at, last_outbound_at, created_at) DESC
                LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()

    def update_attempt_status(self, attempt_id: str, status: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE prospect_offer_attempts SET status = ?, updated_at = ? WHERE attempt_id = ?",
                (status, utcnow_iso(), attempt_id),
            )

    def upsert_conversation(
        self,
        *,
        conversation_id: str,
        source_entity_detail_id: int,
        email_normalized: str,
        offer_key: str,
        attempt_id: str | None,
        agentmail_inbox: str,
        state: str,
        thread_metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO conversations (
                    conversation_id, source_entity_detail_id, email_normalized, offer_key, attempt_id,
                    agentmail_inbox, conversation_state, thread_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    conversation_state=excluded.conversation_state,
                    agentmail_inbox=excluded.agentmail_inbox,
                    thread_metadata_json=excluded.thread_metadata_json,
                    updated_at=?
                """,
                (
                    conversation_id,
                    source_entity_detail_id,
                    email_normalized,
                    offer_key,
                    attempt_id,
                    agentmail_inbox,
                    state,
                    dumps_json(thread_metadata or {}),
                    utcnow_iso(),
                ),
            )

    def find_conversation_by_email(self, email_normalized: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT * FROM conversations
                WHERE email_normalized = ?
                ORDER BY COALESCE(last_inbound_at, last_outbound_at, created_at) DESC
                LIMIT 1
                """,
                (email_normalized,),
            ).fetchone()

    def find_conversation_by_provider_thread(self, provider_thread_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT c.*
                FROM conversations c
                JOIN email_messages m ON m.conversation_id = c.conversation_id
                WHERE m.provider_thread_id = ?
                ORDER BY COALESCE(c.last_inbound_at, c.last_outbound_at, c.created_at) DESC
                LIMIT 1
                """,
                (provider_thread_id,),
            ).fetchone()

    def insert_email_message(self, row: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO email_messages (
                    message_id, channel, direction, mailbox, provider_message_id, provider_thread_id,
                    in_reply_to_provider_message_id, subject, body_text, headers_json, recipient_email, sender_email,
                    attempt_id, conversation_id, sent_at, received_at, delivery_status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("message_id") or str(uuid4()),
                    row["channel"],
                    row["direction"],
                    row["mailbox"],
                    row.get("provider_message_id"),
                    row.get("provider_thread_id"),
                    row.get("in_reply_to_provider_message_id"),
                    row.get("subject"),
                    row.get("body_text", ""),
                    dumps_json(row.get("headers_json", {})),
                    row.get("recipient_email"),
                    row.get("sender_email"),
                    row.get("attempt_id"),
                    row.get("conversation_id"),
                    row.get("sent_at"),
                    row.get("received_at"),
                    row.get("delivery_status"),
                    dumps_json(row.get("metadata_json", {})),
                ),
            )

    def provider_message_seen(self, provider_message_id: str | None) -> bool:
        if not provider_message_id:
            return False
        with self.session() as conn:
            row = conn.execute(
                "SELECT 1 FROM email_messages WHERE provider_message_id = ? LIMIT 1",
                (provider_message_id,),
            ).fetchone()
            return row is not None

    def record_classification(
        self,
        *,
        conversation_id: str,
        email_message_id: str,
        stage: str,
        model: str,
        prompt_version: str,
        raw_output: dict[str, Any],
        normalized_output: dict[str, Any],
        confidence: float,
        latency_ms: int | None,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO inbound_classifications (
                    classification_id, conversation_id, email_message_id, stage, model, prompt_version,
                    raw_output_json, normalized_output_json, confidence, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    conversation_id,
                    email_message_id,
                    stage,
                    model,
                    prompt_version,
                    dumps_json(raw_output),
                    dumps_json(normalized_output),
                    confidence,
                    latency_ms,
                ),
            )

    def update_conversation_after_inbound(self, conversation_id: str, *, latest_intent: str, is_closed: bool = False) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET latest_intent = ?, last_inbound_at = ?, conversation_state = CASE WHEN ? = 1 THEN 'closed' ELSE conversation_state END,
                    is_closed = CASE WHEN ? = 1 THEN 1 ELSE is_closed END, updated_at = ?
                WHERE conversation_id = ?
                """,
                (latest_intent, utcnow_iso(), 1 if is_closed else 0, 1 if is_closed else 0, utcnow_iso(), conversation_id),
            )

    def queue_outbound_reply(
        self,
        *,
        conversation_id: str,
        attempt_id: str | None,
        mailbox: str,
        recipient_email: str,
        subject: str,
        body_text: str,
        scheduled_for: str,
        in_reply_to_provider_message_id: str | None = None,
        provider_thread_id: str | None = None,
    ) -> str:
        msg_id = str(uuid4())
        self.insert_email_message(
            {
                "message_id": msg_id,
                "channel": "agentmail",
                "direction": "outbound",
                "mailbox": mailbox,
                "subject": subject,
                "body_text": body_text,
                "recipient_email": recipient_email,
                "sender_email": mailbox,
                "attempt_id": attempt_id,
                "conversation_id": conversation_id,
                "delivery_status": "queued",
                "metadata_json": {
                    "scheduled_for": scheduled_for,
                    "queued_type": "reply",
                    "reply_to_message_id": in_reply_to_provider_message_id,
                    "provider_thread_id": provider_thread_id,
                },
                "in_reply_to_provider_message_id": in_reply_to_provider_message_id,
            }
        )
        return msg_id

    def due_outbound_messages(self) -> list[sqlite3.Row]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM email_messages
                WHERE direction = 'outbound' AND delivery_status = 'queued'
                ORDER BY COALESCE(json_extract(metadata_json, '$.scheduled_for'), created_at)
                LIMIT 500
                """
            ).fetchall()
        due: list[sqlite3.Row] = []
        now = utcnow_iso()
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            if str(meta.get("scheduled_for") or "") <= now:
                due.append(row)
        return due

    def mark_email_sent(
        self,
        *,
        message_id: str,
        provider_message_id: str | None,
        provider_thread_id: str | None,
        delivery_status: str = "sent",
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE email_messages
                SET provider_message_id = COALESCE(?, provider_message_id),
                    provider_thread_id = COALESCE(?, provider_thread_id),
                    delivery_status = ?, sent_at = ?, metadata_json = metadata_json
                WHERE message_id = ?
                """,
                (provider_message_id, provider_thread_id, delivery_status, utcnow_iso(), message_id),
            )

    def update_email_delivery_status(self, *, message_id: str, delivery_status: str) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE email_messages
                SET delivery_status = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (delivery_status, utcnow_iso(), message_id),
            )

    def find_initial_outbound_for_attempt(self, attempt_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT *
                FROM email_messages
                WHERE attempt_id = ?
                  AND direction = 'outbound'
                  AND json_extract(metadata_json, '$.queued_type') = 'initial'
                ORDER BY COALESCE(sent_at, created_at) DESC
                LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()

    def create_payment_record(self, row: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO payments (
                    payment_id, conversation_id, attempt_id, square_customer_id, square_order_id,
                    square_invoice_id, square_invoice_number, square_public_url, amount_cents, status,
                    invoice_sent_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["payment_id"],
                    row["conversation_id"],
                    row.get("attempt_id"),
                    row.get("square_customer_id"),
                    row.get("square_order_id"),
                    row.get("square_invoice_id"),
                    row.get("square_invoice_number"),
                    row.get("square_public_url"),
                    row["amount_cents"],
                    row["status"],
                    row.get("invoice_sent_at"),
                    dumps_json(row.get("metadata_json", {})),
                ),
            )

    def get_payment_by_square_ids(self, *, square_invoice_id: str | None = None, square_order_id: str | None = None) -> sqlite3.Row | None:
        with self.session() as conn:
            if square_invoice_id:
                row = conn.execute(
                    "SELECT * FROM payments WHERE square_invoice_id = ? ORDER BY created_at DESC LIMIT 1",
                    (square_invoice_id,),
                ).fetchone()
                if row is not None:
                    return row
            if square_order_id:
                return conn.execute(
                    "SELECT * FROM payments WHERE square_order_id = ? ORDER BY created_at DESC LIMIT 1",
                    (square_order_id,),
                ).fetchone()
            return None

    def get_open_payment_for_conversation(self, conversation_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT * FROM payments
                WHERE conversation_id = ? AND status IN ('requested','overdue')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()

    def list_open_payments(self) -> list[sqlite3.Row]:
        with self.session() as conn:
            return conn.execute(
                "SELECT * FROM payments WHERE status IN ('requested','overdue') ORDER BY created_at LIMIT 200"
            ).fetchall()

    def mark_payment_paid(self, payment_id: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE payments SET status = 'paid', paid_at = ?, updated_at = ? WHERE payment_id = ?",
                (utcnow_iso(), utcnow_iso(), payment_id),
            )

    def create_fulfillment_job(self, row: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO fulfillment_jobs (
                    job_id, conversation_id, offer_key, status, inputs_json, artifacts_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["job_id"],
                    row["conversation_id"],
                    row["offer_key"],
                    row["status"],
                    dumps_json(row.get("inputs_json", {})),
                    dumps_json(row.get("artifacts_json", {})),
                ),
            )

    def get_conversation(self, conversation_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()

    def fulfillment_job_exists(self, conversation_id: str, offer_key: str) -> bool:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM fulfillment_jobs
                WHERE conversation_id = ? AND offer_key = ?
                LIMIT 1
                """,
                (conversation_id, offer_key),
            ).fetchone()
            return row is not None

    def update_conversation_state(self, conversation_id: str, state: str, *, latest_intent: str | None = None, closed: bool | None = None) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET conversation_state = ?,
                    latest_intent = COALESCE(?, latest_intent),
                    is_closed = COALESCE(?, is_closed),
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (state, latest_intent, (1 if closed else 0) if closed is not None else None, utcnow_iso(), conversation_id),
            )

    def record_reward_event(self, row: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO reward_events (reward_id, attempt_id, conversation_id, event_type, value, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["reward_id"],
                    row.get("attempt_id"),
                    row.get("conversation_id"),
                    row["event_type"],
                    float(row["value"]),
                    dumps_json(row.get("details_json", {})),
                ),
            )

    def pending_fulfillment_jobs(self, *, running_stale_after_minutes: int = 45) -> list[sqlite3.Row]:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(running_stale_after_minutes)))).isoformat()
        with self.session() as conn:
            return conn.execute(
                """
                SELECT *
                FROM fulfillment_jobs
                WHERE status = 'queued'
                   OR (status = 'running' AND COALESCE(started_at, created_at) <= ?)
                ORDER BY created_at
                LIMIT 100
                """,
                (cutoff_iso,),
            ).fetchall()

    def update_fulfillment_job(self, job_id: str, *, status: str, artifacts_json: dict[str, Any] | None = None) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE fulfillment_jobs
                SET status = ?,
                    artifacts_json = COALESCE(?, artifacts_json),
                    started_at = CASE WHEN ? = 'running' THEN COALESCE(started_at, ?) ELSE started_at END,
                    completed_at = CASE WHEN ? IN ('completed','failed','delivered') THEN ? ELSE completed_at END,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    dumps_json(artifacts_json) if artifacts_json is not None else None,
                    status,
                    utcnow_iso(),
                    status,
                    utcnow_iso(),
                    utcnow_iso(),
                    job_id,
                ),
            )

    def list_active_offer_variants(self, offer_key: str) -> list[sqlite3.Row]:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT v.*, o.offer_key
                FROM offer_variants v
                JOIN offers o ON o.offer_id = v.offer_id
                WHERE o.offer_key = ? AND o.active_flag = 1 AND v.status = 'active'
                ORDER BY v.variant_id
                """,
                (offer_key,),
            ).fetchall()
