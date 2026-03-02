from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ProspectFeatures:
    entity_detail_id: int
    email: str
    business_name: str
    contact_name_raw: str | None
    contact_name_normalized: str | None
    first_name_for_greeting: str
    website: str | None
    phone: str | None
    state: str | None
    city: str | None
    zipcode: str | None
    naics_primary: str | None
    naics_all_codes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    capabilities_narrative: str | None = None
    certs: list[str] = field(default_factory=list)
    self_small_boolean: bool | None = None
    self_cert_flags: dict[str, bool] = field(default_factory=dict)
    uei: str | None = None
    cage_code: str | None = None
    year_established: int | None = None
    display_email: bool = False
    public_display: bool = False
    public_display_limited: bool = False
    raw_json: dict[str, Any] = field(default_factory=dict)
    source_row: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Offer:
    offer_key: str
    offer_type: str
    price_cents: int
    fulfillment_workflow_key: str
    targeting_rules: dict[str, Any]
    sales_constraints: dict[str, Any]
    active: bool = True


@dataclass(slots=True)
class OfferVariant:
    variant_key: str
    offer_key: str
    subject_template: str
    body_template: str
    style_tags: list[str]
    status: str = "active"


@dataclass(slots=True)
class CandidateScore:
    total: float
    reasons: list[str]
    components: dict[str, float]


@dataclass(slots=True)
class ProspectSelection:
    features: ProspectFeatures
    offer_key: str
    variant_key: str
    score: CandidateScore


@dataclass(slots=True)
class ClassificationResult:
    stage: str
    label: str
    confidence: float
    raw: dict[str, Any]


@dataclass(slots=True)
class ClassificationBundle:
    stages: list[ClassificationResult]

    def label_for(self, stage: str) -> str | None:
        for item in self.stages:
            if item.stage == stage:
                return item.label
        return None


@dataclass(slots=True)
class AgentAction:
    action: str
    reason: str
    reply_subject: str | None = None
    reply_body: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunSummary:
    run_id: str
    slot: str
    started_at: datetime
    finished_at: datetime | None
    metrics: dict[str, Any]
    decisions: dict[str, Any]
