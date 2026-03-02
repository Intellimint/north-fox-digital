from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sbs_sales_agent.config import AgentSettings
from sbs_sales_agent.db import OpsDB
from sbs_sales_agent.inbound.poller import poll_agentmail_inbox
from sbs_sales_agent.payments.square_webhooks import process_square_webhook_payload
from sbs_sales_agent.runner import run_orchestrator
from sbs_sales_agent.worker import (
    dispatch_scheduled_messages,
    process_due_prechecks,
    run_fulfillment_jobs,
    send_fulfillment_and_survey,
    send_main_outreach_from_passed_prechecks,
    trigger_invoice_for_conversation,
)


def _create_test_sbs_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sbs_entities (
            entity_detail_id INTEGER PRIMARY KEY,
            meili_primary_key TEXT NULL,
            uei TEXT NULL,
            cage_code TEXT NULL,
            legal_business_name TEXT NULL,
            dba_name TEXT NULL,
            contact_person TEXT NULL,
            email TEXT NULL,
            phone TEXT NULL,
            fax TEXT NULL,
            website TEXT NULL,
            additional_website TEXT NULL,
            address_1 TEXT NULL,
            address_2 TEXT NULL,
            city TEXT NULL,
            state TEXT NULL,
            zipcode TEXT NULL,
            county TEXT NULL,
            msa TEXT NULL,
            congressional_district TEXT NULL,
            naics_primary TEXT NULL,
            last_update_date TEXT NULL,
            display_email INTEGER NULL,
            display_phone INTEGER NULL,
            public_display INTEGER NULL,
            public_display_limited INTEGER NULL,
            raw TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            description TEXT NULL,
            keywords TEXT NULL,
            tags TEXT NULL,
            certs TEXT NULL
        )
        """
    )
    rows = [
        (
            1, "pk1", "UEI111", "CAGE1", "ALPHA SERVICES LLC", None, "JANE DOE", "jane@alpha.example",
            "5551112222", None, "www.alpha.example", None, "1 Main", None, "Kissimmee", "FL", "34741",
            None, None, None, "541611", None, 1, 1, 1, 0,
            json.dumps(
                {
                    "self_small_boolean": True,
                    "naics_all_codes": ["541611", "541614"],
                    "keywords": ["Management Consulting", "Operations", "Process Improvement"],
                    "capabilities_narrative": "Consulting and operations support.",
                    "uei": "UEI111",
                    "cage_code": "CAGE1",
                }
            ),
            "2026-02-24T00:00:00Z",
            "Consulting and operations support.",
            None,
            "[]",
            "[]",
        ),
        (
            2, "pk2", "UEI222", "CAGE2", "BRAVO TECH INC", None, "BOB SMITH", "bob@bravo.example",
            "5553334444", None, "https://bravo.example", None, "2 Main", None, "Orlando", "FL", "32801",
            None, None, None, "541512", None, 1, 1, 1, 0,
            json.dumps(
                {
                    "self_small_boolean": True,
                    "naics_all_codes": ["541512"],
                    "keywords": ["IT Services", "Cybersecurity", "Help Desk"],
                    "capabilities_narrative": "IT and cybersecurity support.",
                    "uei": "UEI222",
                    "cage_code": "CAGE2",
                }
            ),
            "2026-02-24T00:00:00Z",
            "IT and cybersecurity support.",
            None,
            "[]",
            "[]",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO sbs_entities (
            entity_detail_id, meili_primary_key, uei, cage_code, legal_business_name, dba_name, contact_person,
            email, phone, fax, website, additional_website, address_1, address_2, city, state, zipcode,
            county, msa, congressional_district, naics_primary, last_update_date, display_email, display_phone,
            public_display, public_display_limited, raw, updated_at, description, keywords, tags, certs
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


class EndToEndTests(unittest.TestCase):
    def test_orchestrator_dedupes_emails_across_offers_and_respects_daily_caps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sbs_db = td_path / "sbs_live.db"
            ops_db = td_path / "ops.db"
            _create_test_sbs_db(sbs_db)
            # Force duplicate contact email across source rows to exercise cross-offer dedupe.
            conn = sqlite3.connect(sbs_db)
            conn.execute("UPDATE sbs_entities SET email = 'jane@alpha.example' WHERE entity_detail_id = 2")
            conn.commit()
            conn.close()

            settings = AgentSettings(sbs_db_path=sbs_db, ops_db_path=ops_db, logs_dir=td_path / "logs", artifacts_dir=td_path / "artifacts")
            settings.per_run_offer_cap = 10
            settings.daily_offer_cap = 1
            settings.daily_total_initial_cap = 1
            out = run_orchestrator(settings, slot="09", dry_run=True)
            self.assertTrue(out["ok"])
            self.assertEqual(out["metrics"]["selected_total"], 1)
            self.assertEqual(out["metrics"]["prechecks_created"], 1)

    def test_send_main_outreach_skips_when_initial_send_already_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sbs_db = td_path / "sbs_live.db"
            ops_db_path = td_path / "ops.db"
            _create_test_sbs_db(sbs_db)
            settings = AgentSettings(sbs_db_path=sbs_db, ops_db_path=ops_db_path, logs_dir=td_path / "logs", artifacts_dir=td_path / "artifacts")
            ops = OpsDB(ops_db_path)
            ops.init_db()
            run_id = "run-1"
            attempt_id = "attempt-1"
            ops.create_attempt(
                {
                    "attempt_id": attempt_id,
                    "source_entity_detail_id": 1,
                    "email_normalized": "jane@alpha.example",
                    "offer_key": "dsbs_rewrite_v1",
                    "variant_key": "dsbs_v1_a",
                    "run_id": run_id,
                    "status": "precheck_passed",
                    "send_window_local_date": "2026-02-27",
                    "cooldown_until": None,
                    "score_json": {},
                    "selection_reasons_json": {},
                }
            )
            ops.insert_email_message(
                {
                    "channel": "agentmail",
                    "direction": "outbound",
                    "mailbox": settings.agentmail_sales_inbox,
                    "attempt_id": attempt_id,
                    "conversation_id": None,
                    "subject": "seed",
                    "body_text": "seed",
                    "recipient_email": "jane@alpha.example",
                    "sender_email": settings.agentmail_sales_inbox,
                    "delivery_status": "sending",
                    "metadata_json": {"queued_type": "initial"},
                }
            )
            with patch("sbs_sales_agent.worker._within_initial_send_window", return_value=True), patch(
                "sbs_sales_agent.integrations.agentmail.AgentMailClient.send_message",
                side_effect=AssertionError("send_message must not be called for duplicate initial send"),
            ):
                out = send_main_outreach_from_passed_prechecks(settings, run_id=run_id, dry_run=False)
            self.assertTrue(out["ok"])
            self.assertEqual(out["sent"], 0)

    def test_send_main_outreach_continues_after_single_send_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sbs_db = td_path / "sbs_live.db"
            ops_db_path = td_path / "ops.db"
            _create_test_sbs_db(sbs_db)
            settings = AgentSettings(sbs_db_path=sbs_db, ops_db_path=ops_db_path, logs_dir=td_path / "logs", artifacts_dir=td_path / "artifacts")
            ops = OpsDB(ops_db_path)
            ops.init_db()
            ops.upsert_offer(
                offer={
                    "offer_key": "dsbs_rewrite_v1",
                    "offer_type": "DOC_REWRITE",
                    "price_cents": 14900,
                    "fulfillment_workflow_key": "dsbs_rewrite",
                    "active": True,
                    "targeting_rules": {},
                    "sales_constraints": {},
                }
            )
            ops.upsert_offer_variant(
                variant={
                    "offer_key": "dsbs_rewrite_v1",
                    "variant_key": "dsbs_v1_a",
                    "subject_template": "Subj",
                    "body_template": "Body",
                    "status": "active",
                }
            )
            run_id = "run-send-failure"
            for idx, source_id in enumerate((1, 2), start=1):
                ops.create_attempt(
                    {
                        "attempt_id": f"attempt-{idx}",
                        "source_entity_detail_id": source_id,
                        "email_normalized": ("jane@alpha.example" if source_id == 1 else "bob@bravo.example"),
                        "offer_key": "dsbs_rewrite_v1",
                        "variant_key": "dsbs_v1_a",
                        "run_id": run_id,
                        "status": "precheck_passed",
                        "send_window_local_date": "2026-02-27",
                        "cooldown_until": None,
                        "score_json": {},
                        "selection_reasons_json": {},
                    }
                )

            with patch("sbs_sales_agent.worker._within_initial_send_window", return_value=True), patch(
                "sbs_sales_agent.worker._get_or_run_light_scan",
                return_value=[],
            ), patch(
                "sbs_sales_agent.worker.build_initial_outreach",
                return_value=("Subject", "Body"),
            ), patch(
                "sbs_sales_agent.integrations.agentmail.AgentMailClient.send_message",
                side_effect=[RuntimeError("provider down"), {"message_id": "ok-2", "thread_id": "thread-2"}],
            ):
                out = send_main_outreach_from_passed_prechecks(settings, run_id=run_id, dry_run=False)

            self.assertTrue(out["ok"])
            self.assertEqual(out["sent"], 1)
            self.assertEqual(out["failed"], 1)
            with ops.session() as conn:
                statuses = conn.execute(
                    "SELECT attempt_id, status FROM prospect_offer_attempts WHERE run_id = ? ORDER BY attempt_id",
                    (run_id,),
                ).fetchall()
                failed_msg = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM email_messages
                    WHERE attempt_id = 'attempt-1'
                      AND delivery_status = 'failed'
                    """,
                ).fetchone()
            status_by_attempt = {str(r["attempt_id"]): str(r["status"]) for r in statuses}
            self.assertEqual(status_by_attempt["attempt-1"], "precheck_passed")
            self.assertEqual(status_by_attempt["attempt-2"], "main_sent")
            self.assertEqual(int(failed_msg["n"]), 1)

    def test_fulfillment_resume_does_not_resend_delivery_after_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            settings = AgentSettings(
                sbs_db_path=td_path / "sbs.db",
                ops_db_path=td_path / "ops.db",
                logs_dir=td_path / "logs",
                artifacts_dir=td_path / "artifacts",
            )
            ops = OpsDB(settings.ops_db_path)
            ops.init_db()
            conv_id = "conv-fulfill-1"
            ops.upsert_conversation(
                conversation_id=conv_id,
                source_entity_detail_id=1,
                email_normalized="buyer@example.com",
                offer_key="capability_statement_v1",
                attempt_id="attempt-x",
                agentmail_inbox=settings.agentmail_sales_inbox,
                state="awaiting_reply",
                thread_metadata={},
            )
            pdf = td_path / "deliverable.pdf"
            pdf.write_bytes(b"%PDF-1.4\nstub")
            job_id = "job-1"
            ops.create_fulfillment_job(
                {
                    "job_id": job_id,
                    "conversation_id": conv_id,
                    "offer_key": "capability_statement_v1",
                    "status": "completed",
                    "inputs_json": {"source_entity_detail_id": 1},
                    "artifacts_json": {"artifacts": [str(pdf)]},
                }
            )
            ops.create_fulfillment_job(
                {
                    "job_id": "job-2",
                    "conversation_id": conv_id,
                    "offer_key": "capability_statement_v1",
                    "status": "completed",
                    "inputs_json": {"source_entity_detail_id": 1},
                    "artifacts_json": {"artifacts": [str(pdf)]},
                }
            )

            with patch(
                "sbs_sales_agent.integrations.agentmail.AgentMailClient.send_message",
                side_effect=[
                    {"message_id": "delivery-1", "thread_id": "thread-1"},
                    RuntimeError("survey failed"),
                    {"message_id": "delivery-2", "thread_id": "thread-2"},
                    {"message_id": "survey-2", "thread_id": "thread-2"},
                ],
            ):
                first = send_fulfillment_and_survey(settings, dry_run=False)
            self.assertTrue(first["ok"])
            self.assertEqual(first["sent_delivery"], 2)
            self.assertEqual(first["sent_survey"], 1)
            self.assertEqual(first["failed"], 1)

            with patch(
                "sbs_sales_agent.integrations.agentmail.AgentMailClient.send_message",
                return_value={"message_id": "survey-1", "thread_id": "thread-1"},
            ):
                out = send_fulfillment_and_survey(settings, dry_run=False)
            self.assertTrue(out["ok"])
            self.assertEqual(out["sent_delivery"], 0)
            self.assertEqual(out["sent_survey"], 1)

            with ops.session() as conn:
                delivery_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM email_messages WHERE conversation_id = ? AND json_extract(metadata_json, '$.send_phase') = 'fulfillment_delivery'",
                    (conv_id,),
                ).fetchone()
                survey_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM email_messages WHERE conversation_id = ? AND json_extract(metadata_json, '$.send_phase') = 'survey'",
                    (conv_id,),
                ).fetchone()
                job = conn.execute("SELECT status, artifacts_json FROM fulfillment_jobs WHERE job_id = ?", (job_id,)).fetchone()
            self.assertEqual(int(delivery_rows["n"]), 2)
            self.assertEqual(int(survey_rows["n"]), 2)
            self.assertEqual(str(job["status"]), "delivered")
            self.assertIn("delivery_email_message_id", str(job["artifacts_json"]))
            self.assertIn("survey_email_message_id", str(job["artifacts_json"]))

    def test_send_fulfillment_ignores_blank_or_directory_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            settings = AgentSettings(
                sbs_db_path=td_path / "sbs.db",
                ops_db_path=td_path / "ops.db",
                logs_dir=td_path / "logs",
                artifacts_dir=td_path / "artifacts",
            )
            ops = OpsDB(settings.ops_db_path)
            ops.init_db()
            conv_id = "conv-fulfill-artifacts"
            ops.upsert_conversation(
                conversation_id=conv_id,
                source_entity_detail_id=1,
                email_normalized="buyer@example.com",
                offer_key="capability_statement_v1",
                attempt_id="attempt-z",
                agentmail_inbox=settings.agentmail_sales_inbox,
                state="awaiting_reply",
                thread_metadata={},
            )
            pdf = td_path / "deliverable.pdf"
            pdf.write_bytes(b"%PDF-1.4\nstub")
            job_id = "job-artifacts"
            ops.create_fulfillment_job(
                {
                    "job_id": job_id,
                    "conversation_id": conv_id,
                    "offer_key": "capability_statement_v1",
                    "status": "completed",
                    "inputs_json": {"source_entity_detail_id": 1},
                    "artifacts_json": {
                        "artifacts": [
                            str(pdf),
                            "",
                            "   ",
                            str(td_path),
                            str(td_path / "missing.json"),
                        ]
                    },
                }
            )

            with patch(
                "sbs_sales_agent.integrations.agentmail.AgentMailClient.send_message",
                side_effect=[
                    {"message_id": "delivery-1", "thread_id": "thread-1"},
                    {"message_id": "survey-1", "thread_id": "thread-1"},
                ],
            ) as mocked_send:
                out = send_fulfillment_and_survey(settings, dry_run=False)

            self.assertTrue(out["ok"])
            self.assertEqual(out["sent_delivery"], 1)
            self.assertEqual(out["sent_survey"], 1)
            first_call_kwargs = mocked_send.call_args_list[0].kwargs
            attachments = list(first_call_kwargs.get("attachments") or [])
            self.assertEqual(len(attachments), 1)
            self.assertEqual(str(attachments[0]), str(pdf))

    def test_run_fulfillment_jobs_continues_after_single_job_exception(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sbs_db = td_path / "sbs_live.db"
            ops_db = td_path / "ops.db"
            _create_test_sbs_db(sbs_db)
            settings = AgentSettings(
                sbs_db_path=sbs_db,
                ops_db_path=ops_db,
                logs_dir=td_path / "logs",
                artifacts_dir=td_path / "artifacts",
            )
            ops = OpsDB(settings.ops_db_path)
            ops.init_db()
            ops.create_fulfillment_job(
                {
                    "job_id": "job-a",
                    "conversation_id": "conv-a",
                    "offer_key": "capability_statement_v1",
                    "status": "queued",
                    "inputs_json": {"source_entity_detail_id": 1},
                    "artifacts_json": {},
                }
            )
            ops.create_fulfillment_job(
                {
                    "job_id": "job-b",
                    "conversation_id": "conv-b",
                    "offer_key": "capability_statement_v1",
                    "status": "queued",
                    "inputs_json": {"source_entity_detail_id": 2},
                    "artifacts_json": {},
                }
            )

            with patch("sbs_sales_agent.worker.fetch_website_context", return_value={}), patch(
                "sbs_sales_agent.worker.build_capability_statement_artifacts",
                side_effect=[RuntimeError("render failed"), {"artifacts": ["deliverable.pdf"]}],
            ), patch("sbs_sales_agent.worker.validate_capability_artifacts", return_value={"ok": True}):
                out = run_fulfillment_jobs(settings)

            self.assertTrue(out["ok"])
            self.assertEqual(out["failed"], 1)
            self.assertEqual(out["completed"], 1)
            self.assertEqual(out["scanned"], 2)

            with ops.session() as conn:
                row_a = conn.execute("SELECT status, artifacts_json FROM fulfillment_jobs WHERE job_id = 'job-a'").fetchone()
                row_b = conn.execute("SELECT status, artifacts_json FROM fulfillment_jobs WHERE job_id = 'job-b'").fetchone()
            self.assertEqual(str(row_a["status"]), "failed")
            self.assertIn("fulfillment_exception", str(row_a["artifacts_json"]))
            self.assertEqual(str(row_b["status"]), "completed")

    def test_pending_fulfillment_jobs_skips_fresh_running_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            ops = OpsDB(td_path / "ops.db")
            ops.init_db()
            now_iso = datetime.now(timezone.utc).isoformat()
            stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
            with ops.session() as conn:
                conn.execute(
                    """
                    INSERT INTO fulfillment_jobs (job_id, conversation_id, offer_key, status, inputs_json, started_at)
                    VALUES ('queued-1', 'conv-1', 'capability_statement_v1', 'queued', '{}', NULL)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO fulfillment_jobs (job_id, conversation_id, offer_key, status, inputs_json, started_at)
                    VALUES ('running-fresh', 'conv-2', 'capability_statement_v1', 'running', '{}', ?)
                    """,
                    (now_iso,),
                )
                conn.execute(
                    """
                    INSERT INTO fulfillment_jobs (job_id, conversation_id, offer_key, status, inputs_json, started_at)
                    VALUES ('running-stale', 'conv-3', 'capability_statement_v1', 'running', '{}', ?)
                    """,
                    (stale_iso,),
                )
            pending_ids = {str(r["job_id"]) for r in ops.pending_fulfillment_jobs(running_stale_after_minutes=45)}
            self.assertIn("queued-1", pending_ids)
            self.assertIn("running-stale", pending_ids)
            self.assertNotIn("running-fresh", pending_ids)

    def test_update_fulfillment_job_sets_started_at_when_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            ops = OpsDB(td_path / "ops.db")
            ops.init_db()
            ops.create_fulfillment_job(
                {
                    "job_id": "job-started-at",
                    "conversation_id": "conv-1",
                    "offer_key": "capability_statement_v1",
                    "status": "queued",
                    "inputs_json": {"source_entity_detail_id": 1},
                    "artifacts_json": {},
                }
            )
            ops.update_fulfillment_job("job-started-at", status="running")
            with ops.session() as conn:
                row = conn.execute("SELECT status, started_at FROM fulfillment_jobs WHERE job_id = 'job-started-at'").fetchone()
            self.assertEqual(str(row["status"]), "running")
            self.assertTrue(str(row["started_at"] or "").strip())

    def test_dispatch_scheduled_messages_continues_after_single_send_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            ops = OpsDB(td_path / "ops.db")
            ops.init_db()
            ops.insert_email_message(
                {
                    "message_id": "m1",
                    "channel": "agentmail",
                    "direction": "outbound",
                    "mailbox": "sales@example.com",
                    "subject": "s1",
                    "body_text": "b1",
                    "recipient_email": "a@example.com",
                    "sender_email": "sales@example.com",
                    "conversation_id": "c1",
                    "delivery_status": "queued",
                    "metadata_json": {"scheduled_for": "2000-01-01T00:00:00+00:00"},
                }
            )
            ops.insert_email_message(
                {
                    "message_id": "m2",
                    "channel": "agentmail",
                    "direction": "outbound",
                    "mailbox": "sales@example.com",
                    "subject": "s2",
                    "body_text": "b2",
                    "recipient_email": "b@example.com",
                    "sender_email": "sales@example.com",
                    "conversation_id": "c2",
                    "delivery_status": "queued",
                    "metadata_json": {"scheduled_for": "2000-01-01T00:00:00+00:00"},
                }
            )
            settings = AgentSettings(ops_db_path=td_path / "ops.db", logs_dir=td_path / "logs", artifacts_dir=td_path / "artifacts")
            with patch(
                "sbs_sales_agent.integrations.agentmail.AgentMailClient.send_message",
                side_effect=[RuntimeError("network"), {"message_id": "ok-2", "thread_id": "t2"}],
            ):
                out = dispatch_scheduled_messages(settings, dry_run=False)
            self.assertTrue(out["ok"])
            self.assertEqual(out["sent"], 1)
            self.assertEqual(out["failed"], 1)
            with ops.session() as conn:
                m1 = conn.execute("SELECT delivery_status FROM email_messages WHERE message_id = 'm1'").fetchone()
                m2 = conn.execute("SELECT delivery_status FROM email_messages WHERE message_id = 'm2'").fetchone()
            self.assertEqual(str(m1["delivery_status"]), "failed")
            self.assertEqual(str(m2["delivery_status"]), "sent")

    def test_end_to_end_mocked_flow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sbs_db = td_path / "sbs_live.db"
            ops_db = td_path / "ops.db"
            logs_dir = td_path / "logs"
            artifacts_dir = td_path / "artifacts"
            _create_test_sbs_db(sbs_db)

            settings = AgentSettings(
                sbs_db_path=sbs_db,
                ops_db_path=ops_db,
                logs_dir=logs_dir,
                artifacts_dir=artifacts_dir,
            )
            settings.dry_run_default = True
            settings.use_llm_first_touch = False
            settings.per_run_offer_cap = 1
            settings.reply_delay_min_minutes = 0
            settings.reply_delay_max_minutes = 0
            settings.precheck_hold_min_minutes = 0
            settings.precheck_hold_max_minutes = 0

            orchestrator = run_orchestrator(settings, slot="09", dry_run=True)
            self.assertTrue(orchestrator["ok"])
            run_id = orchestrator["run_id"]

            precheck_eval = process_due_prechecks(settings, dry_run=True)
            self.assertTrue(precheck_eval["ok"])
            self.assertGreaterEqual(precheck_eval["safe"], 1)

            with patch("sbs_sales_agent.worker._within_initial_send_window", return_value=True):
                main_send = send_main_outreach_from_passed_prechecks(settings, run_id=run_id, dry_run=True)
            self.assertTrue(main_send["ok"])
            self.assertGreaterEqual(main_send["sent"], 1)

            ops = OpsDB(ops_db)
            ops.init_db()
            conv = ops.find_conversation_by_email("jane@alpha.example") or ops.find_conversation_by_email("bob@bravo.example")
            self.assertIsNotNone(conv)
            assert conv is not None

            provider_thread_id = "dry-thread-inbound-1"
            # Seed an outbound message thread id for correlation if needed.
            with ops.session() as conn:
                conn.execute(
                    "UPDATE email_messages SET provider_thread_id = ? WHERE conversation_id = ? AND direction = 'outbound'",
                    (provider_thread_id, str(conv["conversation_id"])),
                )

            fake_inbound = {
                "items": [
                    {
                        "message_id": "inbound-1",
                        "thread_id": provider_thread_id,
                        "from": str(conv["email_normalized"]),
                        "subject": "Re: quick question",
                        "text": "Interested. How much? Send invoice.",
                        "preview": "Interested. How much? Send invoice.",
                    }
                ]
            }
            with patch("sbs_sales_agent.integrations.agentmail.AgentMailClient.list_messages", return_value=fake_inbound), patch(
                "sbs_sales_agent.worker.trigger_invoice_for_conversation",
                return_value={"ok": True, "invoice": {"id": "dry-inv-inline", "public_url": "https://example.com/invoice-inline"}},
            ), patch("sbs_sales_agent.inbound.classifier.InboundClassifier._ollama_classify", return_value=None), patch(
                "sbs_sales_agent.inbound.reply_agent.SalesReplyAgent._ollama_reply",
                return_value=None,
            ):
                poll_result = poll_agentmail_inbox(settings, ops, dry_run=False)
            self.assertTrue(poll_result["ok"])
            self.assertEqual(poll_result["processed"], 1)
            self.assertGreaterEqual(poll_result["queued_replies"], 1)

            offer_prices = {"dsbs_rewrite_v1": 14900, "capability_statement_v1": 19900}
            invoice_result = trigger_invoice_for_conversation(
                settings,
                conversation_id=str(conv["conversation_id"]),
                customer_email=str(conv["email_normalized"]),
                customer_name="Test Buyer",
                offer_key=str(conv["offer_key"]),
                amount_cents=offer_prices[str(conv["offer_key"])],
                dry_run=True,
            )
            self.assertTrue(invoice_result["ok"])
            invoice_id = str(invoice_result["invoice"]["id"])

            webhook_payload = {
                "type": "invoice.payment_made",
                "data": {
                    "object": {
                        "invoice": {
                            "id": invoice_id,
                            "status": "PAID",
                            "order_id": f"dry-order-{str(conv['conversation_id'])[:8]}",
                        }
                    }
                },
            }
            webhook_result = process_square_webhook_payload(ops, webhook_payload)
            self.assertTrue(webhook_result["ok"])
            self.assertTrue(webhook_result["marked_paid"])
            self.assertTrue(webhook_result["fulfillment_job_created"])

            fulfill_result = run_fulfillment_jobs(settings)
            self.assertTrue(fulfill_result["ok"])
            self.assertGreaterEqual(fulfill_result["completed"], 1)
            self.assertTrue(any(artifacts_dir.iterdir()))

    def test_ollama_classifier_and_reply_fallback(self) -> None:
        from sbs_sales_agent.inbound.classifier import InboundClassifier
        from sbs_sales_agent.inbound.reply_agent import SalesReplyAgent
        from sbs_sales_agent.models import ClassificationBundle, ClassificationResult

        settings = AgentSettings()
        with patch("sbs_sales_agent.integrations.ollama_client.OllamaClient.chat_json", return_value={
            "safety": "clear",
            "bounce_system": "none",
            "intent": "positive_interest",
            "payment": "none",
            "fulfillment": "unknown",
            "survey_feedback": "none",
            "confidence": 0.9,
        }):
            bundle = InboundClassifier(settings).classify("Interested. How much?")
            self.assertEqual(bundle.label_for("intent"), "positive_interest")

        with patch("sbs_sales_agent.integrations.ollama_client.OllamaClient.chat_json", return_value={"body": "Thanks for the reply. I can handle it for a flat $149. Want me to send the invoice?"}):
            action = SalesReplyAgent(settings).next_action(
                classifications=ClassificationBundle(
                    stages=[
                        ClassificationResult("safety", "clear", 0.8, {}),
                        ClassificationResult("bounce_system", "none", 0.8, {}),
                        ClassificationResult("intent", "positive_interest", 0.8, {}),
                        ClassificationResult("payment", "none", 0.8, {}),
                        ClassificationResult("fulfillment", "unknown", 0.2, {}),
                        ClassificationResult("survey_feedback", "none", 0.2, {}),
                    ]
                ),
                offer_price_cents=14900,
            )
            self.assertEqual(action.action, "reply")
            self.assertIn("flat $149", action.reply_body or "")

        # Pre-sale "Before I pay..." style questions should not trigger payment-support canned replies.
        with patch("sbs_sales_agent.integrations.ollama_client.OllamaClient.chat_json", return_value={"body": "Included: a 1-page PDF, editable source, and a same-day draft. Usually delivered within 24 hours. Yes, it's a PDF you can forward."}):
            action2 = SalesReplyAgent(settings).next_action(
                classifications=ClassificationBundle(
                    stages=[
                        ClassificationResult("safety", "clear", 0.8, {}),
                        ClassificationResult("bounce_system", "none", 0.8, {}),
                        ClassificationResult("intent", "needs_info", 0.8, {}),
                        ClassificationResult("payment", "payment_related", 0.8, {}),
                        ClassificationResult("fulfillment", "unknown", 0.2, {}),
                        ClassificationResult("survey_feedback", "none", 0.2, {}),
                    ]
                ),
                offer_price_cents=19900,
            )
            self.assertEqual(action2.action, "reply")
            self.assertNotIn("resend the invoice/payment link", (action2.reply_body or "").lower())

        with patch("sbs_sales_agent.integrations.ollama_client.OllamaClient.chat_json", return_value={"body": ""}):
            action3 = SalesReplyAgent(settings).next_action(
                classifications=ClassificationBundle(
                    stages=[
                        ClassificationResult("safety", "clear", 0.8, {}),
                        ClassificationResult("bounce_system", "none", 0.8, {}),
                        ClassificationResult("intent", "needs_info", 0.8, {}),
                        ClassificationResult("payment", "payment_related", 0.8, {}),
                        ClassificationResult("fulfillment", "unknown", 0.2, {}),
                        ClassificationResult("survey_feedback", "none", 0.2, {}),
                    ]
                ),
                offer_price_cents=19900,
                offer_key="capability_statement_v1",
            )
            self.assertIn("1-page capability statement PDF", action3.reply_body or "")

    def test_poller_persists_cursor_and_skips_old_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            ops = OpsDB(td_path / "ops.db")
            ops.init_db()
            conv_id = "conv-1"
            ops.upsert_conversation(
                conversation_id=conv_id,
                source_entity_detail_id=1,
                email_normalized="buyer@example.com",
                offer_key="dsbs_rewrite_v1",
                attempt_id=None,
                agentmail_inbox="neilfox@agentmail.to",
                state="awaiting_reply",
                thread_metadata={},
            )
            ops.insert_email_message(
                {
                    "channel": "agentmail",
                    "direction": "outbound",
                    "mailbox": "neilfox@agentmail.to",
                    "provider_message_id": "seed-out-1",
                    "provider_thread_id": "thread-123",
                    "subject": "hi",
                    "body_text": "hello",
                    "recipient_email": "buyer@example.com",
                    "sender_email": "neilfox@agentmail.to",
                    "conversation_id": conv_id,
                    "sent_at": "2026-02-24T12:00:00Z",
                    "delivery_status": "sent",
                    "metadata_json": {},
                }
            )
            ops.set_runtime_kv("poller_cursor:agentmail:neilfox@agentmail.to", "2026-02-24T12:00:00Z")
            settings = AgentSettings(agentmail_api_key="x")
            settings.test_mode = True
            settings.reply_delay_min_minutes = 0
            settings.reply_delay_max_minutes = 0
            fake_inbound = {
                "items": [
                    {
                        "message_id": "old-1",
                        "thread_id": "thread-123",
                        "from": "buyer@example.com",
                        "subject": "Re: hi",
                        "text": "Old one",
                        "created_at": "2026-02-24T11:59:59Z",
                    },
                    {
                        "message_id": "new-1",
                        "thread_id": "thread-123",
                        "from": "buyer@example.com",
                        "subject": "Re: hi",
                        "text": "Interested, send details.",
                        "created_at": "2026-02-24T12:00:01Z",
                    },
                ]
            }
            with patch("sbs_sales_agent.integrations.agentmail.AgentMailClient.list_messages", return_value=fake_inbound), patch(
                "sbs_sales_agent.inbound.classifier.InboundClassifier._ollama_classify", return_value=None
            ), patch("sbs_sales_agent.inbound.reply_agent.SalesReplyAgent._ollama_reply", return_value=None):
                result = poll_agentmail_inbox(settings, ops, dry_run=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["processed"], 1)
            self.assertEqual(
                ops.get_runtime_kv("poller_cursor:agentmail:neilfox@agentmail.to"),
                "2026-02-24T12:00:01Z",
            )


if __name__ == "__main__":
    unittest.main()
