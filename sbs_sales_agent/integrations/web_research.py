from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ResearchResult:
    pain_points: list[str]
    evidence_links: list[str]
    offer_angle_candidates: list[str]


class WebResearchClient:
    def research_segment(self, segment: str) -> ResearchResult:
        # v1 bounded stub; hook web search here later.
        return ResearchResult(
            pain_points=[f"Pain points research pending for segment: {segment}"],
            evidence_links=[],
            offer_angle_candidates=["Lead with fast, fixed-price deliverable and clear turnaround"],
        )
