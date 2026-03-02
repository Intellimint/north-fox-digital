from __future__ import annotations

import json
import re
from email.utils import parseaddr
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from .models import ProspectFeatures

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GENERIC_CONTACT_NAMES = {"owner", "admin", "sales", "info", "office", "contact"}
SUFFIXES = {"LLC", "INC", "LTD", "CO", "CORP", "III", "II", "IV", "LP", "LLP", "PC"}


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    _, parsed = parseaddr(email)
    candidate = parsed or email
    value = candidate.strip()
    if not value:
        return None
    return value.lower()


def is_valid_email(email: str | None) -> bool:
    return bool(email and EMAIL_RE.match(email.strip()))


def normalize_website(website: str | None) -> str | None:
    if not website:
        return None
    value = website.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    return host


def _smart_title_token(token: str) -> str:
    if not token:
        return token
    cleaned = token.strip()
    upper = cleaned.upper()
    if upper in SUFFIXES:
        return upper
    if len(cleaned) <= 2 and cleaned.isalpha() and cleaned.isupper():
        return cleaned
    if "'" in cleaned:
        return "'".join(part.capitalize() for part in cleaned.split("'"))
    if "-" in cleaned:
        return "-".join(part.capitalize() for part in cleaned.split("-"))
    return cleaned.capitalize()


def normalize_person_name(name: str | None) -> str | None:
    if not name:
        return None
    value = " ".join(name.strip().split())
    if not value:
        return None
    tokens = [_smart_title_token(t) for t in value.split(" ")]
    return " ".join(tokens)


def normalize_business_name(name: str | None) -> str | None:
    if not name:
        return None
    value = " ".join(name.strip().split())
    if not value:
        return None
    # Normalize ALL CAPS company names and preserve common suffixes with punctuation.
    tokens: list[str] = []
    for raw in value.split(" "):
        token = raw.strip()
        if not token:
            continue
        trail = ""
        while token and token[-1] in ",.":
            trail = token[-1] + trail
            token = token[:-1]
        normalized = _smart_title_token(token)
        if normalized.upper() in SUFFIXES:
            normalized = normalized.upper().capitalize() if normalized.upper() == "CO" else normalized.upper().capitalize()
            if normalized.upper() in {"LLC", "INC", "LTD", "CORP", "LP", "LLP", "PC"}:
                normalized = normalized.title()
        tokens.append(normalized + trail)
    out = " ".join(tokens)
    return out or value


def greeting_name(contact_name: str | None, business_name: str | None) -> str:
    normalized_contact = normalize_person_name(contact_name)
    if normalized_contact:
        first = normalized_contact.split()[0]
        if first and first.lower() not in GENERIC_CONTACT_NAMES:
            return first
        return normalized_contact
    if business_name:
        cleaned = business_name.strip()
        if cleaned:
            return normalize_person_name(cleaned) or cleaned
    return "there"


def _to_list_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return [part.strip() for part in s.split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _extract_cert_names(raw_certs: Any, tags: Any) -> list[str]:
    cert_names: list[str] = []
    for source in (raw_certs, tags):
        if source is None:
            continue
        if isinstance(source, str):
            try:
                parsed = json.loads(source)
            except json.JSONDecodeError:
                parsed = source
        else:
            parsed = source
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    if item.strip():
                        cert_names.append(item.strip())
                elif isinstance(item, dict):
                    name = str(item.get("name") or item.get("label") or "").strip()
                    if name:
                        cert_names.append(name)
    seen: set[str] = set()
    deduped: list[str] = []
    for item in cert_names:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _parse_raw_json(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str):
        raw_value = raw_value.strip()
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def features_from_sbs_row(row: dict[str, Any]) -> ProspectFeatures:
    raw = _parse_raw_json(row.get("raw"))
    contact_raw = row.get("contact_person") or raw.get("contact_person")
    business_name = str(
        row.get("legal_business_name")
        or row.get("dba_name")
        or raw.get("legal_business_name")
        or raw.get("dba_name")
        or ""
    ).strip()

    keywords = _to_list_str(raw.get("keywords") or row.get("keywords"))
    naics_all = _to_list_str(raw.get("naics_all_codes"))
    certs = _extract_cert_names(row.get("certs"), row.get("tags") or raw.get("meili_self_certifications"))
    desc = row.get("description") or raw.get("capabilities_narrative") or raw.get("capabilitiesNarrative")

    self_cert_flags = {
        k: bool(raw.get(k))
        for k in [
            "self_wosb_boolean",
            "self_wosb_jv_boolean",
            "self_edwosb_boolean",
            "self_sdb_boolean",
            "self_vosb_boolean",
            "self_sdvosb_boolean",
            "self_minority_owned_boolean",
            "self_hubzone_boolean",
        ]
        if k in raw
    }
    year_established = raw.get("year_established") or raw.get("yearEstablished")
    try:
        year_established = int(year_established) if year_established not in (None, "") else None
    except (TypeError, ValueError):
        year_established = None

    email = normalize_email(str(row.get("email") or raw.get("email") or ""))
    if not email:
        email = ""
    normalized_business_name = normalize_business_name(business_name) or business_name
    return ProspectFeatures(
        entity_detail_id=int(row["entity_detail_id"]),
        email=email,
        business_name=normalized_business_name,
        contact_name_raw=str(contact_raw).strip() if contact_raw else None,
        contact_name_normalized=normalize_person_name(str(contact_raw)) if contact_raw else None,
        first_name_for_greeting=greeting_name(str(contact_raw) if contact_raw else None, normalized_business_name),
        website=row.get("website") or raw.get("website"),
        phone=row.get("phone") or raw.get("phone"),
        state=row.get("state") or raw.get("state"),
        city=row.get("city") or raw.get("city"),
        zipcode=row.get("zipcode") or raw.get("zipcode"),
        naics_primary=row.get("naics_primary") or raw.get("naics_primary"),
        naics_all_codes=naics_all,
        keywords=keywords,
        capabilities_narrative=str(desc).strip() if desc else None,
        certs=certs,
        self_small_boolean=(bool(raw.get("self_small_boolean")) if raw.get("self_small_boolean") is not None else None),
        self_cert_flags=self_cert_flags,
        uei=row.get("uei") or raw.get("uei"),
        cage_code=row.get("cage_code") or raw.get("cage_code"),
        year_established=year_established,
        display_email=bool(row.get("display_email")),
        public_display=bool(row.get("public_display")),
        public_display_limited=bool(row.get("public_display_limited")),
        raw_json=raw,
        source_row=row,
    )


def prospect_snapshot(features: ProspectFeatures) -> dict[str, Any]:
    data = asdict(features)
    # Avoid storing full source row/raw duplicates in the light snapshot.
    data.pop("source_row", None)
    return data
