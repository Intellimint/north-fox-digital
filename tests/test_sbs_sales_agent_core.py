from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from sbs_sales_agent.config import AgentSettings
from sbs_sales_agent.db import OpsDB
from sbs_sales_agent.features import (
    features_from_sbs_row,
    greeting_name,
    is_valid_email,
    normalize_business_name,
    normalize_person_name,
)
from sbs_sales_agent.inbound.classifier import InboundClassifier
from sbs_sales_agent.inbound.reply_agent import SalesReplyAgent
from sbs_sales_agent.learning.reward import RewardInputs, compute_reward
from sbs_sales_agent.models import ClassificationBundle, ClassificationResult, Offer, OfferVariant
from sbs_sales_agent.offers.generator import _select_light_findings, build_initial_outreach, count_words
from sbs_sales_agent.payments.square_webhooks import verify_square_signature
from sbs_sales_agent.scheduling import schedule_reply_send
from sbs_sales_agent.selection import _has_capability_statement_listing, eligible_for_initial_outreach
from sbs_sales_agent.fulfillment.pdf_render import render_capability_data_to_pdf, render_html_to_pdf
from sbs_sales_agent.integrations.agentmail import AgentMailClient
from sbs_sales_agent.worker import _extract_light_findings


class CoreTests(unittest.TestCase):
    def test_name_normalization_all_caps(self) -> None:
        self.assertEqual(normalize_person_name("MICHAEL FRAY"), "Michael Fray")
        self.assertEqual(normalize_person_name("ACME LLC"), "Acme LLC")
        self.assertEqual(normalize_business_name("MODPROS ELEVATOR, INC."), "Modpros Elevator, Inc.")
        self.assertEqual(greeting_name("DEBBIE PYLE", "PYLE PERCUSSION"), "Debbie")
        self.assertEqual(greeting_name(None, None), "there")

    def test_feature_extraction_from_row(self) -> None:
        row = {
            "entity_detail_id": 1,
            "email": "OWNER@Example.com ",
            "legal_business_name": "TEST LLC",
            "contact_person": "JANE DOE",
            "website": "www.example.com",
            "phone": "555-5555",
            "state": "FL",
            "city": "Kissimmee",
            "zipcode": "34741",
            "naics_primary": "541512",
            "description": None,
            "keywords": None,
            "certs": "[]",
            "tags": "[]",
            "uei": "ABC123",
            "cage_code": "XYZ99",
            "display_email": 1,
            "public_display": 1,
            "public_display_limited": 0,
            "raw": json.dumps(
                {
                    "naics_all_codes": ["541512", "541519"],
                    "keywords": ["IT", "Cybersecurity"],
                    "self_small_boolean": True,
                    "capabilities_narrative": "Managed IT and cybersecurity services.",
                }
            ),
        }
        f = features_from_sbs_row(row)
        self.assertEqual(f.email, "owner@example.com")
        self.assertEqual(f.contact_name_normalized, "Jane Doe")
        self.assertEqual(f.first_name_for_greeting, "Jane")
        self.assertEqual(f.naics_all_codes, ["541512", "541519"])
        self.assertTrue(f.self_small_boolean)
        self.assertEqual(f.capabilities_narrative, "Managed IT and cybersecurity services.")

    def test_email_validation(self) -> None:
        self.assertTrue(is_valid_email("a@b.com"))
        self.assertFalse(is_valid_email("bad"))

    def test_copy_constraints_and_footer(self) -> None:
        settings = AgentSettings()
        settings.use_llm_first_touch = False
        offer = Offer(
            offer_key="test",
            offer_type="DSBS_REWRITE",
            price_cents=10000,
            fulfillment_workflow_key="x",
            targeting_rules={},
            sales_constraints={"max_main_words": 50},
        )
        variant = OfferVariant(
            variant_key="v1",
            offer_key="test",
            subject_template="Saw {{business_name}}",
            body_template="Saw {{business_name}}. I help clean up your DSBS profile for better buyer search matching. Want me to send how?",
            style_tags=[],
        )
        prospect = features_from_sbs_row(
            {
                "entity_detail_id": 2,
                "email": "t@example.com",
                "legal_business_name": "TESTCO",
                "contact_person": "BOB SMITH",
                "website": None,
                "phone": None,
                "state": "FL",
                "city": "Orlando",
                "zipcode": "32801",
                "naics_primary": "541611",
                "description": "desc",
                "keywords": '["consulting"]',
                "certs": "[]",
                "tags": "[]",
                "uei": None,
                "cage_code": None,
                "display_email": 1,
                "public_display": 1,
                "public_display_limited": 0,
                "raw": "{}",
            }
        )
        subject, body = build_initial_outreach(settings=settings, offer=offer, variant=variant, prospect=prospect)
        self.assertIn("Testco", subject)
        main = body.split("\n\n")[0]
        self.assertLessEqual(count_words(main), 50)
        self.assertIn("Sincerely,", body)
        self.assertIn(settings.unsubscribe_footer, body)
        self.assertIn(settings.sender_address_footer, body)

    def test_extract_light_findings_keeps_highest_severity_duplicate_title(self) -> None:
        payload = {
            "findings": [
                {"title": "Missing recommended HTTP security headers", "severity": "low", "category": "security"},
                {"title": "Missing recommended HTTP security headers", "severity": "high", "category": "security"},
                {"title": "No H1 heading found on homepage", "severity": "medium", "category": "seo"},
            ]
        }
        out = _extract_light_findings(payload, max_items=3)
        self.assertEqual(len(out), 2)
        top = out[0]
        self.assertEqual(top["title"], "Missing recommended HTTP security headers")
        self.assertEqual(top["severity"], "high")
        self.assertEqual(top["category"], "security")

    def test_select_light_findings_keeps_highest_severity_duplicate_title(self) -> None:
        rows = [
            {"title": "No H1 heading found on homepage", "severity": "low", "category": "seo"},
            {"title": "No H1 heading found on homepage", "severity": "high", "category": "seo"},
            {"title": "Missing viewport meta tag", "severity": "medium", "category": "conversion"},
        ]
        picks = _select_light_findings(rows, max_items=2)
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks[0]["title"], "No H1 heading found on homepage")
        self.assertEqual(picks[0]["severity"], "high")

    def test_web_report_outreach_uses_finding_specific_risk_line(self) -> None:
        settings = AgentSettings()
        settings.use_llm_first_touch = False
        offer = Offer(
            offer_key="web_report_test",
            offer_type="WEB_PRESENCE_REPORT",
            price_cents=29900,
            fulfillment_workflow_key="x",
            targeting_rules={},
            sales_constraints={"max_main_words": 140},
        )
        variant = OfferVariant(
            variant_key="unused",
            offer_key="web_report_test",
            subject_template="unused",
            body_template="unused",
            style_tags=[],
        )
        prospect = features_from_sbs_row(
            {
                "entity_detail_id": 10,
                "email": "owner@example.com",
                "legal_business_name": "Sample Co",
                "contact_person": "SAM OWNER",
                "website": "https://example.com",
                "phone": "555-5555",
                "state": "FL",
                "city": "Orlando",
                "zipcode": "32801",
                "naics_primary": "541611",
                "description": "Business consulting",
                "keywords": '["consulting"]',
                "certs": "[]",
                "tags": "[]",
                "uei": None,
                "cage_code": None,
                "display_email": 1,
                "public_display": 1,
                "public_display_limited": 0,
                "raw": "{}",
            }
        )
        _subject, body = build_initial_outreach(
            settings=settings,
            offer=offer,
            variant=variant,
            prospect=prospect,
            light_findings=[{"title": "No H1 heading found on homepage", "severity": "high", "category": "seo"}],
        )
        self.assertIn("hide important pages from Google", body)
        self.assertNotIn("send emails that look like they came from your company", body)

    def test_settings_from_env_reads_current_environment(self) -> None:
        old_url = os.environ.get("SBS_AGENT_LOCAL_MAIL_API_URL")
        old_test_mode = os.environ.get("SBS_AGENT_TEST_MODE")
        old_timeout = os.environ.get("SBS_AGENT_REQUEST_TIMEOUT_SECONDS")
        try:
            os.environ["SBS_AGENT_LOCAL_MAIL_API_URL"] = "http://example.test:8081"
            os.environ["SBS_AGENT_TEST_MODE"] = "true"
            os.environ["SBS_AGENT_REQUEST_TIMEOUT_SECONDS"] = "42.5"
            settings = AgentSettings.from_env()
            self.assertEqual(settings.local_mail_api_url, "http://example.test:8081")
            self.assertTrue(settings.test_mode)
            self.assertEqual(settings.request_timeout_seconds, 42.5)
        finally:
            if old_url is None:
                os.environ.pop("SBS_AGENT_LOCAL_MAIL_API_URL", None)
            else:
                os.environ["SBS_AGENT_LOCAL_MAIL_API_URL"] = old_url
            if old_test_mode is None:
                os.environ.pop("SBS_AGENT_TEST_MODE", None)
            else:
                os.environ["SBS_AGENT_TEST_MODE"] = old_test_mode
            if old_timeout is None:
                os.environ.pop("SBS_AGENT_REQUEST_TIMEOUT_SECONDS", None)
            else:
                os.environ["SBS_AGENT_REQUEST_TIMEOUT_SECONDS"] = old_timeout

    def test_agentmail_send_message_retries_once_for_thread_errors(self) -> None:
        settings = AgentSettings(agentmail_base_url="https://agentmail.example")
        client = AgentMailClient(settings)
        req = httpx.Request("POST", "https://agentmail.example/inboxes/inbox/messages/send")
        sent_payloads: list[dict[str, object]] = []

        def _fake_post(*args: object, **kwargs: object) -> httpx.Response:
            payload = dict(kwargs.get("json") or {})
            sent_payloads.append(payload)
            if len(sent_payloads) == 1:
                return httpx.Response(422, request=req, json={"error": "thread not found"})
            return httpx.Response(200, request=req, json={"message_id": "m1", "thread_id": "t1"})

        with patch.object(
            client.client,
            "post",
            side_effect=_fake_post,
        ) as mocked_post:
            out = client.send_message(
                inbox_id="inbox",
                to=["buyer@example.com"],
                subject="Subject",
                text="Body",
                thread_id="thread-old",
            )
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(str(out["message_id"]), "m1")
        self.assertEqual(str(sent_payloads[0].get("thread_id") or ""), "thread-old")
        self.assertNotIn("thread_id", sent_payloads[1])

    def test_agentmail_send_message_does_not_retry_for_auth_error(self) -> None:
        settings = AgentSettings(agentmail_base_url="https://agentmail.example")
        client = AgentMailClient(settings)
        req = httpx.Request("POST", "https://agentmail.example/inboxes/inbox/messages/send")
        with patch.object(
            client.client,
            "post",
            return_value=httpx.Response(401, request=req, json={"error": "unauthorized"}),
        ) as mocked_post:
            with self.assertRaises(httpx.HTTPStatusError):
                client.send_message(
                    inbox_id="inbox",
                    to=["buyer@example.com"],
                    subject="Subject",
                    text="Body",
                    thread_id="thread-old",
                )
        self.assertEqual(mocked_post.call_count, 1)

    def test_unsubscribe_precedence_in_classifier(self) -> None:
        bundle = InboundClassifier().classify("Interested, but also unsubscribe me please")
        self.assertEqual(bundle.label_for("safety"), "unsubscribe")
        self.assertEqual(bundle.label_for("intent"), "unsubscribe")

    def test_reply_delay_scheduling_range(self) -> None:
        now = datetime(2026, 2, 24, 14, 0, tzinfo=timezone.utc)
        scheduled = schedule_reply_send(now, 5, 10)
        delta_min = (scheduled - now).total_seconds() / 60
        self.assertGreaterEqual(delta_min, 5)
        self.assertLessEqual(delta_min, 10)

    def test_square_signature_verification(self) -> None:
        import base64
        import hashlib
        import hmac

        url = "https://example.com/v1/webhooks/square"
        body = '{"type":"invoice.payment_made"}'
        key = "secret"
        sig = base64.b64encode(hmac.new(key.encode(), f"{url}{body}".encode(), hashlib.sha256).digest()).decode()
        self.assertTrue(verify_square_signature(url, body, sig, key))
        self.assertFalse(verify_square_signature(url, body, "bad", key))

    def test_reward_weights_cash_priority(self) -> None:
        settings = AgentSettings()
        a = compute_reward(settings, RewardInputs(cash_collected_cents=10000, positive_replies=1, replies=2))
        b = compute_reward(settings, RewardInputs(cash_collected_cents=0, positive_replies=10, replies=20))
        self.assertGreater(a, b)

    def test_suppression_and_cooldown_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ops_path = Path(td) / "ops.db"
            ops = OpsDB(ops_path)
            ops.init_db()
            ops.suppress_email(
                suppression_id="s1",
                email_normalized="bad@example.com",
                reason="unsubscribe",
                source_entity_detail_id=1,
            )
            ok, reason = eligible_for_initial_outreach(ops_db=ops, entity_id=1, email_normalized="bad@example.com")
            self.assertFalse(ok)
            self.assertEqual(reason, "suppressed")

    def test_capability_listing_filter_heuristic(self) -> None:
        prospect = features_from_sbs_row(
            {
                "entity_detail_id": 7,
                "email": "owner@example.com",
                "legal_business_name": "Sample Co",
                "contact_person": "SAM OWNER",
                "website": "example.com",
                "phone": "555",
                "state": "FL",
                "city": "Orlando",
                "zipcode": "32801",
                "naics_primary": "541611",
                "description": "Business consulting",
                "keywords": '["consulting"]',
                "certs": "[]",
                "tags": "[]",
                "uei": None,
                "cage_code": None,
                "display_email": 1,
                "public_display": 1,
                "public_display_limited": 0,
                "raw": json.dumps({"capability_statement_url": "https://example.com/cap-statement.pdf"}),
            }
        )
        self.assertTrue(_has_capability_statement_listing(prospect))

    def test_reply_agent_needs_info_quality(self) -> None:
        settings = AgentSettings()
        action = SalesReplyAgent(settings).next_action(
            classifications=ClassificationBundle(
                stages=[
                    ClassificationResult("safety", "clear", 0.9, {}),
                    ClassificationResult("bounce_system", "none", 0.9, {}),
                    ClassificationResult("intent", "needs_info", 0.9, {}),
                    ClassificationResult("payment", "payment_related", 0.9, {}),
                    ClassificationResult("fulfillment", "unknown", 0.2, {}),
                    ClassificationResult("survey_feedback", "none", 0.2, {}),
                ]
            ),
            offer_price_cents=19900,
            offer_key="capability_statement_v1",
        )
        self.assertEqual(action.action, "reply")
        self.assertNotIn("[", action.reply_body or "")
        self.assertNotIn("thanks for reaching out", (action.reply_body or "").lower())

    def test_pdf_fallback_is_real_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            html = Path(td) / "in.html"
            pdf = Path(td) / "out.pdf"
            html.write_text("<html><body><h1>Test</h1></body></html>", encoding="utf-8")
            with patch.dict("sys.modules", {"weasyprint": None}):
                result = render_html_to_pdf(html, pdf)
            self.assertEqual(result.get("ok"), "true")
            self.assertTrue(pdf.exists())
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-1.4"))

    def test_capability_data_pdf_contains_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "cap.pdf"
            result = render_capability_data_to_pdf(
                {
                    "business_name": "Acme Services",
                    "uei": "UEI123",
                    "cage_code": "CAGE1",
                    "capability_summary": "Acme provides logistics and facilities support.",
                    "core_capabilities": ["Logistics support", "Facility maintenance"],
                    "naics_codes": ["561210"],
                    "certifications": ["Woman-Owned Business"],
                    "differentiators": "Fast response and reliable delivery",
                    "contact_name": "Jane Doe",
                    "email": "jane@example.com",
                    "phone": "555-555-1212",
                    "website": "acme.example",
                },
                pdf,
            )
            self.assertEqual(result.get("ok"), "true")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-1.4"))
            self.assertGreater(pdf.stat().st_size, 300)


if __name__ == "__main__":
    unittest.main()
