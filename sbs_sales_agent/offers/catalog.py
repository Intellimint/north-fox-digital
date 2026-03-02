from __future__ import annotations

from .generator import count_words
from ..models import Offer, OfferVariant


def default_offers() -> list[Offer]:
    return [
        Offer(
            offer_key="dsbs_rewrite_v1",
            offer_type="WEB_PRESENCE_REPORT",
            price_cents=29900,
            fulfillment_workflow_key="web_presence_report_pdf",
            targeting_rules={
                "require_small_business": True,
                "prefer_website": True,
                "prefer_public_email": True,
            },
            sales_constraints={"max_main_words": 100, "tone": "hometown_sales_guy"},
        ),
        Offer(
            offer_key="capability_statement_v1",
            offer_type="WEB_PRESENCE_REPORT",
            price_cents=29900,
            fulfillment_workflow_key="web_presence_report_pdf",
            targeting_rules={
                "require_small_business": True,
                "prefer_website": True,
                "prefer_public_email": True,
            },
            sales_constraints={"max_main_words": 100, "tone": "hometown_sales_guy"},
        ),
    ]


def default_offer_variants() -> list[OfferVariant]:
    variants = [
        OfferVariant(
            variant_key="web_report_security_q1",
            offer_key="dsbs_rewrite_v1",
            subject_template="Quick heads up on your website",
            body_template=(
                "Hey {{first_name}}, I ran a quick public check on {{business_name}} and spotted a few issues that can hurt trust and lead flow.\n"
                "I build a Web Presence Risk + Revenue Growth Report that shows exactly what to fix first across security, spoof-risk, search visibility, accessibility, and conversion.\n"
                "Flat $299 with a prioritized 30/60/90 day action plan.\n"
                "Want me to send the highlights I found?"
            ),
            style_tags=["security_first", "pain_point", "flat_price"],
        ),
        OfferVariant(
            variant_key="web_report_security_q2",
            offer_key="dsbs_rewrite_v1",
            subject_template="I found 3 urgent website risks",
            body_template=(
                "Hey {{first_name}}, I checked {{business_name}} and found issues that can make you easier to spoof and harder to trust online.\n"
                "I put together a consultant-style report with screenshots, risk ratings, and a practical fix sequence your developer can use right away.\n"
                "It is $299 flat and built to save weeks of guesswork.\n"
                "Want the short version of what I found?"
            ),
            style_tags=["plainspoken", "evidence_hint", "urgency"],
        ),
        OfferVariant(
            variant_key="web_report_growth_q1",
            offer_key="capability_statement_v1",
            subject_template="Quick wins I found on your site",
            body_template=(
                "Hey {{first_name}}, I reviewed {{business_name}} and found a few quick website fixes tied to security, search visibility, and lead conversion.\n"
                "I package this into a Web Presence Risk + Revenue Growth Report with clear priorities, effort estimates, and exact remediation steps.\n"
                "Flat $299, delivered as a polished PDF your team can execute from.\n"
                "Want me to send the key findings?"
            ),
            style_tags=["value_first", "outcome_focused", "action_plan"],
        ),
        OfferVariant(
            variant_key="web_report_growth_q2",
            offer_key="capability_statement_v1",
            subject_template="Worth fixing this before it costs leads",
            body_template=(
                "Hey {{first_name}}, quick note on {{business_name}}.\n"
                "I found a few avoidable website issues that can affect inbox trust, Google visibility, and form conversion.\n"
                "I can send a full report with page-level evidence, screenshots, and a 30/60/90 day roadmap your team can implement.\n"
                "Flat fee is $299.\n"
                "Want me to send the top issues first?"
            ),
            style_tags=["value_first", "simple", "deliverable_clear"],
        ),
    ]
    for variant in variants:
        if count_words(variant.body_template) > 100:
            raise ValueError(f"variant exceeds 100-word cap: {variant.variant_key}")
    return variants
