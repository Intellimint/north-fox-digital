from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .config import DEFAULT_ORIGIN, DEFAULT_REFERER, RETRYABLE_STATUS_CODES
from .states import state_payload_value

if TYPE_CHECKING:  # pragma: no cover
    import httpx

logger = logging.getLogger(__name__)


def build_search_payload(state_code: str) -> dict[str, Any]:
    return {
        "searchProfiles": {"searchTerm": ""},
        "location": {
            "states": [{"value": state_payload_value(state_code)}],
            "zipCodes": [],
            "counties": [],
            "districts": [],
            "msas": [],
        },
        "sbaCertifications": {"activeCerts": [], "isPreviousCert": False, "operatorType": "Or"},
        "naics": {"codes": [], "isPrimary": False, "operatorType": "Or"},
        "selfCertifications": {"certifications": [], "operatorType": "Or"},
        "keywords": {"list": [], "operatorType": "Or"},
        "lastUpdated": {"date": {"label": "Anytime", "value": "anytime"}},
        "samStatus": {"isActiveSAM": False},
        "qualityAssuranceStandards": {"qas": []},
        "bondingLevels": {
            "constructionIndividual": "",
            "constructionAggregate": "",
            "serviceIndividual": "",
            "serviceAggregate": "",
        },
        "businessSize": {"relationOperator": "at-least", "numberOfEmployees": ""},
        "annualRevenue": {"relationOperator": "at-least", "annualGrossRevenue": ""},
        "entityDetailId": "",
    }


@dataclass(slots=True)
class FetchResult:
    state_code: str
    raw_gz_path: Path
    meta_path: Path
    http_status: int
    content_length: int
    etag: str | None
    sha256_uncompressed: str
    pulled_at: str


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delta)
        except Exception:
            return None


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Origin": DEFAULT_ORIGIN,
        "Referer": DEFAULT_REFERER,
        "Accept-Encoding": "gzip, br",
        "User-Agent": user_agent,
    }


def fetch_state_to_cache(
    client: "httpx.Client",
    state_code: str,
    out_dir: Path,
    endpoint: str,
    timeout: float,
    user_agent: str,
    max_retries: int,
) -> FetchResult:
    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{state_code}.json.gz"
    meta_path = out_dir / f"{state_code}.meta.json"
    payload = build_search_payload(state_code)

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            logger.info("Fetching %s (attempt %s)", state_code, attempt + 1)
            with client.stream(
                "POST",
                endpoint,
                json=payload,
                headers=_headers(user_agent),
                timeout=timeout,
            ) as resp:
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                    if attempt >= max_retries:
                        resp.raise_for_status()
                    wait_for = retry_after if retry_after is not None else min(60.0, 2**attempt)
                    logger.warning(
                        "Retryable status %s for %s; sleeping %.1fs",
                        resp.status_code,
                        state_code,
                        wait_for,
                    )
                    time.sleep(wait_for)
                    continue
                resp.raise_for_status()
                sha256 = hashlib.sha256()
                bytes_written = 0
                with gzip.open(raw_path, "wb") as gz:
                    for chunk in resp.iter_bytes():
                        if not chunk:
                            continue
                        gz.write(chunk)
                        sha256.update(chunk)
                        bytes_written += len(chunk)

                pulled_at = datetime.now(timezone.utc).isoformat()
                return FetchResult(
                    state_code=state_code,
                    raw_gz_path=raw_path,
                    meta_path=meta_path,
                    http_status=resp.status_code,
                    content_length=bytes_written,
                    etag=resp.headers.get("ETag"),
                    sha256_uncompressed=sha256.hexdigest(),
                    pulled_at=pulled_at,
                )
        except (httpx.HTTPError, httpx.NetworkError, OSError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            wait_for = min(60.0, 2**attempt)
            logger.warning("Fetch error for %s: %s; sleeping %.1fs", state_code, exc, wait_for)
            time.sleep(wait_for)

    raise RuntimeError(f"Failed to fetch {state_code} after {max_retries + 1} attempts") from last_error


def write_meta_file(meta_path: Path, metadata: dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
