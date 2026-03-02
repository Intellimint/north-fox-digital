from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import AgentSettings
from ..features import features_from_sbs_row
from ..source_sbs import SourceProspectRepository
from .scan_pipeline import run_scan_pipeline

_NOINDEX_RE = re.compile(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex', re.IGNORECASE)


def _parse_parenthetical_list(title: str) -> list[str]:
    m = re.search(r"\(([^\)]+)\)", title)
    if not m:
        return []
    return [x.strip().upper() for x in m.group(1).split(",") if x.strip()]


def _verify_noindex_claim(url: str) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            return False, f"http_{resp.status_code}"
        has_noindex = bool(_NOINDEX_RE.search(resp.text or ""))
        return has_noindex, "ok"
    except Exception as exc:  # pragma: no cover
        return False, f"fetch_error:{exc}"


def _audit_scan_result(scan: dict[str, Any]) -> dict[str, Any]:
    findings = list(scan.get("findings") or [])
    dns = dict(scan.get("dns_auth") or {})
    discrepancies: list[dict[str, Any]] = []

    missing_auth = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns.get(k) or "") == "missing"]
    unknown_auth = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns.get(k) or "") == "unknown"]

    for idx, f in enumerate(findings):
        title = str(getattr(f, "title", "") or "")
        title_l = title.lower()
        ev = getattr(f, "evidence", None)
        metadata = dict(getattr(ev, "metadata", {}) or {})

        if "email authentication gaps detected" in title_l:
            claimed = sorted(_parse_parenthetical_list(title))
            expected = sorted(missing_auth)
            if claimed != expected or not expected:
                discrepancies.append(
                    {
                        "index": idx,
                        "title": title,
                        "reason": "email_auth_missing_mismatch",
                        "claimed": claimed,
                        "expected": expected,
                    }
                )

        if "email authentication could not be fully verified" in title_l:
            claimed = sorted(_parse_parenthetical_list(title))
            expected = sorted(unknown_auth)
            if claimed != expected or not expected:
                discrepancies.append(
                    {
                        "index": idx,
                        "title": title,
                        "reason": "email_auth_unknown_mismatch",
                        "claimed": claimed,
                        "expected": expected,
                    }
                )

        if "dmarc policy set to none" in title_l:
            if dns.get("dmarc") != "present" or str(dns.get("dmarc_policy") or "").lower() != "none":
                discrepancies.append(
                    {
                        "index": idx,
                        "title": title,
                        "reason": "dmarc_policy_claim_mismatch",
                        "dns": dns,
                    }
                )

        if "sensitive path(s) publicly accessible" in title_l:
            exposed = list(metadata.get("exposed_paths") or [])
            bad = [p for p in exposed if int((p or {}).get("status_code") or 0) != 200]
            if not exposed or bad:
                discrepancies.append(
                    {
                        "index": idx,
                        "title": title,
                        "reason": "sensitive_path_claim_mismatch",
                        "exposed_paths": exposed,
                    }
                )

        if "noindex" in title_l:
            page_url = str(getattr(ev, "page_url", "") or "")
            if page_url:
                ok, detail = _verify_noindex_claim(page_url)
                if not ok:
                    discrepancies.append(
                        {
                            "index": idx,
                            "title": title,
                            "reason": "noindex_claim_not_reproduced",
                            "page_url": page_url,
                            "detail": detail,
                        }
                    )

    return {
        "findings_count": len(findings),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
    }


def run_accuracy_audit(
    *,
    settings: AgentSettings,
    sample_size: int = 8,
    deep_count: int = 3,
) -> dict[str, Any]:
    src = SourceProspectRepository(settings.sbs_db_path)

    selected: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for batch in src.iter_candidates(batch_size=300):
        for row in batch:
            feat = features_from_sbs_row(row)
            if not feat.website:
                continue
            website = str(feat.website).strip()
            if not website:
                continue
            domain = website.lower().replace("https://", "").replace("http://", "").split("/")[0]
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            selected.append(
                {
                    "entity_id": int(feat.entity_detail_id),
                    "business_name": str(feat.business_name or ""),
                    "website": website,
                }
            )
            if len(selected) >= max(1, int(sample_size)):
                break
        if len(selected) >= max(1, int(sample_size)):
            break

    audit_rows: list[dict[str, Any]] = []
    for i, item in enumerate(selected):
        for mode in (["light", "deep"] if i < int(deep_count) else ["light"]):
            out_dir = settings.logs_dir / "accuracy_audits" / time.strftime("%Y-%m-%d") / f"entity_{item['entity_id']}_{mode}"
            t0 = time.monotonic()
            scan = run_scan_pipeline(settings=settings, website=item["website"], out_dir=out_dir, mode=mode)
            elapsed = round(time.monotonic() - t0, 2)
            audited = _audit_scan_result(scan)
            audit_rows.append(
                {
                    "entity_id": item["entity_id"],
                    "business_name": item["business_name"],
                    "website": item["website"],
                    "mode": mode,
                    "elapsed_seconds": elapsed,
                    **audited,
                }
            )

    total_scans = len(audit_rows)
    scans_with_discrepancies = sum(1 for r in audit_rows if int(r.get("discrepancy_count") or 0) > 0)
    discrepancy_total = sum(int(r.get("discrepancy_count") or 0) for r in audit_rows)

    result = {
        "ok": True,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sample_size": len(selected),
        "deep_count": int(deep_count),
        "total_scans": total_scans,
        "scans_with_discrepancies": scans_with_discrepancies,
        "discrepancy_total": discrepancy_total,
        "rows": audit_rows,
    }

    out_root = settings.logs_dir / "accuracy_audits" / time.strftime("%Y-%m-%d")
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    out_path = out_root / f"accuracy_audit_{stamp}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["artifact_path"] = str(out_path)
    return result
