from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..features import features_from_sbs_row, is_valid_email
from ..source_sbs import SourceProspectRepository
from .strategy_memory import ResearchDB


@dataclass(slots=True)
class SampledBusiness:
    entity_detail_id: int
    business_name: str
    website: str
    contact_name: str
    email: str


def iter_valid_businesses(source_repo: SourceProspectRepository, *, batch_size: int = 500) -> Iterable[SampledBusiness]:
    for batch in source_repo.iter_candidates(batch_size=batch_size):
        for row in batch:
            f = features_from_sbs_row(row)
            if not f.public_display:
                continue
            if not f.website:
                continue
            if not is_valid_email(f.email):
                continue
            website = str(f.website).strip()
            if not website:
                continue
            yield SampledBusiness(
                entity_detail_id=f.entity_detail_id,
                business_name=f.business_name,
                website=website,
                contact_name=f.contact_name_normalized or f.first_name_for_greeting,
                email=f.email,
            )


def pick_next_business(
    source_repo: SourceProspectRepository,
    research_db: ResearchDB,
    *,
    excluded_ids: set[int] | None = None,
) -> SampledBusiness:
    excluded: set[int] = {int(x) for x in (excluded_ids or set())}
    used = research_db.used_business_ids(limit=10000)
    recent: set[int] = set()
    try:
        recent = set(research_db.recent_business_ids(limit=32))
    except Exception:
        recent = set()
    rotation_state: dict[int, tuple[int, str]] = {}
    try:
        rotation_state = dict(research_db.business_rotation_state())
    except Exception:
        rotation_state = {}
    if rotation_state:
        used = set(rotation_state.keys())
    if excluded:
        used = set(used) | excluded

    first: SampledBusiness | None = None
    unseen_fallback: SampledBusiness | None = None
    lru_reuse: SampledBusiness | None = None
    lru_rank: tuple[int, int, str, int] | None = None
    for item in iter_valid_businesses(source_repo):
        if first is None:
            first = item
        entity_id = int(item.entity_detail_id)
        if entity_id in excluded:
            continue
        if entity_id not in used and entity_id not in recent:
            return item
        if entity_id not in used and unseen_fallback is None:
            unseen_fallback = item

        run_count, last_used_at = rotation_state.get(entity_id, (0, ""))
        rank = (
            1 if entity_id in recent else 0,  # Prefer entities outside recent-window first.
            int(run_count),
            str(last_used_at),
            entity_id,
        )
        if lru_rank is None or rank < lru_rank:
            lru_rank = rank
            lru_reuse = item

    if unseen_fallback is not None:
        return unseen_fallback
    if lru_reuse is not None:
        return lru_reuse
    if first is not None:
        return first
    raise RuntimeError("no_eligible_business_for_rnd")
