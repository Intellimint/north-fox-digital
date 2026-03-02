from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WebsiteEvidence:
    page_url: str
    screenshot_path: str | None = None
    snippet: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScanFinding:
    category: str
    severity: str
    title: str
    description: str
    remediation: str
    evidence: WebsiteEvidence
    confidence: float


@dataclass(slots=True)
class ReportSection:
    key: str
    title: str
    body_markdown: str


@dataclass(slots=True)
class ReportScore:
    value_score: float
    accuracy_score: float
    aesthetic_score: float
    pass_gate: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SalesSimulationScenario:
    scenario_key: str
    persona: str
    turns: list[dict[str, str]]
    score_close: float
    score_trust: float
    score_objection: float


@dataclass(slots=True)
class StrategyUpdate:
    version: int
    memory: dict[str, Any]


@dataclass(slots=True)
class IterationResult:
    iteration_id: str
    entity_detail_id: int
    business_name: str
    website: str
    status: str
    findings: list[ScanFinding]
    report_json_path: str
    report_html_path: str
    report_pdf_path: str
    score: ReportScore
    sales_scenarios: list[SalesSimulationScenario]
    report_word_count: int = 0
    report_depth_level: int = 1
    sales_avg_close: float = 0.0
    sales_avg_trust: float = 0.0
    sales_avg_objection: float = 0.0
    roi_base_monthly_upside: int = 0
    roi_base_payback_days: int = 0
    report_attempt_count: int = 1


_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_ALLOWED_CATEGORIES = {"security", "email_auth", "seo", "ada", "conversion", "performance", "context"}
_REQUIRED_REPORT_SECTION_KEYS = [
    "executive_summary",
    "risk_dashboard",
    "security",
    "email_auth",
    "ada",
    "seo",
    "conversion",
    "performance",
    "competitor_context",
    "roadmap",
    "appendix",
]


def validate_finding(finding: ScanFinding) -> None:
    if finding.category not in _ALLOWED_CATEGORIES:
        raise ValueError(f"invalid_category:{finding.category}")
    if finding.severity not in _ALLOWED_SEVERITIES:
        raise ValueError(f"invalid_severity:{finding.severity}")
    if not (0.0 <= float(finding.confidence) <= 1.0):
        raise ValueError("invalid_confidence")
    if not finding.title.strip():
        raise ValueError("missing_title")
    if not finding.remediation.strip():
        raise ValueError("missing_remediation")


def validate_report_score(score: ReportScore) -> None:
    for key, value in {
        "value_score": score.value_score,
        "accuracy_score": score.accuracy_score,
        "aesthetic_score": score.aesthetic_score,
    }.items():
        v = float(value)
        if v < 0 or v > 100:
            raise ValueError(f"invalid_{key}")


def required_report_section_keys() -> list[str]:
    return list(_REQUIRED_REPORT_SECTION_KEYS)


def validate_sections_payload(payload: Any, *, expected_keys: list[str]) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        raise ValueError("sections_payload_not_object")
    sections = payload.get("sections")
    if not isinstance(sections, list):
        raise ValueError("sections_payload_missing_sections")

    normalized_by_key: dict[str, dict[str, str]] = {}
    for raw in sections:
        if not isinstance(raw, dict):
            raise ValueError("sections_payload_invalid_item")
        key = str(raw.get("key") or "").strip()
        title = str(raw.get("title") or "").strip()
        body = str(raw.get("body") or "").strip()
        if not key or not title or not body:
            raise ValueError(f"sections_payload_missing_fields:{key or 'unknown'}")
        if len(body) < 40:
            raise ValueError(f"sections_payload_body_too_short:{key}")
        normalized_by_key[key] = {"key": key, "title": title, "body": body}

    expected = set(expected_keys)
    found = set(normalized_by_key.keys())
    missing = expected - found
    if missing:
        raise ValueError(f"sections_payload_missing_keys:{','.join(sorted(missing))}")
    extras = found - expected
    if extras:
        raise ValueError(f"sections_payload_unexpected_keys:{','.join(sorted(extras))}")
    return [normalized_by_key[k] for k in expected_keys]


def validate_sales_reply_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("sales_reply_payload_not_object")
    reply = str(payload.get("reply") or "").strip()
    if not reply:
        raise ValueError("sales_reply_missing")
    if len(reply) > 700:
        raise ValueError("sales_reply_too_long")
    if "call" in reply.lower() or "zoom" in reply.lower():
        raise ValueError("sales_reply_channel_violation")
    return reply
