from __future__ import annotations

import re
from time import perf_counter

from ..config import AgentSettings
from ..integrations.ollama_client import OllamaClient
from ..models import ClassificationBundle, ClassificationResult

UNSUB_RE = re.compile(r"\b(unsubscribe|stop|do not contact|remove me|opt[- ]?out)\b", re.IGNORECASE)
BOUNCE_RE = re.compile(r"\b(undeliverable|delivery status notification|mail delivery subsystem|mailer-daemon)\b", re.IGNORECASE)
OOO_RE = re.compile(r"\b(out of office|auto[- ]?reply|automatic reply)\b", re.IGNORECASE)
POSITIVE_RE = re.compile(r"\b(interested|yes|sounds good|send (?:details|it)|how much|price|quote)\b", re.IGNORECASE)
NEGATIVE_RE = re.compile(r"\b(no thanks|not interested|pass|leave me alone)\b", re.IGNORECASE)
PAYMENT_RE = re.compile(r"\b(invoice|pay|payment link|receipt)\b", re.IGNORECASE)
LEGAL_RE = re.compile(r"\b(lawyer|attorney|legal|can-spam|complaint|report spam)\b", re.IGNORECASE)


class InboundClassifier:
    def __init__(self, settings: AgentSettings | None = None) -> None:
        self.settings = settings
        self.ollama = OllamaClient(settings) if settings is not None else None

    def _rule_classify(self, text: str) -> ClassificationBundle:
        stages: list[ClassificationResult] = []
        start = perf_counter()

        if UNSUB_RE.search(text):
            safety = "unsubscribe"
            safety_conf = 0.99
        elif LEGAL_RE.search(text):
            safety = "legal_or_complaint"
            safety_conf = 0.95
        else:
            safety = "clear"
            safety_conf = 0.75
        stages.append(ClassificationResult(stage="safety", label=safety, confidence=safety_conf, raw={"pattern": "rule"}))

        if BOUNCE_RE.search(text):
            bounce = "hard_bounce"
        elif OOO_RE.search(text):
            bounce = "out_of_office"
        else:
            bounce = "none"
        stages.append(ClassificationResult(stage="bounce_system", label=bounce, confidence=0.95, raw={"pattern": "rule"}))

        if safety == "unsubscribe":
            intent = "unsubscribe"
        elif POSITIVE_RE.search(text):
            intent = "positive_interest"
        elif NEGATIVE_RE.search(text):
            intent = "not_interested"
        else:
            intent = "needs_info"
        stages.append(ClassificationResult(stage="intent", label=intent, confidence=0.8, raw={"pattern": "rule"}))

        payment_label = "payment_related" if PAYMENT_RE.search(text) else "none"
        stages.append(ClassificationResult(stage="payment", label=payment_label, confidence=0.85, raw={"pattern": "rule"}))
        stages.append(ClassificationResult(stage="fulfillment", label="unknown", confidence=0.25, raw={"pattern": "rule"}))
        stages.append(ClassificationResult(stage="survey_feedback", label="none", confidence=0.2, raw={"pattern": "rule"}))
        elapsed_ms = int((perf_counter() - start) * 1000)
        for item in stages:
            item.raw["latency_ms"] = elapsed_ms
        return ClassificationBundle(stages=stages)

    def _ollama_classify(self, text: str) -> ClassificationBundle | None:
        if self.ollama is None:
            return None
        schema_hint = {
            "type": "object",
            "properties": {
                "safety": {"type": "string"},
                "bounce_system": {"type": "string"},
                "intent": {"type": "string"},
                "payment": {"type": "string"},
                "fulfillment": {"type": "string"},
                "survey_feedback": {"type": "string"},
                "confidence": {"type": "number"},
            },
        }
        result = self.ollama.chat_json(
            system=(
                "Classify an inbound business email reply for an outbound sales agent. "
                "Return strict JSON with labels for safety,bounce_system,intent,payment,fulfillment,survey_feedback and confidence (0-1). "
                "Use labels: safety=[clear,unsubscribe,legal_or_complaint], bounce_system=[none,hard_bounce,out_of_office], "
                "intent=[positive_interest,not_interested,unsubscribe,needs_info], payment=[none,payment_related], "
                "fulfillment=[unknown,fulfillment_related], survey_feedback=[none,feedback]."
            ),
            user=text,
            schema_hint=schema_hint,
        )
        if not isinstance(result, dict) or result.get("ok") is False:
            return None
        try:
            conf = float(result.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        labels = {
            "safety": str(result.get("safety") or "clear"),
            "bounce_system": str(result.get("bounce_system") or "none"),
            "intent": str(result.get("intent") or "needs_info"),
            "payment": str(result.get("payment") or "none"),
            "fulfillment": str(result.get("fulfillment") or "unknown"),
            "survey_feedback": str(result.get("survey_feedback") or "none"),
        }
        # Rule overlays for obvious high-signal patterns to reduce model misses in production.
        if UNSUB_RE.search(text):
            labels["safety"] = "unsubscribe"
            labels["intent"] = "unsubscribe"
        elif LEGAL_RE.search(text):
            labels["safety"] = "legal_or_complaint"
        elif labels["safety"] in {"unsubscribe", "legal_or_complaint"}:
            # Prevent model false positives from auto-suppressing without explicit lexical evidence.
            labels["safety"] = "clear"
        if BOUNCE_RE.search(text):
            labels["bounce_system"] = "hard_bounce"
        elif OOO_RE.search(text):
            labels["bounce_system"] = "out_of_office"
        if PAYMENT_RE.search(text):
            labels["payment"] = "payment_related"
        if POSITIVE_RE.search(text) and labels["intent"] not in {"unsubscribe", "not_interested"}:
            labels["intent"] = "positive_interest"
        elif NEGATIVE_RE.search(text) and labels["intent"] != "unsubscribe":
            labels["intent"] = "not_interested"
        # Enforce unsubscribe precedence even if model misses consistency.
        if labels["safety"] == "unsubscribe":
            labels["intent"] = "unsubscribe"
        stages = [
            ClassificationResult(stage=stage, label=label, confidence=conf, raw={"provider": "ollama"})
            for stage, label in labels.items()
        ]
        return ClassificationBundle(stages=stages)

    def classify(self, body: str, subject: str | None = None) -> ClassificationBundle:
        text = f"{subject or ''}\n{body}".strip()
        ollama_bundle = self._ollama_classify(text)
        if ollama_bundle is not None:
            return ollama_bundle
        return self._rule_classify(text)
