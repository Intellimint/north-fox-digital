from __future__ import annotations

from .models import CandidateScore, Offer, ProspectFeatures


def score_for_offer(features: ProspectFeatures, offer: Offer) -> CandidateScore:
    total = 0.0
    reasons: list[str] = []
    components: dict[str, float] = {}

    if features.self_small_boolean is True:
        components["small_business"] = 2.0
        reasons.append("self_small_boolean")
    elif offer.targeting_rules.get("require_small_business"):
        components["small_business"] = -10.0
        reasons.append("not_small_business")
    else:
        components["small_business"] = 0.0

    if features.display_email and features.public_display:
        components["public_email"] = 2.0
        reasons.append("public_display_email")
    else:
        components["public_email"] = -10.0

    if offer.offer_type == "DSBS_REWRITE":
        if features.capabilities_narrative:
            components["has_narrative"] = 1.2
            reasons.append("has_narrative")
        if features.keywords:
            components["has_keywords"] = 1.0
            reasons.append("has_keywords")
        if features.naics_primary:
            components["naics"] = 0.8
            reasons.append("has_naics")
    elif offer.offer_type == "CAPABILITY_STATEMENT":
        if features.uei:
            components["uei"] = 1.1
            reasons.append("has_uei")
        if features.cage_code:
            components["cage"] = 1.1
            reasons.append("has_cage")
        if features.naics_primary or features.naics_all_codes:
            components["naics"] = 1.0
            reasons.append("has_naics")
        if features.website:
            components["website"] = 0.4
            reasons.append("has_website")
    elif offer.offer_type == "WEB_PRESENCE_REPORT":
        if features.website:
            components["website"] = 1.6
            reasons.append("has_website")
        else:
            components["website"] = -4.0
            reasons.append("missing_website")
        if features.public_display:
            components["public_display"] = 0.6
            reasons.append("public_display")
        if features.keywords:
            components["keyword_surface"] = 0.4
            reasons.append("has_keywords")
        if features.naics_primary:
            components["naics"] = 0.3
            reasons.append("has_naics")

    if features.certs:
        components["certs"] = min(len(features.certs), 3) * 0.2
        reasons.append("has_certs")

    if features.contact_name_normalized:
        components["contact_name"] = 0.3
        reasons.append("has_contact")

    total = round(sum(components.values()), 4)
    return CandidateScore(total=total, reasons=reasons, components=components)
