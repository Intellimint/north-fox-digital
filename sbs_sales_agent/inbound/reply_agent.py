from __future__ import annotations

import re
from ..config import AgentSettings
from ..integrations.ollama_client import OllamaClient
from ..models import AgentAction, ClassificationBundle

PRICE_OBJECTION_RE = re.compile(r"\b(too expensive|expensive|budget|pricey|can't afford|cost|worth \$?[0-9]+)\b", re.IGNORECASE)
PROOF_OBJECTION_RE = re.compile(r"\b(proof|sample|example|how do i know|skeptic|accurate|evidence)\b", re.IGNORECASE)
PRIVACY_RE = re.compile(r"\b(privacy|data|collected|crawl|scan|backend|secure)\b", re.IGNORECASE)
AGENCY_RE = re.compile(r"\b(we already have an agency|already work with|our agency)\b", re.IGNORECASE)
TIMELINE_RE = re.compile(r"\b(today|asap|urgent|timeline|how fast|turnaround|this week|deadline|by [a-z]+)\b", re.IGNORECASE)
CALL_RE = re.compile(r"\b(call|phone|zoom|meeting|chat)\b", re.IGNORECASE)


class SalesReplyAgent:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.ollama = OllamaClient(settings)

    def _ollama_reply(
        self,
        *,
        intent: str,
        offer_price_cents: int | None = None,
        offer_key: str | None = None,
        inbound_text: str = "",
    ) -> str | None:
        if intent not in {"positive_interest", "needs_info"}:
            return None
        price = f"${offer_price_cents/100:.0f}" if offer_price_cents else "a flat price"
        result = self.ollama.chat_json(
            system=(
                "Write a short plainspoken outbound email reply in a hometown-sales-guy tone. "
                "No hype. No AI wording. Return JSON: {\"body\": \"...\"}. "
                "Keep under 90 words. Keep it email-only (no calls/meetings/chat requests)."
            ),
            user=(
                f"Intent={intent}. Reply over email only. "
                f"Offer={offer_key or 'unknown'}. "
                f"Inbound message: {inbound_text}\n"
                f"If positive_interest, mention fixed price concept ({price}) and ask permission to send invoice/scope. "
                "If needs_info, answer the prospect's likely question directly (scope/turnaround/format) before asking one short follow-up only if needed."
                " For WEB_PRESENCE_REPORT, emphasize page-level evidence, screenshots, and a prioritized fix plan (30/60/90 days)."
                " For DSBS_REWRITE and CAPABILITY_STATEMENT replies, position turnaround as same day or next business day."
            ),
            schema_hint={"type": "object", "properties": {"body": {"type": "string"}}},
        )
        if not isinstance(result, dict) or result.get("ok") is False:
            return None
        body = str(result.get("body") or "").strip()
        if not body:
            return None
        if len(body.split()) > 90:
            return None
        lowered = body.lower()
        if re.search(r"\[[^\]]+\]", body):
            return None
        if "[your name]" in lowered or "your name" in lowered and "[" in body:
            return None
        if re.search(r"\b(call|chat|meeting|zoom|phone)\b", lowered):
            return None
        if offer_key in {"dsbs_rewrite_v1", "capability_statement_v1"} and re.search(r"\b[3-9]\s*[-–]\s*[0-9]+\s*business\s*days\b", lowered):
            return None
        return body

    def _enforce_sender_voice(self, body: str) -> str:
        text = (body or "").strip()
        if not text:
            return text
        sender_first = (self.settings.sender_name.split()[0] if self.settings.sender_name else "").strip().lower()
        # Prevent self-addressed greetings like "Hey Neil" from the Neil account.
        if sender_first and re.match(rf"^\s*(hi|hey)\s+{re.escape(sender_first)}\b", text, flags=re.IGNORECASE):
            text = re.sub(r"^\s*(hi|hey)\s+[a-zA-Z][a-zA-Z.'-]*,?\s*", "", text, flags=re.IGNORECASE).strip()
        # Strip signatures that impersonate prospect names; leave body concise and neutral.
        text = re.sub(r"\n{2,}(thanks|best|sincerely),?\n[^\n]{1,60}$", "", text, flags=re.IGNORECASE).strip()
        return text

    def _strong_fallback_reply(
        self,
        *,
        offer_key: str | None,
        offer_type: str | None,
        offer_price_cents: int | None,
        inbound_text: str,
        intent: str,
    ) -> str:
        price = f"${offer_price_cents/100:.0f}" if offer_price_cents else "a flat fee"
        if offer_type == "WEB_PRESENCE_REPORT":
            if PRICE_OBJECTION_RE.search(inbound_text):
                return (
                    f"Fair question. This is {price} because it is not just a scorecard. "
                    "You get page-level evidence, screenshots, and a prioritized fix plan your developer can execute immediately. "
                    "If helpful, I can send the top 3 issues I found first."
                )
            if PROOF_OBJECTION_RE.search(inbound_text):
                return (
                    "Totally reasonable. Every finding is tied to a specific page URL with evidence and remediation steps. "
                    "I can send a short sample section first so you can see exactly how specific it is."
                )
            if PRIVACY_RE.search(inbound_text):
                return (
                    "Good question. The scan is read-only on publicly accessible pages and DNS records, like a browser would see. "
                    "No backend access, no form submissions, and no private customer data collection."
                )
            if AGENCY_RE.search(inbound_text):
                return (
                    "That is common. This works well as an independent second opinion your agency can execute from. "
                    "It usually surfaces a few high-impact items that were deprioritized."
                )
            if TIMELINE_RE.search(inbound_text):
                return (
                    "Fast turnaround: report delivery is within 24 hours, and the first fixes are prioritized for immediate action. "
                    "You can hand the 0-30 day section to your developer right away."
                )
            if CALL_RE.search(inbound_text):
                return "Happy to keep this simple over email. I can send scope, price, and next steps right here."
            if intent == "positive_interest":
                return (
                    f"Perfect. It is a flat {price}. I can send the exact scope and invoice now, "
                    "then deliver the report within 24 hours after payment."
                )
            return (
                "I focus on the issues that directly affect trust, visibility, and lead flow. "
                "The report includes evidence screenshots, severity, and a practical 30/60/90 day fix plan."
            )

        if offer_key == "capability_statement_v1" and intent == "needs_info":
            return (
                f"Great question. You’d get a clean 1-page capability statement PDF for a flat {price} built from your SBA profile: "
                "core capabilities, NAICS, certs, UEI/CAGE, differentiators, and contact block. "
                "Turnaround is 24 hours and ready to forward."
            )
        if offer_key == "dsbs_rewrite_v1" and intent == "needs_info":
            return (
                f"Great question. It includes a short + long DSBS narrative rewrite, tighter keyword alignment, "
                f"top differentiators, and a paste-ready version for a flat {price}. "
                "Delivery is same day or next business day."
            )
        if CALL_RE.search(inbound_text):
            return "Happy to keep this simple over email. I can send scope, price, and next steps right here."
        if intent == "positive_interest":
            return (
                f"Perfect. It is a flat {price}. I can send the exact scope and invoice now, "
                "then deliver the report within 24 hours after payment."
            )
        return "Happy to keep this simple over email. Tell me what you want to solve first, and I’ll map exact next steps."

    def next_action(
        self,
        *,
        classifications: ClassificationBundle,
        offer_price_cents: int | None = None,
        offer_key: str | None = None,
        offer_type: str | None = None,
        inbound_subject: str = "",
        inbound_body: str = "",
    ) -> AgentAction:
        safety = classifications.label_for("safety")
        bounce = classifications.label_for("bounce_system")
        intent = classifications.label_for("intent")
        inbound_text = f"{inbound_subject}\n{inbound_body}".strip()

        if safety in {"unsubscribe", "legal_or_complaint"}:
            return AgentAction(action="suppress", reason=safety or "safety")
        if bounce == "hard_bounce":
            return AgentAction(action="suppress", reason="hard_bounce")
        if intent == "not_interested":
            return AgentAction(action="close", reason="not_interested")
        if intent in {"positive_interest", "needs_info"}:
            if offer_type == "WEB_PRESENCE_REPORT":
                drafted = self._strong_fallback_reply(
                    offer_key=offer_key,
                    offer_type=offer_type,
                    offer_price_cents=offer_price_cents,
                    inbound_text=inbound_text,
                    intent=intent,
                )
                return AgentAction(
                    action="reply",
                    reason=intent,
                    reply_subject="Re: quick question",
                    reply_body=self._enforce_sender_voice(drafted),
                )
            deterministic_objection = (
                intent == "needs_info"
                and (
                    PRICE_OBJECTION_RE.search(inbound_text)
                    or PROOF_OBJECTION_RE.search(inbound_text)
                    or PRIVACY_RE.search(inbound_text)
                    or AGENCY_RE.search(inbound_text)
                    or TIMELINE_RE.search(inbound_text)
                    or CALL_RE.search(inbound_text)
                )
            )
            drafted = None
            if not deterministic_objection:
                drafted = self._ollama_reply(
                    intent=intent,
                    offer_price_cents=offer_price_cents,
                    offer_key=offer_key,
                    inbound_text=inbound_text,
                )
            if not drafted:
                drafted = self._strong_fallback_reply(
                    offer_key=offer_key,
                    offer_type=offer_type,
                    offer_price_cents=offer_price_cents,
                    inbound_text=inbound_text,
                    intent=intent,
                )
            return AgentAction(
                action="reply",
                reason=intent,
                reply_subject="Re: quick question",
                reply_body=self._enforce_sender_voice(drafted),
            )
        if offer_key == "capability_statement_v1":
            price_line = f" for a flat ${offer_price_cents/100:.0f}" if offer_price_cents else ""
            return AgentAction(
                action="reply",
                reason="needs_info",
                reply_subject="Re: quick question",
                reply_body=(
                    f"Great question. You’d get a clean 1-page capability statement PDF{price_line} built from your SBA profile: "
                    "core capabilities, NAICS, certs, UEI/CAGE, differentiators, and contact block. "
                    "Turnaround is 24 hours, and yes, it is made to forward to buyers/primes."
                ),
            )
        if offer_key == "dsbs_rewrite_v1":
            price_line = f" for a flat ${offer_price_cents/100:.0f}" if offer_price_cents else ""
            return AgentAction(
                action="reply",
                reason="needs_info",
                reply_subject="Re: quick question",
                reply_body=(
                    f"Great question. It includes a short + long DSBS narrative rewrite, tighter NAICS keyword clusters, "
                    f"top differentiators, and a paste-ready version{price_line}. "
                    "Delivery is same day or next business day."
                ),
            )
        return AgentAction(
            action="reply",
            reason="needs_info",
            reply_subject="Re: quick question",
            reply_body="Thanks for the note. Happy to keep this simple over email. What part would be most useful for you first?",
        )
