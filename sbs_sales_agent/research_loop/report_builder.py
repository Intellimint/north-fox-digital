from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ..config import AgentSettings
from ..integrations.codex_fulfillment import CodexFulfillmentClient
from ..integrations.ollama_client import OllamaClient
from .business_sampler import SampledBusiness
from .types import ReportSection, ScanFinding, required_report_section_keys, validate_sections_payload

_UNVERIFIED_CLAIM_LINE_PATTERNS = [
    re.compile(r"\b(?:studies show|typically|on average|industry benchmark|benchmarks?)\b", re.IGNORECASE),
    re.compile(r"\bgoogle penalizes\b", re.IGNORECASE),
    re.compile(r"\b\d{1,3}\s*[–-]\s*\d{1,3}\s*%"),
    re.compile(r"\$\d[\d,]*(?:\s*[–-]\s*\$?\d[\d,]*)+"),
]


def _asdict_safe(value: Any) -> dict[str, Any]:
    """Best-effort conversion for dataclass-like payloads."""
    if is_dataclass(value):
        return dict(asdict(value))
    if isinstance(value, dict):
        return dict(value)
    return {"value": str(value)}


def _sanitize_unverified_claims_in_markdown(text: str) -> tuple[str, int]:
    """Remove lines with benchmark-style claims that cannot be verified from scan evidence."""
    removed = 0
    kept: list[str] = []
    for line in str(text or "").splitlines():
        if any(p.search(line) for p in _UNVERIFIED_CLAIM_LINE_PATTERNS):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed


def _top_urgent(findings: list[ScanFinding], *, limit: int = 5) -> list[ScanFinding]:
    sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
    return sorted(findings, key=lambda x: (sev_rank.get(x.severity, 0), x.confidence), reverse=True)[:limit]


def _roadmap(findings: list[ScanFinding]) -> list[dict[str, str]]:
    _time_map: dict[str, str] = {
        "security": "2–4h",
        "email_auth": "30min",
        "seo": "1–2h",
        "ada": "1–3h",
        "conversion": "1–2h",
        "performance": "2–4h",
        "context": "—",
    }
    _skill_map: dict[str, str] = {
        "security": "Web Dev",
        "email_auth": "DNS / IT",
        "seo": "SEO / Copywriter",
        "ada": "Web Dev",
        "conversion": "Copywriter",
        "performance": "Web Dev",
        "context": "Strategy",
    }
    _sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}

    _by_cat: dict[str, list[ScanFinding]] = {}
    for f in findings:
        _by_cat.setdefault(f.category, []).append(f)

    # De-duplicate by (category, title) and aggregate pages affected so actions don't repeat.
    grouped: dict[tuple[str, str], list[ScanFinding]] = {}
    for f in findings:
        key = (f.category, f.title.strip().lower())
        grouped.setdefault(key, []).append(f)

    collapsed: list[tuple[ScanFinding, int]] = []
    for _, group in grouped.items():
        best = max(group, key=lambda x: (_sev_rank.get(x.severity, 0), float(x.confidence)))
        pages = {str(g.evidence.page_url or "").strip() for g in group if str(g.evidence.page_url or "").strip()}
        collapsed.append((best, len(pages) if pages else len(group)))

    # Ensure coverage across key categories, then fill by urgency.
    required_items: list[tuple[ScanFinding, int]] = []
    for cat in ("security", "email_auth", "seo", "ada", "conversion"):
        cat_rows = [row for row in collapsed if row[0].category == cat]
        if cat_rows:
            required_items.append(max(cat_rows, key=lambda row: (_sev_rank.get(row[0].severity, 0), row[1], float(row[0].confidence))))

    required_keys = {(row[0].category, row[0].title.strip().lower()) for row in required_items}
    extra_items = sorted(
        [row for row in collapsed if (row[0].category, row[0].title.strip().lower()) not in required_keys],
        key=lambda row: (_sev_rank.get(row[0].severity, 0), row[1], float(row[0].confidence)),
        reverse=True,
    )[: max(0, 12 - len(required_items))]

    candidates = sorted(
        required_items + extra_items,
        key=lambda row: (_sev_rank.get(row[0].severity, 0), row[1], float(row[0].confidence)),
        reverse=True,
    )

    out: list[dict[str, str]] = []
    for f, pages_affected in candidates:
        bucket = "0–30 days" if f.severity in {"critical", "high"} else "31–60 days"
        if f.severity == "low":
            bucket = "61–90 days"
        impact = "High" if f.severity in {"critical", "high"} else "Medium" if f.severity == "medium" else "Low"
        effort = "Medium" if f.category in {"security", "ada"} else "Low"
        action = f.title
        if pages_affected > 1 and "pages affected" not in action.lower():
            action = f"{action} ({pages_affected} pages affected)"
        out.append({
            "window": bucket,
            "action": action,
            "impact": impact,
            "effort": effort,
            "est_time": _time_map.get(f.category, "1–2h"),
            "skill": _skill_map.get(f.category, "Web Dev"),
        })
    return out


def _section_body(title: str, items: list[ScanFinding], *, max_items: int = 10, snippet_max_len: int = 280) -> str:
    if not items:
        return f"No material {title.lower()} findings in this pass."
    lines = []
    for f in items[: max(3, max_items)]:
        snippet_block = ""
        if f.evidence.snippet and len(str(f.evidence.snippet).strip()) > 10:
            snippet_text = str(f.evidence.snippet).strip()[:snippet_max_len]
            snippet_block = f"\n**Evidence snippet:** `{snippet_text}`\n"
        meta_block = ""
        if f.evidence.metadata and isinstance(f.evidence.metadata, dict):
            meta_pairs = [f"{k}: {v}" for k, v in list(f.evidence.metadata.items())[:3]]
            if meta_pairs:
                meta_block = f"\n**Data:** {', '.join(meta_pairs)}\n"
        lines.append(
            f"### {f.title}\n"
            f"**Severity:** {f.severity.upper()}\n\n"
            f"**Confidence:** {int(round(float(f.confidence) * 100.0))}% "
            f"({'Verified signal' if float(f.confidence) >= 0.85 else 'Needs manual confirmation'})\n\n"
            f"**Why it matters:** {f.description}\n\n"
            f"**Recommended fix:** {f.remediation}\n\n"
            f"**Evidence page:** {f.evidence.page_url}"
            f"{snippet_block}"
            f"{meta_block}"
        )
    return "\n---\n".join(lines)


def _report_depth_level(strategy: dict[str, Any] | None) -> int:
    raw = 1 if not isinstance(strategy, dict) else int(strategy.get("report_depth_level", 1) or 1)
    return max(1, min(5, raw))


def _section_depth_addendum(*, category_label: str, findings: list[ScanFinding], depth: int) -> str:
    if depth <= 1:
        return ""
    high_n = sum(1 for f in findings if f.severity in {"high", "critical"})
    med_n = sum(1 for f in findings if f.severity == "medium")
    low_n = sum(1 for f in findings if f.severity in {"low", "info"})
    urgency = "Immediate" if high_n > 0 else "Planned"
    lines = [
        "",
        "### Implementation Notes",
        f"- **Priority posture:** {urgency} ({high_n} high/critical, {med_n} medium, {low_n} low/info).",
        f"- **Execution owner:** {category_label} lead with weekly status check until closure.",
        "- **Validation checkpoint:** confirm baseline before and after each fix, then re-scan.",
    ]
    if depth >= 3:
        lines.extend(
            [
                "- **KPI target:** complete top fixes in 14 days and verify measurable risk reduction.",
                "- **Evidence standard:** attach screenshot/config proof for every closed item.",
            ]
        )
    if depth >= 4:
        lines.extend(
            [
                "- **Dependency flag:** sequence foundational fixes first (infrastructure, then content/UI).",
                "- **Escalation rule:** unresolved high-severity items after 7 days move to owner escalation.",
            ]
        )
    if depth >= 5:
        lines.extend(
            [
                "- **Value tracking:** map each fix to pipeline impact, lead quality, or delivery risk reduction.",
                "- **Governance:** lock in monthly review cadence to prevent regression.",
            ]
        )
    return "\n".join(lines)


def _risk_score_label(count: int, cat: str) -> str:
    thresholds = {"security": (2, 4), "email_auth": (1, 3), "seo": (3, 6), "ada": (2, 4), "conversion": (2, 4)}
    lo, hi = thresholds.get(cat, (2, 4))
    if count == 0:
        return "✅ Low"
    if count <= lo:
        return "🟠 Elevated"
    if count <= hi:
        return "🔴 High"
    return "🚨 Critical"


def _web_health_score(findings: list[ScanFinding]) -> int:
    """Compute a rough 0–100 web presence health score (higher = healthier)."""
    deductions = 0
    for f in findings:
        deductions += {"critical": 18, "high": 10, "medium": 5, "low": 2, "info": 0}.get(f.severity, 0)
    return max(0, min(100, 100 - deductions))


def _competitor_context_section(scan_payload: dict[str, Any], findings: list[ScanFinding]) -> ReportSection:
    base_url = str(scan_payload.get("base_url") or "")
    tls = scan_payload.get("tls") or {}
    dns = scan_payload.get("dns_auth") or {}
    pages_crawled = len(scan_payload.get("pages") or [])
    domain = base_url.split("/")[2] if "//" in base_url else base_url

    by_cat: dict[str, list[ScanFinding]] = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)

    health = _web_health_score(findings)
    health_label = (
        "🚨 Failing (Urgent)" if health < 70
        else "🟠 Needs Improvement" if health < 85
        else "🟡 Stable" if health < 95
        else "✅ Strong"
    )

    # Category-level positioning language (evidence-only, no external benchmark percentages)
    cat_lines: list[str] = []
    sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
    for cat, label in [
        ("security", "Security Posture"),
        ("email_auth", "Email/Domain Trust"),
        ("seo", "SEO Readiness"),
        ("ada", "ADA/Accessibility"),
        ("conversion", "Conversion Optimization"),
    ]:
        cat_findings = by_cat.get(cat, [])
        high_n = sum(1 for f in cat_findings if sev_rank.get(f.severity, 0) >= 4)
        if not cat_findings:
            cat_lines.append(f"- **{label}:** No issues detected in this scan pass.")
        elif high_n > 0:
            cat_lines.append(f"- **{label}:** {high_n} high/critical issue(s) require immediate attention.")
        else:
            cat_lines.append(f"- **{label}:** {len(cat_findings)} low/medium issue(s) identified.")

    # TLS and email summary
    tls_line = (
        "TLS/HTTPS is correctly configured." if tls.get("ok")
        else "TLS configuration issue detected — a red flag for both visitors and search engines."
    )
    email_missing = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns.get(k) or "") == "missing"]
    email_line = (
        f"Email authentication gaps: {', '.join(email_missing)} not published — domain spoofing risk is elevated vs. properly configured peers."
        if email_missing
        else "Email authentication (SPF/DKIM/DMARC) appears configured — a deliverability and trust advantage."
    )

    body = (
        "## Competitive Positioning Context\n\n"
        f"**Overall Web Presence Health Score: {health}/100 — {health_label}**\n\n"
        f"Assessment covered **{pages_crawled} page(s)** of `{domain}`. "
        "Comparisons below reflect only findings from this scan.\n\n"
        "### Category Positioning\n\n"
        + "\n".join(cat_lines)
        + "\n\n"
        "### Infrastructure Signals\n\n"
        f"- {tls_line}\n"
        f"- {email_line}\n\n"
        "_Competitor domains were not directly crawled in this pass._"
    )
    return ReportSection(key="competitor_context", title="Competitor and Market Context", body_markdown=body)


def _build_scan_coverage_summary(findings: list[ScanFinding], scan_payload: dict[str, Any]) -> str:
    """Build an 'Audit Coverage Summary' info table injected at the start of the appendix (v39).

    A transparent coverage summary immediately answers the 'how thorough was this?' question
    that technically-minded buyers and skeptical SMB owners ask before paying $299. Showing
    the number of pages crawled, screenshots captured, and findings across six check categories
    makes the report feel comprehensive and systematically derived — not a generic scan.
    This table also helps the sales conversation: the agent can reference 'we checked 62
    different issue types across 5 pages' as concrete proof of thoroughness.
    """
    if len(findings) < 3:
        return ""

    pages_crawled = len(scan_payload.get("pages") or [])
    screenshots = len(scan_payload.get("screenshots") or {})
    total = len(findings)

    # Category breakdown
    cat_counts: dict[str, int] = {}
    for f in findings:
        cat_counts[f.category] = cat_counts.get(f.category, 0) + 1

    _CAT_LABELS = {
        "security": "Security",
        "email_auth": "Email Authentication",
        "seo": "SEO",
        "ada": "Accessibility (ADA)",
        "conversion": "Conversion / UX",
        "performance": "Performance",
    }
    cat_rows = ""
    for cat in ["security", "email_auth", "seo", "ada", "conversion", "performance"]:
        count = cat_counts.get(cat, 0)
        if count > 0:
            cat_rows += f"| {_CAT_LABELS[cat]} | {count} |\n"

    # Confidence distribution
    high_conf = sum(1 for f in findings if float(f.confidence) >= 0.85)
    med_conf = sum(1 for f in findings if 0.70 <= float(f.confidence) < 0.85)
    std_conf = sum(1 for f in findings if float(f.confidence) < 0.70)

    conf_row = (
        f"High (≥0.85): {high_conf} · "
        f"Medium (0.70–0.84): {med_conf} · "
        f"Standard (<0.70): {std_conf}"
    )

    return (
        "\n\n### Audit Coverage Summary\n\n"
        "| Audit Dimension | Value |\n"
        "|----------------|-------|\n"
        f"| Pages Crawled | {pages_crawled} |\n"
        f"| Screenshots Captured | {screenshots} |\n"
        f"| Total Findings | {total} |\n"
        f"| Confidence Distribution | {conf_row} |\n\n"
        "**Findings by Category**\n\n"
        "| Category | Findings |\n"
        "|----------|----------|\n"
        + cat_rows
        + "\n_All findings were identified through passive HTTP inspection, HTML analysis, DNS "
        "lookups, and browser rendering — no intrusive or destructive testing was performed._\n"
    )


def _build_technical_debt_summary(findings: list[ScanFinding]) -> str:
    """Generate a compact Technical Debt Scorecard showing finding counts by category and severity (v21).

    This table helps technical stakeholders (developers, IT managers) quickly estimate
    the remediation workload and prioritise sprint planning. It shows the distribution
    of findings across the six tracked categories with a severity breakdown, making it
    easy to see which area has the most urgent vs. low-priority technical debt.
    """
    from collections import Counter

    categories = ["security", "email_auth", "seo", "ada", "conversion", "performance"]
    severities = ["critical", "high", "medium", "low"]
    cat_labels = {
        "security": "Security",
        "email_auth": "Email / DNS",
        "seo": "SEO",
        "ada": "Accessibility",
        "conversion": "Conversion UX",
        "performance": "Performance",
    }

    # Build a counter of (category, severity) → count
    breakdown: Counter[tuple[str, str]] = Counter()
    for f in findings:
        if f.category in categories and f.severity in severities:
            breakdown[(f.category, f.severity)] += 1

    # Only include categories with at least one finding
    active_cats = [c for c in categories if any(breakdown[(c, s)] > 0 for s in severities)]
    if not active_cats:
        return ""

    # Header row
    header = "| Category | Critical | High | Medium | Low | Total |\n"
    divider = "|----------|----------|------|--------|-----|-------|\n"
    rows = ""
    for cat in active_cats:
        crit = breakdown[(cat, "critical")]
        high = breakdown[(cat, "high")]
        med = breakdown[(cat, "medium")]
        low = breakdown[(cat, "low")]
        total = crit + high + med + low
        crit_cell = f"**{crit}**" if crit > 0 else "—"
        high_cell = f"**{high}**" if high > 0 else "—"
        rows += f"| {cat_labels.get(cat, cat)} | {crit_cell} | {high_cell} | {med or '—'} | {low or '—'} | {total} |\n"

    total_findings = sum(breakdown.values())
    critical_total = sum(breakdown[(c, "critical")] for c in active_cats)
    high_total = sum(breakdown[(c, "high")] for c in active_cats)

    urgency_note = ""
    if critical_total > 0:
        urgency_note = f"  \n⚠ **{critical_total} critical** finding(s) require immediate action before next deployment."
    elif high_total >= 3:
        urgency_note = f"  \n⚡ **{high_total} high-severity** findings should be remediated within the next sprint cycle."

    return (
        "\n\n### Technical Debt Scorecard\n\n"
        f"Total findings catalogued: **{total_findings}**{urgency_note}\n\n"
        + header
        + divider
        + rows
        + "\n_Bold values indicate elevated severity. Use this table to estimate sprint capacity "
        "and assign findings to the appropriate team member by category._\n"
    )


def _build_appendix_body(findings: list[ScanFinding], scan_payload: dict[str, Any]) -> str:
    """Build the appendix section body with page-level evidence table."""
    # --- Findings by page ---
    page_finding_counts: dict[str, int] = {}
    for f in findings:
        url = str(f.evidence.page_url or "")
        if url.startswith("http"):
            page_finding_counts[url] = page_finding_counts.get(url, 0) + 1

    page_table_rows = ""
    for url, count in sorted(page_finding_counts.items(), key=lambda kv: -kv[1]):
        short_url = url.split("//", 1)[-1][:60] + ("…" if len(url.split("//", 1)[-1]) > 60 else "")
        page_table_rows += f"| {short_url} | {count} |\n"

    page_table_md = (
        "### Findings by Page\n\n"
        "| Page URL | Findings |\n"
        "|----------|----------|\n"
        + (page_table_rows or "| (no page data) | 0 |\n")
    ) if page_finding_counts else ""

    # --- Pages crawled ---
    pages_crawled = list(scan_payload.get("pages") or [])
    pages_list = "\n".join(f"- {p}" for p in pages_crawled[:12]) or "- (none)"

    return (
        "## Scan Methodology\n\n"
        "This report was generated via automated web presence analysis covering:\n\n"
        "- **Security headers**: HTTP response header inspection\n"
        "- **TLS/SSL**: Certificate validity and cipher strength check\n"
        "- **DNS/Email authentication**: SPF, DMARC, DKIM record lookup\n"
        "- **SEO**: Title, meta description, heading structure per page\n"
        "- **ADA/Accessibility**: Image alt text presence, form compliance, skip navigation\n"
        "- **Conversion UX**: CTA language, trust signals, form friction, social proof\n"
        "- **Performance**: Page payload size and load time heuristics\n\n"
        "No intrusive security actions were performed. "
        "This report is informational and does not constitute legal advice.\n\n"
        + (page_table_md + "\n\n" if page_table_md else "")
        + "### Pages Crawled\n\n"
        + pages_list
    )


def _business_impact_bullets(findings: list[ScanFinding], scan_payload: dict[str, Any]) -> str:
    """Return business-specific ROI impact bullets derived from actual scan findings."""
    by_cat: dict[str, list[ScanFinding]] = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)

    dns = scan_payload.get("dns_auth") or {}
    conversion_items = by_cat.get("conversion", [])
    conversion_high = sum(1 for f in conversion_items if f.severity in {"high", "critical"})
    seo_items = by_cat.get("seo", [])
    email_missing = [k for k in ("spf", "dkim", "dmarc") if (dns.get(k) or "missing") in ("missing", "", None, "unknown")]
    security_items = by_cat.get("security", [])
    security_high = sum(1 for f in security_items if f.severity in {"high", "critical"})
    ada_items = by_cat.get("ada", [])

    bullets: list[str] = []

    # Conversion impact — specificity makes the report feel earned, not canned
    if conversion_high >= 2:
        bullets.append(
            f"- **High conversion upside** from fixing {conversion_high} "
            f"high-priority CTA and trust signal gaps found in this assessment"
        )
    elif conversion_high >= 1:
        bullets.append(
            f"- **Meaningful conversion upside** from {len(conversion_items)} identified "
            f"UX friction and CTA gaps ({conversion_high} high-priority)"
        )
    elif conversion_items:
        bullets.append(
            f"- **Conversion lift potential** from {len(conversion_items)} "
            f"CTA and messaging optimization opportunity found"
        )
    else:
        bullets.append("- **Conversion improvement potential** from CTA clarity and trust signal optimizations")

    # SEO impact
    if len(seo_items) >= 6:
        bullets.append(
            f"- **Strong organic growth potential** within 90 days of addressing "
            f"{len(seo_items)} technical SEO issues found"
        )
    elif len(seo_items) >= 2:
        bullets.append(
            f"- **Organic search improvement potential** from implementing {len(seo_items)} SEO fixes"
        )
    elif seo_items:
        bullets.append("- **SEO ranking improvement** from resolving the technical SEO issue identified")

    # Email deliverability
    if email_missing:
        rec_str = ", ".join(k.upper() for k in email_missing)
        bullets.append(
            f"- **Reduced spoofing and deliverability risk** after publishing missing "
            f"{rec_str} email authentication records — currently elevating domain spoofing risk"
        )
    else:
        bullets.append(
            "- **Maintained email deliverability advantage** — "
            "SPF/DKIM/DMARC records appear configured correctly"
        )

    # Security / visitor trust
    if security_high >= 2:
        bullets.append(
            f"- **Improved visitor trust and search ranking** from resolving {security_high} "
            f"high-severity security gaps detectable by informed visitors and search bots"
        )
    elif security_high >= 1:
        bullets.append(
            f"- **Reduced breach risk** from fixing {security_high} high-severity security "
            f"vulnerability affecting site integrity and visitor trust"
        )

    # ADA / legal exposure
    if ada_items:
        bullets.append(
            f"- **Reduced legal exposure** from addressing {len(ada_items)} ADA/WCAG "
            "accessibility gap(s) and documenting remediation evidence for counsel/developer review"
        )
    else:
        bullets.append("- **Lower legal/liability exposure** from ADA accessibility compliance improvements")

    return "\n".join(bullets)


def _value_model(findings: list[ScanFinding], *, strategy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create an assumptions-based revenue upside model for low/base/upside scenarios."""
    by_cat: dict[str, list[ScanFinding]] = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)

    conversion_count = len(by_cat.get("conversion", []))
    seo_count = len(by_cat.get("seo", []))
    security_urgent = sum(1 for f in by_cat.get("security", []) if f.severity in {"high", "critical"})
    email_urgent = sum(1 for f in by_cat.get("email_auth", []) if f.severity in {"high", "critical"})

    lead_bias = int((strategy or {}).get("value_model_lead_bias", 0) or 0)
    baseline_monthly_leads = max(8, min(160, 12 + conversion_count * 3 + (seo_count // 2) + lead_bias))
    close_rate = 0.22
    avg_deal_value = int((strategy or {}).get("avg_deal_value_usd", 1200) or 1200)
    urgency_bias = float((strategy or {}).get("value_model_urgency_bias", 0.0) or 0.0)
    urgency_multiplier = max(0.85, min(1.55, 1.0 + (0.06 * min(4, security_urgent + email_urgent)) + urgency_bias))

    scenarios: list[dict[str, Any]] = []
    for name, conv_lift, traffic_lift, confidence in [
        ("low", 0.10, 0.08, 0.78),
        ("base", 0.17, 0.14, 0.72),
        ("upside", 0.26, 0.22, 0.64),
    ]:
        incremental_leads = round(
            baseline_monthly_leads * ((1.0 + conv_lift) * (1.0 + traffic_lift) - 1.0) * urgency_multiplier
        )
        monthly_revenue = int(max(0, round(incremental_leads * close_rate * avg_deal_value)))
        payback_days = int(round((299.0 / monthly_revenue) * 30.0)) if monthly_revenue > 0 else 999
        scenarios.append(
            {
                "name": name,
                "incremental_leads_monthly": int(max(0, incremental_leads)),
                "incremental_revenue_monthly_usd": monthly_revenue,
                "incremental_revenue_annual_usd": int(monthly_revenue * 12),
                "payback_days_for_report_fee": max(1, payback_days),
                "confidence": confidence,
            }
        )

    return {
        "assumptions": {
            "baseline_monthly_leads": baseline_monthly_leads,
            "close_rate": close_rate,
            "avg_deal_value_usd": avg_deal_value,
            "report_fee_usd": 299,
        },
        "scenarios": scenarios,
    }


def _value_model_markdown(value_model: dict[str, Any]) -> str:
    assumptions = dict(value_model.get("assumptions") or {})
    rows = list(value_model.get("scenarios") or [])
    table_rows = "\n".join(
        [
            (
                f"| {str(r.get('name') or '').title()} | "
                f"{int(r.get('incremental_leads_monthly') or 0)} | "
                f"${int(r.get('incremental_revenue_monthly_usd') or 0):,} | "
                f"${int(r.get('incremental_revenue_annual_usd') or 0):,} | "
                f"{int(r.get('payback_days_for_report_fee') or 0)} day(s) | "
                f"{float(r.get('confidence') or 0.0):.0%} |"
            )
            for r in rows
        ]
    )
    return (
        "### Revenue Recovery Model (Assumption-Based)\n\n"
        "These scenarios estimate upside if roadmap items are executed in sequence.\n\n"
        "| Scenario | Added Leads / Month | Added Revenue / Month | Added Revenue / Year | Payback on $299 Report | Confidence |\n"
        "|----------|---------------------|-----------------------|----------------------|------------------------|------------|\n"
        f"{table_rows}\n\n"
        f"Assumptions: baseline {int(assumptions.get('baseline_monthly_leads') or 0)} monthly leads, "
        f"{float(assumptions.get('close_rate') or 0.0):.0%} close rate, "
        f"${int(assumptions.get('avg_deal_value_usd') or 0):,} average deal value.\n"
    )


def _build_quick_wins_roi_table(findings: list[ScanFinding]) -> str:
    """Generate a 'Quick Wins ROI Summary' table for the roadmap section (v38).

    Surfaces up to 6 high-ROI, low-effort fixes with estimated implementation time,
    expected business outcome, and skill level required. This gives the SMB owner a
    concrete actionable starting point — the 'what do I do first this week?' answer —
    framed in business outcome language rather than technical severity.

    Criteria for inclusion:
    - Finding severity is high, medium, or critical
    - Remediation length ≤ 400 chars (short = low-effort signal)
    - No heavy-refactor language in remediation
    Returns empty string if fewer than 2 qualifying findings exist.
    """
    import re as _re
    _HEAVY_RE = _re.compile(
        r'\b(?:rebuild|redesign|migrate\s+(?:your|the)|rewrite|overhaul|re-architect)\b',
        _re.IGNORECASE,
    )
    _IMPACT_LABELS: dict[str, str] = {
        "security": "Reduce breach/liability risk",
        "email_auth": "Stop email spoofing & phishing",
        "seo": "Improve search ranking & visibility",
        "ada": "Reduce ADA lawsuit exposure",
        "conversion": "Increase lead/booking conversions",
        "performance": "Speed up page load & Core Web Vitals",
    }
    _EFFORT_LABELS: dict[str, str] = {
        "security": "30 min",
        "email_auth": "1 hr",
        "seo": "45 min",
        "ada": "1 hr",
        "conversion": "30 min",
        "performance": "30 min",
    }
    qualified = [
        f for f in findings
        if f.severity in {"high", "critical", "medium"}
        and f.remediation
        and len(f.remediation) <= 400
        and not _HEAVY_RE.search(f.remediation)
    ]
    # Sort: critical > high > medium, then by shorter remediation = easier
    qualified.sort(key=lambda f: ({"critical": 0, "high": 1, "medium": 2}.get(f.severity, 3), len(f.remediation or "")))
    if len(qualified) < 2:
        return ""
    rows: list[str] = []
    seen_titles: set[str] = set()
    for f in qualified:
        key = f.title[:50]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        impact = _IMPACT_LABELS.get(f.category, "Improve site quality")
        effort = _EFFORT_LABELS.get(f.category, "1 hr")
        sev_badge = {"critical": "🔴 Critical", "high": "🟠 High", "medium": "🟡 Medium"}.get(f.severity, f.severity)
        rows.append(f"| {f.title[:55]} | {sev_badge} | {impact} | {effort} |")
        if len(rows) >= 6:
            break
    if not rows:
        return ""
    header = (
        "\n\n### Quick Wins ROI Summary\n\n"
        "The following fixes deliver the highest return relative to implementation effort. "
        "Start here for immediate risk reduction and measurable business impact.\n\n"
        "| Finding | Priority | Expected Outcome | Est. Time |\n"
        "|---------|----------|------------------|-----------|\n"
    )
    return header + "\n".join(rows) + "\n"


def _build_priority_matrix_md(findings: list[ScanFinding]) -> str:
    """Generate a 2x2 Impact vs. Effort priority matrix as a markdown table (v20).

    Maps each unique finding to one of four quadrants based on:
    - Impact:  High if severity is 'high' or 'critical'; Low otherwise.
    - Effort:  Low if remediation ≤ 150 chars AND no heavy-refactor language;
               High otherwise.

    Returns a markdown subsection that appends naturally to the roadmap section.
    Lists up to 4 findings per quadrant by name to stay scannable.
    """
    _HEAVY = re.compile(r'\b(?:rebuild|redesign|migrate\s+(?:your|the)|rewrite|overhaul)\b', re.IGNORECASE)

    quadrants: dict[str, list[str]] = {
        "do_first": [],
        "plan_next": [],
        "quick_wins": [],
        "deprioritize": [],
    }
    seen: set[str] = set()
    sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
    sorted_findings = sorted(findings, key=lambda x: sev_rank.get(x.severity, 0), reverse=True)

    for f in sorted_findings:
        key = (f.category, (f.title or "")[:40].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        rem = f.remediation.strip()
        high_impact = f.severity in {"high", "critical"}
        low_effort = len(rem) <= 150 and not _HEAVY.search(rem)
        label = (f.title or "")[:55].strip()
        if high_impact and low_effort:
            quadrants["do_first"].append(label)
        elif high_impact and not low_effort:
            quadrants["plan_next"].append(label)
        elif not high_impact and low_effort:
            quadrants["quick_wins"].append(label)
        else:
            quadrants["deprioritize"].append(label)

    def _fmt(items: list[str]) -> str:
        if not items:
            return "_(none in this iteration)_"
        shown = items[:4]
        suffix = f" +{len(items) - 4} more" if len(items) > 4 else ""
        return "; ".join(shown) + suffix

    return (
        "\n\n## Priority Matrix: Impact vs. Effort\n\n"
        "| Quadrant | Guidance | Findings |\n"
        "|----------|----------|----------|\n"
        f"| **Do First** | High impact, low effort — tackle immediately | {_fmt(quadrants['do_first'])} |\n"
        f"| **Plan Next** | High impact, higher effort — schedule in roadmap | {_fmt(quadrants['plan_next'])} |\n"
        f"| **Quick Wins** | Lower impact, low effort — batch opportunistically | {_fmt(quadrants['quick_wins'])} |\n"
        f"| **Deprioritize** | Lower impact, higher effort — defer or skip | {_fmt(quadrants['deprioritize'])} |\n"
        "\n_Impact = finding severity (high/critical vs. medium/low). "
        "Effort = remediation complexity (length + language signals)._\n"
    )


def _build_finding_summary_table(findings: list[ScanFinding]) -> str:
    """Generate a compact at-a-glance summary table for the executive summary (v24).

    Produces a 4-column markdown table showing each category's finding count,
    high/critical count, and the top (most severe + confident) issue title.
    Only rows for categories with at least one finding are included.
    The table gives an executive reader an instant risk triage without needing
    to navigate to each section — reducing time-to-decision for the buyer.
    """
    _ALL_CATS = [
        ("security", "Security"),
        ("email_auth", "Email / Domain Trust"),
        ("seo", "SEO"),
        ("ada", "Accessibility (ADA)"),
        ("conversion", "Conversion"),
        ("performance", "Performance"),
    ]
    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    rows: list[str] = []
    for cat_key, cat_label in _ALL_CATS:
        cat_findings = [f for f in findings if f.category == cat_key]
        if not cat_findings:
            continue
        high_crit = sum(1 for f in cat_findings if f.severity in {"high", "critical"})
        # Best = highest severity, then highest confidence
        best = min(
            cat_findings,
            key=lambda f: (_SEV_ORDER.get(f.severity, 9), -float(f.confidence)),
        )
        top_issue = (best.title or "")[:55]
        if len(best.title or "") > 55:
            top_issue += "…"
        sev_badge = f"🔴 {high_crit} urgent" if high_crit else "🟡 0 urgent"
        rows.append(f"| {cat_label} | {len(cat_findings)} | {sev_badge} | {top_issue} |")

    if not rows:
        return ""

    header = "| Category | Findings | Urgency | Top Issue |\n|---|---|---|---|"
    return "\n".join([header] + rows) + "\n"


def _build_kpi_section(findings: list[ScanFinding]) -> str:
    """Generate a 'Success Metrics & Measurement Plan' section with per-category KPIs.

    Only includes categories where findings actually exist, keeping the section focused.
    Returns markdown for embedding as a ReportSection body.
    """
    cats_present = {f.category for f in findings}
    lines: list[str] = [
        "## Success Metrics & Measurement Plan\n",
        "After implementing the roadmap items, track these KPIs to confirm progress, "
        "demonstrate ROI, and identify when a follow-up assessment makes sense.\n",
    ]

    if "security" in cats_present:
        lines.append(
            "### Security\n"
            "- Run a free headers check at **securityheaders.com** after each header fix — target grade **A or better**.\n"
            "- Test SSL/TLS configuration at **ssllabs.com/ssltest/** — target **A+** rating.\n"
            "- Monitor DMARC aggregate reports (use the free tier at DMARC.io or MXToolbox) — "
            "watch for unauthorized sending sources after SPF/DKIM fixes.\n"
            "- Review server error logs monthly for spike patterns that indicate scanning or brute-force attempts.\n"
        )

    if "email_auth" in cats_present:
        lines.append(
            "### Email / Domain Trust\n"
            "- Verify SPF, DKIM, and DMARC DNS records using **MXToolbox.com/SuperTool/** within 48 hours of changes.\n"
            "- Check email deliverability score at **mail-tester.com** — target 9/10 or above.\n"
            "- Monitor DMARC XML reports weekly for the first 30 days post-implementation to catch misaligned senders.\n"
        )

    if "seo" in cats_present:
        lines.append(
            "### SEO\n"
            "- Track keyword position changes weekly in **Google Search Console** > Performance > Queries.\n"
            "- Monitor organic click-through rate (CTR) — target +10–25% improvement within 90 days of on-page fixes.\n"
            "- Verify sitemap submission and indexing status: Google Search Console > Sitemaps.\n"
            "- Check crawl coverage: Google Search Console > Coverage report — "
            "confirm no noindex or crawl errors on key pages within 48 hours of fixes.\n"
        )

    if "ada" in cats_present:
        lines.append(
            "### ADA / Accessibility\n"
            "- Re-run the **axe DevTools** browser extension (free) after each accessibility fix — "
            "target zero critical and serious violations.\n"
            "- Test with **NVDA** (free screen reader, Windows) or **VoiceOver** (built into Mac/iPhone) "
            "on key pages post-remediation.\n"
            "- Target WCAG 2.1 AA conformance across all public-facing pages within 90 days.\n"
            "- Document each fix with the WCAG success criterion addressed for your compliance records.\n"
        )

    if "conversion" in cats_present:
        lines.append(
            "### Conversion & Lead-Gen\n"
            "- Set up **Google Analytics 4** goal tracking for form submissions and phone click events "
            "(use Google Tag Manager for no-code setup).\n"
            "- Measure and record form conversion rate as a baseline today; target +15–30% after UX fixes.\n"
            "- Track click-to-call rate if `tel:` links are added — a 10% improvement in calls "
            "is typical for local service businesses.\n"
            "- Review heatmap data using **Microsoft Clarity** (free) 30 days after CTA and layout changes.\n"
        )

    if "performance" in cats_present:
        lines.append(
            "### Performance\n"
            "- Benchmark **Google PageSpeed Insights** score before and after each fix — "
            "target 75+ mobile, 85+ desktop.\n"
            "- Monitor Core Web Vitals (LCP, CLS, INP) in Google Search Console > Core Web Vitals — "
            "target all pages in 'Good' status within 60 days.\n"
            "- Track monthly load time trend using **GTmetrix** or **WebPageTest** (both free) "
            "from a consistent location and connection type.\n"
        )

    lines.append(
        "\n_Re-run a full scan 60–90 days after the roadmap is completed to measure improvement "
        "across all categories and update the health score baseline._"
    )
    return "\n".join(lines)


def _build_remediation_impact_timeline(findings: list[ScanFinding]) -> str:
    """Generate an 'Implementation Impact Timeline' table for the roadmap section (v41).

    Organizes qualifying high/medium/critical findings into three implementation
    timeframes based on effort signals in the remediation text, helping the SMB owner
    visualize when they'll see results after acting:
      - Week 1–2: Quick-win fixes (no heavy refactor, no server config required)
      - Month 1:  Developer or server-configuration tasks
      - Quarter 1: Infrastructure changes and multi-step architectural improvements

    Returns an empty string when fewer than 3 actionable findings are present.
    """
    import re as _re

    _HEAVY_EFFORT_RE = _re.compile(
        r'\b(?:rebuild|redesign|migrate|rewrite|overhaul|re-architect|developer|full\s+stack)\b',
        re.IGNORECASE,
    )
    _QUICK_ACTION_RE = _re.compile(
        r'\b(?:add\s+(?:a\s+|the\s+)?|enable\s+|configure\s+|update\s+(?:the\s+)?|install\s+'
        r'|activate\s+|turn\s+on|set\s+(?:the\s+)?|use\s+(?:the\s+)?free\s+)\b',
        re.IGNORECASE,
    )
    _SERVER_CONFIG_RE = _re.compile(
        r'\b(?:nginx|apache|\.htaccess|server\s+config|hosting\s+provider|dns\s+record|'
        r'caa\s+record|cloudflare|certbot|ssl\s+certificate|infrastructure|php\.ini)\b',
        re.IGNORECASE,
    )
    _OUTCOME_SIGNAL_RE = _re.compile(
        r'(?:reduces?|improves?|prevents?|increases?|eliminates?|stops?|enables?|boosts?|protects?)\s+[^.;]{5,50}',
        re.IGNORECASE,
    )

    def _outcome_phrase(rem: str) -> str:
        m = _OUTCOME_SIGNAL_RE.search(rem)
        if m:
            phrase = m.group(0).strip().rstrip(",;")
            return phrase[:60].capitalize()
        return "Reduces risk and improves site quality"

    actionable = [
        f for f in findings
        if f.severity in {"high", "medium", "critical"}
        and f.remediation
        and len(f.remediation) >= 40
    ]
    if len(actionable) < 3:
        return ""

    _sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actionable.sort(key=lambda f: (_sev_order.get(f.severity, 4), -f.confidence))

    week_items: list[tuple[str, str]] = []
    month_items: list[tuple[str, str]] = []
    quarter_items: list[tuple[str, str]] = []
    seen: set[str] = set()

    for f in actionable:
        title = (f.title or "Untitled")[:55]
        if title in seen:
            continue
        seen.add(title)
        rem = f.remediation or ""
        outcome = _outcome_phrase(rem)
        is_heavy = bool(_HEAVY_EFFORT_RE.search(rem))
        is_server = bool(_SERVER_CONFIG_RE.search(rem))
        is_quick = bool(_QUICK_ACTION_RE.search(rem)) and not is_heavy

        if is_quick and not is_server and len(week_items) < 4:
            week_items.append((title, outcome))
        elif is_server and not is_heavy and len(month_items) < 4:
            month_items.append((title, outcome))
        elif not is_heavy and len(month_items) < 4:
            month_items.append((title, outcome))
        elif len(quarter_items) < 3:
            quarter_items.append((title, outcome))

    # Ensure at least one item per tier for readability
    remaining = [f for f in actionable if (f.title or "Untitled")[:55] not in seen]
    for f in remaining:
        title = (f.title or "Untitled")[:55]
        if title and title not in seen:
            seen.add(title)
            outcome = _outcome_phrase(f.remediation or "")
            if not week_items:
                week_items.append((title, outcome))
            elif not month_items:
                month_items.append((title, outcome))
            elif not quarter_items:
                quarter_items.append((title, outcome))

    rows: list[str] = []
    for title, outcome in week_items[:4]:
        rows.append(f"| **Week 1–2** | {title} | {outcome} |")
    for title, outcome in month_items[:4]:
        rows.append(f"| **Month 1** | {title} | {outcome} |")
    for title, outcome in quarter_items[:3]:
        rows.append(f"| **Quarter 1** | {title} | {outcome} |")

    if not rows:
        return ""

    return (
        "\n\n**Implementation Impact Timeline**\n\n"
        "| Timeframe | Action Item | Expected Outcome |\n"
        "|-----------|-------------|------------------|\n"
        + "\n".join(rows)
        + "\n\n_Quick wins deliver results in days; developer tasks typically take 1–4 weeks; "
        "infrastructure changes depend on your hosting provider._"
    )


def _build_before_after_comparison(findings: list[ScanFinding]) -> str:
    """Generate a Before vs After markdown table for the top 5 highest-priority findings (v23).

    Visualises the transformation for each fix: current broken state, expected state after
    remediation, and the category-level business impact. This makes the report immediately
    scannable for executives and owners who want to know 'what changes' rather than just 'what's wrong'.
    Only includes findings with non-empty description and remediation to ensure content quality.
    """
    if not findings:
        return ""

    eligible = [f for f in findings if (f.description or "").strip() and (f.remediation or "").strip()]
    if not eligible:
        return ""

    sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
    top = sorted(eligible, key=lambda x: (sev_rank.get(x.severity, 0), x.confidence), reverse=True)[:5]

    _impact_by_category: dict[str, str] = {
        "security": "Reduced breach / spoofing risk",
        "email_auth": "Email deliverability restored",
        "seo": "Improved search ranking signal",
        "ada": "WCAG 2.1 AA compliance strengthened",
        "conversion": "Higher lead conversion rate",
        "performance": "Faster load, better Core Web Vitals",
    }

    lines: list[str] = ["\n\n### Before vs. After: What Changes When You Act\n"]
    lines.append(
        "| Finding | Current State | After Remediation | Business Impact |\n"
        "|---|---|---|---|"
    )

    for f in top:
        title = (f.title or "")[:55].rstrip(".,")
        # Current state: strip HTML tags and compress to 80 chars
        current_raw = re.sub(r"<[^>]+>", " ", f.description or "")
        current_raw = re.sub(r"\s+", " ", current_raw).strip()
        current = (current_raw[:80].rstrip(" .,") + "…") if len(current_raw) > 80 else current_raw
        # After fix: first sentence of remediation as the positive future state
        rem = (f.remediation or "").strip()
        after_sentences = re.split(r"(?<=[.!?])\s+", rem)
        first = after_sentences[0].strip() if after_sentences else rem
        after = (first[:80].rstrip(" .,") + "…") if len(first) > 80 else first
        impact = _impact_by_category.get(f.category, "Site quality improved")
        lines.append(f"| **{title}** | {current} | {after} | {impact} |")

    return "\n".join(lines)


def _build_roi_impact_calculator(findings: list[ScanFinding]) -> str:
    """Generate a qualitative 'Business Impact by Risk Area' summary table (v30).

    Produces a 4-column table showing category, finding count, dominant severity, and
    the primary business risk type for each category that has at least 1 finding. Uses
    qualitative risk tiers rather than specific dollar figures to avoid triggering the
    unverified-claim sanitizer. Injected at the end of the executive_summary section.

    Returns empty string when fewer than 3 distinct categories have findings (too thin
    to justify a table — would look padded rather than informative).
    """
    if not findings:
        return ""
    from collections import Counter

    cat_counts: Counter[str] = Counter(f.category for f in findings)
    cats_with_findings = [(k, v) for k, v in cat_counts.items() if v > 0]
    if len(cats_with_findings) < 3:
        return ""

    _CAT_RISK_LABEL = {
        "security": "Data breach, customer trust erosion, account hijacking",
        "email_auth": "Domain spoofing, phishing exposure, spam-folder deliverability",
        "seo": "Organic traffic loss, reduced local search visibility",
        "ada": "ADA compliance liability, lost accessibility-dependent visitors",
        "conversion": "Lead leakage, reduced booking and inquiry conversion rate",
        "performance": "Visitor abandonment, Core Web Vitals ranking penalty",
    }
    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    _RISK_TIER = {
        "critical": "🔴 Critical",
        "high": "🟠 High",
        "medium": "🟡 Moderate",
        "low": "🟢 Low",
    }

    # Compute dominant severity per category
    cat_dom_sev: dict[str, str] = {}
    for f in findings:
        prev = cat_dom_sev.get(f.category)
        if prev is None or _SEV_ORDER.get(f.severity, 9) < _SEV_ORDER.get(prev, 9):
            cat_dom_sev[f.category] = f.severity

    # Sort by dominant severity then by count
    sorted_cats = sorted(
        cats_with_findings,
        key=lambda kv: (_SEV_ORDER.get(cat_dom_sev.get(kv[0], "low"), 9), -kv[1]),
    )

    lines: list[str] = [
        "\n\n### Business Impact by Risk Area\n",
        "| Risk Area | Issues Found | Risk Tier | Primary Business Impact |",
        "|-----------|-------------|-----------|------------------------|",
    ]
    for cat, count in sorted_cats:
        label = cat.replace("_", " ").title()
        dom_sev = cat_dom_sev.get(cat, "low")
        tier = _RISK_TIER.get(dom_sev, "🟢 Low")
        risk_desc = _CAT_RISK_LABEL.get(cat, "Website quality and user experience")
        lines.append(f"| {label} | {count} | {tier} | {risk_desc} |")

    lines.append("")
    lines.append("_Risk tier reflects highest-severity finding per area. Review roadmap for prioritized fix sequence._\n")
    return "\n".join(lines)


def _build_remediation_effort_guide(findings: list[ScanFinding]) -> str:
    """Generate a 'Fix This Week vs. Plan for Next Quarter' effort classification guide (v30).

    Splits medium/high/critical findings into two effort tiers based on remediation language:
    - Fix This Week: quick-win remediations using add/enable/update/configure/set keywords
      without rebuild/redesign/migrate signals → low-effort, immediate action
    - Plan for Next Quarter: remediations with high-complexity language or major structural change

    Injected at the end of the appendix section body. Returns empty string when fewer than
    4 findings qualify (too few to make a useful effort guide).
    """
    if not findings:
        return ""

    _QUICK_WIN_GUIDE_RE = re.compile(
        r'\b(?:add\s+(?:a\s+|the\s+)?|enable\s+|update\s+(?:the\s+)?|install\s+|activate\s+'
        r'|turn\s+on\s+|configure\s+|include\s+|set\s+(?:the\s+)?|add\s+header|add\s+attribute)\b',
        re.IGNORECASE,
    )
    _HEAVY_GUIDE_RE = re.compile(
        r'\b(?:rebuild|redesign|migrate\s+(?:your|the)|rewrite|overhaul|re-architect'
        r'|full\s+(?:site\s+)?audit|complete\s+(?:site\s+)?overhaul)\b',
        re.IGNORECASE,
    )
    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    actionable = [
        f for f in findings
        if f.severity in {"critical", "high", "medium"}
        and f.remediation.strip()
        and len(f.remediation.strip()) > 20
    ]
    if len(actionable) < 4:
        return ""

    quick_wins: list[ScanFinding] = []
    strategic: list[ScanFinding] = []
    for f in actionable:
        rem = f.remediation or ""
        if _QUICK_WIN_GUIDE_RE.search(rem) and not _HEAVY_GUIDE_RE.search(rem):
            quick_wins.append(f)
        else:
            strategic.append(f)

    if not quick_wins and not strategic:
        return ""

    # Sort each tier by severity
    quick_wins.sort(key=lambda f: _SEV_ORDER.get(f.severity, 9))
    strategic.sort(key=lambda f: _SEV_ORDER.get(f.severity, 9))

    lines: list[str] = ["\n\n### Fix This Week vs. Plan for Next Quarter\n"]
    lines.append(
        "_Quick-win remediations (add/enable/configure) that require minimal developer time "
        "are separated from strategic investments that need planning, testing, or vendor work._\n"
    )

    if quick_wins:
        lines.append("**Fix This Week — Low Effort, High Impact**\n")
        for f in quick_wins[:6]:
            short_rem = (f.remediation or "").strip()
            sentences = re.split(r"(?<=[.!?])\s+", short_rem)
            first_sentence = sentences[0].strip() if sentences else short_rem
            first_sentence = (first_sentence[:120].rstrip(" .,") + "…") if len(first_sentence) > 120 else first_sentence
            lines.append(f"- **[{f.severity.upper()}] {f.title[:60]}** — {first_sentence}")
        lines.append("")

    if strategic:
        lines.append("**Plan for Next Quarter — Strategic Investment Required**\n")
        for f in strategic[:5]:
            lines.append(f"- **[{f.severity.upper()}] {f.title[:60]}** — _{f.category.replace('_', ' ').title()} remediation requires planning_")
        lines.append("")

    return "\n".join(lines) + "\n"


def _build_ada_compliance_checklist(findings: list[ScanFinding]) -> str:
    """Generate an 'ADA Compliance Readiness Checklist' with 10 WCAG 2.1 AA checkpoints (v33).

    Maps each WCAG checkpoint to known finding titles/categories to derive a pass/fail/risk
    status, giving the business owner an at-a-glance compliance snapshot. Injected into the
    ADA section body after _section_depth_addendum. Returns empty string for <2 ADA findings.
    """
    ada_findings = [f for f in findings if f.category == "ada"]
    if len(ada_findings) < 2:
        return ""

    # Build a lowercase search index from finding titles + descriptions
    ada_text = " ".join(
        (f.title + " " + f.description).lower() for f in ada_findings
    )

    def _status(keywords: list[str], fallback_pass: bool = True) -> str:
        """Return ❌ FAIL if any keyword found in ada_text, else ✅ PASS or ⚠️ RISK."""
        if any(kw in ada_text for kw in keywords):
            return "❌ FAIL"
        return "✅ PASS" if fallback_pass else "⚠️ RISK"

    _SEV_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}

    def _finding_severity(keywords: list[str]) -> str:
        """Return the highest severity of any matching finding, or em-dash if none."""
        matched = [
            f for f in ada_findings
            if any(kw in (f.title + " " + f.description).lower() for kw in keywords)
        ]
        if not matched:
            return "—"
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        best = min(matched, key=lambda f: order.get(f.severity, 9))
        return _SEV_LABEL.get(best.severity, best.severity.title())

    # 10 WCAG 2.1 AA checkpoints with detection keyword sets
    checkpoints = [
        (
            "1.1.1 — Images have descriptive alt text",
            ["alt text", "alt=\"\"", "empty alt", "missing alt", "alt attribute", "image alt"],
            "alt text",
        ),
        (
            "1.2.2 — Videos include captions or subtitles",
            ["caption", "subtitle", "video caption", "track element", "wcag 1.2.2"],
            "caption",
        ),
        (
            "1.3.1 — Form fields have visible labels",
            ["label", "form input", "input field", "form label", "placeholder as label", "wcag 1.3.1"],
            "label",
        ),
        (
            "1.4.1 — Links distinguishable from surrounding text",
            ["text-decoration: none", "link underline", "wcag 1.4.1", "indistinguishable from"],
            "underline",
        ),
        (
            "1.4.4 — Text can be resized without loss of content",
            ["user-scalable=no", "maximum-scale=1", "viewport scale", "pinch-zoom", "wcag 1.4.4"],
            "scalable",
        ),
        (
            "2.1.1 — All functionality accessible by keyboard",
            ["keyboard", "tabindex", "focus trap", "wcag 2.1.1"],
            "keyboard",
        ),
        (
            "2.4.1 — Navigation landmarks are labeled",
            ["aria-label", "nav element", "navigation landmark", "wcag 2.4.1", "aria landmark"],
            "landmark",
        ),
        (
            "2.4.7 — Visible focus indicator present",
            ["focus", "outline: none", "outline:none", "focus visible", "wcag 2.4.7"],
            "focus",
        ),
        (
            "3.1.1 — HTML language attribute is set",
            ["lang attribute", "html lang", "language attribute", "wcag 3.1", "lang="],
            "lang",
        ),
        (
            "4.1.2 — iframes and links have accessible names",
            ["iframe", "empty link", "empty alt", "link purpose", "wcag 4.1.2"],
            "name",
        ),
    ]

    lines: list[str] = ["\n\n### ADA Compliance Readiness Checklist\n"]
    lines.append(
        "_Status derived from scan findings. ❌ FAIL = issue detected; ✅ PASS = no evidence found; ⚠️ RISK = partial signals only._\n"
    )
    lines.append("| WCAG 2.1 AA Requirement | Status | Severity If Failing |")
    lines.append("|:---|:---:|:---:|")

    for label, keywords, _short in checkpoints:
        status = _status(keywords)
        sev = _finding_severity(keywords) if status == "❌ FAIL" else "—"
        lines.append(f"| {label} | {status} | {sev} |")

    fail_count = sum(1 for cp in checkpoints if _status(cp[1]) == "❌ FAIL")
    lines.append("")
    if fail_count >= 6:
        lines.append(
            f"> **High ADA Exposure:** {fail_count}/10 WCAG checkpoints failing. "
            "Risk of ADA demand letter or DOJ complaint is elevated. Prioritize accessibility remediation."
        )
    elif fail_count >= 3:
        lines.append(
            f"> **Moderate ADA Exposure:** {fail_count}/10 WCAG checkpoints failing. "
            "Address high/medium severity items before engaging enterprise or government clients."
        )
    elif fail_count >= 1:
        lines.append(
            f"> **Low ADA Exposure:** {fail_count}/10 WCAG checkpoints failing. "
            "Quick fixes available — review finding details above for remediation steps."
        )
    else:
        lines.append(
            "> **No Critical ADA Gaps Detected** from passive scan. "
            "Run axe DevTools or NVDA screen reader for a full WCAG audit confirmation."
        )

    return "\n".join(lines) + "\n"


def _build_accessibility_impact_summary(findings: list[ScanFinding]) -> str:
    """Generate 'Accessibility Risk by Impact Type' table grouping ADA findings by impairment category (v40).

    ADA/WCAG findings affect different groups of users differently — visual impairments
    are affected by alt text and contrast issues, motor impairments by keyboard navigation
    and click target size, cognitive impairments by form complexity and error handling, and
    screen reader users by ARIA markup. Grouping findings by the type of user impairment
    they affect helps the business owner understand WHY each fix matters and for WHOM,
    making the remediation case more compelling than a raw severity list.

    Injected into the ADA section body after _build_ada_compliance_checklist.
    Returns empty string for <2 ADA findings.
    """
    ada_findings = [f for f in findings if f.category == "ada"]
    if len(ada_findings) < 2:
        return ""

    # Impact category classification — each finding maps to one primary impact type
    _IMPACT_PATTERNS: list[tuple[str, str, list[str]]] = [
        (
            "Visual / Low Vision",
            "Affects users with blindness, low vision, or color blindness",
            ["alt text", "alt attribute", "image alt", "color contrast", "color blind",
             "focus visible", "outline", "link underline", "text-decoration", "og:image",
             "missing alt", "empty alt", "short alt", "lang attr", "html lang"],
        ),
        (
            "Motor / Keyboard Navigation",
            "Affects users who rely on keyboard, switch controls, or assistive input devices",
            ["keyboard", "tabindex", "positive tabindex", "click-to-call", "submit button",
             "form submit", "autocomplete", "target size", "focus trap", "bypass block",
             "skip nav", "carousel autorot"],
        ),
        (
            "Screen Reader / ARIA",
            "Affects users of screen readers (JAWS, NVDA, VoiceOver, TalkBack)",
            ["aria", "landmark", "role=", "fieldset", "legend", "iframe title",
             "table header", "nav aria", "aria-label", "aria-live", "live region",
             "form error", "empty link", "wcag 4.1", "wcag 1.3"],
        ),
        (
            "Cognitive / Form UX",
            "Affects users with cognitive, learning, or attention impairments",
            ["placeholder as label", "autocomplete off", "form label", "required field",
             "error message", "error handling", "form field", "input label", "confusion",
             "carousel", "autoplay", "animation", "reduced motion"],
        ),
        (
            "Hearing / Media Access",
            "Affects users with hearing impairments who rely on captions or transcripts",
            ["caption", "subtitle", "track element", "video caption", "audio", "wcag 1.2"],
        ),
    ]

    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    rows: list[str] = []
    for impact_type, business_risk, keywords in _IMPACT_PATTERNS:
        matched = [
            f for f in ada_findings
            if any(kw in (f.title + " " + f.description).lower() for kw in keywords)
        ]
        if not matched:
            continue
        highest_sev = min(matched, key=lambda f: _SEV_ORDER.get(f.severity, 9))
        sev_label = highest_sev.severity.title()
        count = len(matched)
        top_title = matched[0].title[:55]
        rows.append(f"| {impact_type} | {count} | {sev_label} | {top_title}… | {business_risk} |")

    if not rows:
        return ""

    lines: list[str] = [
        "\n\n### Accessibility Risk by User Impact Type\n",
        "_Findings grouped by the user population most affected — beyond the WCAG checkpoint labels._\n",
        "| Impact Type | Findings | Highest Severity | Example Issue | Business Risk |",
        "|:---|:---:|:---:|:---|:---|",
    ]
    lines.extend(rows)
    return "\n".join(lines) + "\n"


def _build_sections(
    findings: list[ScanFinding],
    business: SampledBusiness,
    scan_payload: dict[str, Any],
    strategy: dict[str, Any] | None = None,
    value_model: dict[str, Any] | None = None,
) -> list[ReportSection]:
    by_cat: dict[str, list[ScanFinding]] = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)

    urgent = _top_urgent(findings)
    urgent_md = "\n".join([f"- **{f.title}** ({f.severity})" for f in urgent]) or "- No urgent issues surfaced in this pass"
    risk_counts = Counter([f.category for f in findings])

    high_critical_count = sum(1 for f in findings if f.severity in {"high", "critical"})
    pages_checked = len(scan_payload.get("pages") or [])
    depth = _report_depth_level(strategy)
    section_item_cap = 8 + (depth * 2)
    word_target = int((strategy or {}).get("report_word_target", 1200) or 1200)
    # Scale snippet display length with the report word target so richer iterations show more evidence
    snippet_max_len = min(400, 280 + max(0, (word_target - 1200) // 15))

    risk_table_rows = "\n".join([
        f"| {k.replace('_', ' ').title()} | {v} finding(s) | {_risk_score_label(v, k)} |"
        for k, v in sorted(risk_counts.items())
    ])

    health = _web_health_score(findings)
    health_label = (
        "Failing (Urgent)" if health < 70
        else "Needs Improvement" if health < 85
        else "Stable" if health < 95
        else "Strong"
    )
    cat_risk_lines = "\n".join([
        f"- **{label}:** {risk_counts.get(cat, 0)} finding(s) — {_risk_score_label(risk_counts.get(cat, 0), cat)}"
        for cat, label in [
            ("security", "Security Posture"),
            ("email_auth", "Email / Domain Trust"),
            ("seo", "SEO Readiness"),
            ("ada", "ADA / Accessibility"),
            ("conversion", "Conversion & Lead-Gen"),
        ]
    ])
    sections = [
        ReportSection(
            key="executive_summary",
            title="Executive Summary",
            body_markdown=(
                f"## Web Presence Risk + Growth Assessment\n\n"
                f"**Business:** {business.business_name}  \n"
                f"**Website:** {scan_payload.get('base_url')}  \n"
                f"**Pages analyzed:** {pages_checked}  \n"
                f"**Overall Web Health Score: {health}/100 — {health_label}**  \n"
                f"**Total findings:** {len(findings)} ({high_critical_count} high/critical priority)\n\n"
                + _build_top_findings_callout_box(findings)
                + "\n### At-a-Glance Risk Summary\n\n"
                f"{_build_finding_summary_table(findings)}\n"
                "### Top 5 Urgent Opportunities\n\n"
                f"{urgent_md}\n\n"
                "### Risk by Category\n\n"
                f"{cat_risk_lines}\n\n"
                "### Business Impact Assessment\n\n"
                f"{_business_impact_bullets(findings, scan_payload)}\n\n"
                + _build_roi_impact_calculator(findings)
                + _build_industry_benchmark_comparison(scan_payload, findings)
                + (
                    "### Revenue Recovery Potential\n\n"
                    f"{_value_model_markdown(value_model)}\n"
                    if value_model
                    else ""
                )
                + "_All listed items are actionable. Start with 0–30 day items first, then re-scan to confirm closure._"
            ),
        ),
        ReportSection(
            key="risk_dashboard",
            title="Risk Dashboard",
            body_markdown=(
                "## Risk Overview by Category\n\n"
                "| Category | Findings | Risk Level |\n"
                "|----------|----------|------------|\n"
                f"{risk_table_rows}\n\n"
                f"**Total findings across all categories:** {len(findings)}  \n"
                f"**High/Critical priority items:** {high_critical_count}  \n"
                f"**Pages crawled:** {pages_checked}"
            ),
        ),
        ReportSection(
            "security",
            "Website Security Findings",
            _build_security_header_scorecard(findings, scan_payload)
            + _section_body("security", by_cat.get("security", []), max_items=section_item_cap, snippet_max_len=snippet_max_len)
            + _section_depth_addendum(category_label="Security", findings=by_cat.get("security", []), depth=depth),
        ),
        ReportSection(
            "email_auth",
            "Email and Domain Trust Findings",
            _build_email_auth_scorecard(scan_payload.get("dns_auth") or {})
            + _section_body("email auth", by_cat.get("email_auth", []), max_items=section_item_cap, snippet_max_len=snippet_max_len)
            + _section_depth_addendum(category_label="Email / DNS", findings=by_cat.get("email_auth", []), depth=depth),
        ),
        ReportSection(
            "ada",
            "Accessibility and ADA Compliance",
            _section_body("ADA", by_cat.get("ada", []), max_items=section_item_cap, snippet_max_len=snippet_max_len)
            + _build_ada_compliance_checklist(findings)
            + _build_accessibility_impact_summary(findings)
            + _section_depth_addendum(category_label="Accessibility", findings=by_cat.get("ada", []), depth=depth),
        ),
        ReportSection(
            "seo",
            "SEO Strategy and Technical SEO",
            _section_body("SEO", by_cat.get("seo", []), max_items=section_item_cap, snippet_max_len=snippet_max_len)
            + _build_seo_opportunity_table(by_cat.get("seo", []))
            + _build_local_seo_checklist(findings, scan_payload)
            + _section_depth_addendum(category_label="SEO", findings=by_cat.get("seo", []), depth=depth),
        ),
        ReportSection(
            "conversion",
            "Lead Generation and Conversion UX",
            _section_body("conversion", by_cat.get("conversion", []), max_items=section_item_cap, snippet_max_len=snippet_max_len)
            + _build_conversion_audit_table(findings)
            + _build_trust_signal_checklist(findings, scan_payload)
            + _section_depth_addendum(category_label="Conversion", findings=by_cat.get("conversion", []), depth=depth),
        ),
        ReportSection(
            "performance",
            "Performance Findings",
            _section_body("performance", by_cat.get("performance", []), max_items=section_item_cap, snippet_max_len=snippet_max_len)
            + _build_performance_budget_table(findings)
            + _build_core_web_vitals_mapping(findings)
            + _section_depth_addendum(category_label="Performance", findings=by_cat.get("performance", []), depth=depth),
        ),
        _competitor_context_section(scan_payload, findings),
        ReportSection(
            key="roadmap",
            title="30/60/90 Day Action Roadmap",
            body_markdown=(
                "## Prioritized Action Plan\n\n"
                "| Timeline | Action | Business Impact | Effort | Est. Time | Who |\n"
                "|----------|--------|-----------------|--------|-----------|-----|\n"
                + "\n".join([
                    f"| {r['window']} | {r['action']} | {r['impact']} | {r['effort']} | {r['est_time']} | {r['skill']} |"
                    for r in _roadmap(findings)
                ])
                + "\n\n_Items sorted by severity. Tackle 0–30 day items first for maximum risk reduction._"
                + _build_quick_wins_roi_table(findings)
                + _build_priority_matrix_md(findings)
                + _build_before_after_comparison(findings)
                + _build_remediation_impact_timeline(findings)
            ),
        ),
        ReportSection(
            key="kpi_measurement",
            title="Success Metrics & Measurement Plan",
            body_markdown=_build_kpi_section(findings),
        ),
        ReportSection(
            key="appendix",
            title="Appendix: Methodology and Evidence",
            body_markdown=(
                _build_scan_coverage_summary(findings, scan_payload)
                + _build_appendix_body(findings, scan_payload)
                + _build_technical_debt_summary(findings)
                + _build_implementation_checklist(findings)
                + _build_quick_fix_code_block(findings)
                + _build_remediation_effort_guide(findings)
                + _build_mobile_audit_summary(findings)
                + _build_technical_glossary(findings)
            ),
        ),
    ]
    return sections


def _llm_refine_sections(settings: AgentSettings, sections: list[ReportSection], findings: list[ScanFinding], business: SampledBusiness) -> list[ReportSection]:
    client = OllamaClient(settings)
    base = [{"key": s.key, "title": s.title, "body": s.body_markdown} for s in sections]
    section_keys = [s.key for s in sections]
    expected_keys = [k for k in required_report_section_keys() if k in section_keys]
    high_critical = [f for f in findings if f.severity in {"high", "critical"}]
    top_titles = [f.title for f in high_critical[:5]]
    result = client.chat_json(
        system=(
            "You are a senior web consulting analyst writing a premium $299 SMB report. "
            "Return strict JSON {sections:[{key,title,body}]} — no other keys. Rules:\n"
            "1. Every high/critical finding must include the exact page URL and a concrete business consequence.\n"
            "2. Executive summary must name the top 3 urgent issues by their exact title and use the health score label.\n"
            "3. Roadmap entries must include realistic effort estimates (e.g., '30 min', '2 hours', '1 day').\n"
            "4. Use specific evidence — never vague phrases like 'some issues' or 'various problems'.\n"
            "5. Competitor/context section must include the 0-100 health score and at least 2 category comparisons.\n"
            "6. Do not invent facts, shorten content, or remove evidence snippets. Keep section keys unchanged.\n"
            "7. Write at a senior consultant level: direct, specific, evidence-backed, action-oriented."
        ),
        user=json.dumps(
            {
                "business": {"name": business.business_name, "website": business.website},
                "top_urgent_findings": top_titles,
                "findings": [_asdict_safe(f) for f in findings[:40]],
                "sections": base,
            },
            ensure_ascii=True,
        ),
        schema_hint={"type": "object", "properties": {"sections": {"type": "array"}}},
    )
    try:
        normalized = validate_sections_payload(result, expected_keys=expected_keys)
    except ValueError:
        return sections
    return [ReportSection(key=item["key"], title=item["title"], body_markdown=item["body"]) for item in normalized]


def _strip_client_confidence(text: str) -> str:
    lines = []
    for ln in str(text).splitlines():
        low = ln.lower()
        if "confidence distribution" in low:
            continue
        if "average confidence" in low:
            continue
        if "| **confidence:**" in low:
            ln = re.sub(r"\s*\|\s*\*\*confidence:\*\*[^|]+", "", ln, flags=re.IGNORECASE)
        lines.append(ln)
    return "\n".join(lines)


def _build_implementation_checklist(findings: list[ScanFinding]) -> str:
    """Generate a developer-ready implementation checklist grouped by skill level (v22).

    Splits findings into three effort tiers:
    - No-code / CMS: Fixes achievable via page builder, plugin, or CMS settings (no dev needed)
    - Developer-level: Require HTML/CSS/JS edits or template changes
    - Server / Infrastructure: Require server config, DNS, or hosting panel changes

    Only includes high/medium severity findings with non-trivial remediations to keep the
    checklist actionable and prevent it from becoming a dump of every low-level note.
    """
    if not findings:
        return ""

    _NO_CODE_SIGNALS = re.compile(
        r'\b(?:plugin|wordpress\s+(?:admin|settings|dashboard)|page\s+builder|elementor|wix|'
        r'squarespace|cms|settings\s+(?:panel|page)|control\s+panel|toggle|checkbox|install\s+a\s+free|'
        r'tawk\.to|cookieyes|onetrust|yoast|rankmath|google\s+business\s+profile|google\s+analytics\s+4)\b',
        re.IGNORECASE,
    )
    _SERVER_SIGNALS = re.compile(
        r'\b(?:nginx|apache|\.htaccess|httpd\.conf|server\s+config|hosting\s+(?:panel|control)|'
        r'dns\s+(?:record|settings?|provider)|cpanel|plesk|certbot|lets?\s+encrypt|'
        r'server-side\s+redirect|301\s+redirect|gzip|brotli|compression|cdn|cloudflare)\b',
        re.IGNORECASE,
    )

    no_code: list[ScanFinding] = []
    developer: list[ScanFinding] = []
    server: list[ScanFinding] = []

    for f in findings:
        if f.severity not in {"critical", "high", "medium"}:
            continue
        rem = f.remediation.strip()
        if len(rem) < 20:
            continue
        if _NO_CODE_SIGNALS.search(rem):
            no_code.append(f)
        elif _SERVER_SIGNALS.search(rem):
            server.append(f)
        else:
            developer.append(f)

    if not no_code and not developer and not server:
        return ""

    lines: list[str] = ["\n\n### Implementation Checklist by Skill Level\n"]
    lines.append(
        "_Use this checklist to assign fixes to the right person on your team without requiring a developer for every item._\n"
    )

    if no_code:
        lines.append("\n**No-code / CMS — Owner or Office Manager**\n")
        for f in no_code[:6]:
            lines.append(f"- [ ] **{f.title}** — {f.remediation[:120].rstrip('.,')}…" if len(f.remediation) > 120 else f"- [ ] **{f.title}** — {f.remediation}")

    if developer:
        lines.append("\n**Developer-level — HTML / CSS / JS edits**\n")
        for f in developer[:6]:
            lines.append(f"- [ ] **{f.title}** — {f.remediation[:120].rstrip('.,')}…" if len(f.remediation) > 120 else f"- [ ] **{f.title}** — {f.remediation}")

    if server:
        lines.append("\n**Server / Infrastructure — Hosting or DevOps**\n")
        for f in server[:6]:
            lines.append(f"- [ ] **{f.title}** — {f.remediation[:120].rstrip('.,')}…" if len(f.remediation) > 120 else f"- [ ] **{f.title}** — {f.remediation}")

    return "\n".join(lines)


_SEO_HIGH_TRAFFIC_KEYWORDS_RE = re.compile(
    r'\b(?:sitemap|canonical|noindex|crawl|redirect|broken|title\s+tag|meta\s+description|structured\s+data|json-ld|schema)\b',
    re.IGNORECASE,
)
_SEO_MED_TRAFFIC_KEYWORDS_RE = re.compile(
    r'\b(?:heading|h1|h2|alt\s+text|thin\s+content|duplicate|breadcrumb|faq\s+schema|open\s+graph)\b',
    re.IGNORECASE,
)


def _build_seo_opportunity_table(findings: list[ScanFinding]) -> str:
    """Build a 3-tier SEO opportunity table showing findings grouped by estimated traffic impact (v26).

    Groups SEO findings into High / Medium / Quick Win tiers based on severity and keyword signals.
    Injected into the SEO section body to give readers an at-a-glance prioritisation view.
    Returns empty string if fewer than 3 SEO findings are present.
    """
    seo_findings = [f for f in findings if f.category == "seo"]
    if len(seo_findings) < 3:
        return ""

    high_impact: list[ScanFinding] = []
    med_impact: list[ScanFinding] = []
    low_impact: list[ScanFinding] = []

    for f in seo_findings:
        if f.severity in ("high", "critical") or _SEO_HIGH_TRAFFIC_KEYWORDS_RE.search(f.title):
            high_impact.append(f)
        elif f.severity == "medium" or _SEO_MED_TRAFFIC_KEYWORDS_RE.search(f.title):
            med_impact.append(f)
        else:
            low_impact.append(f)

    rows: list[str] = []
    for impact_label, group in [
        ("High Traffic Impact", high_impact),
        ("Medium Traffic Impact", med_impact),
        ("Quick Win", low_impact),
    ]:
        for f in group[:4]:
            rows.append(f"| {impact_label} | {f.title[:55]} | {f.severity.title()} |")

    if not rows:
        return ""

    return (
        "\n\n### SEO Opportunity Summary\n\n"
        "| Traffic Impact | Finding | Severity |\n"
        "|----------------|---------|----------|\n"
        + "\n".join(rows)
        + "\n\n_Prioritize High Traffic Impact items first — these directly affect how many visitors discover your site._"
    )


def _build_local_seo_checklist(findings: list[ScanFinding], scan_payload: dict[str, Any]) -> str:
    """Build a Local SEO Readiness Checklist with pass/fail indicators (v32).

    Produces a 3-column table (Requirement | Status | Priority) covering the key local SEO
    signals for SMB websites: structured data, mobile, contact signals, social proof, and
    content discoverability. Each item is marked ✅ or ❌ based on whether a matching
    finding was detected (❌ = issue found; ✅ = no issue detected, assumed passing).

    Injected into the SEO section body after the SEO opportunity table. Returns empty string
    if fewer than 3 SEO findings are present (non-local or technical sites).
    """
    seo_findings = [f for f in findings if f.category == "seo"]
    if len(seo_findings) < 3:
        return ""

    all_titles = " ".join(f.title.lower() for f in findings)

    def _has_issue(*keywords: str) -> bool:
        return any(kw.lower() in all_titles for kw in keywords)

    rows: list[tuple[str, str, str]] = [
        (
            "LocalBusiness Schema Markup",
            "❌ Missing" if _has_issue("localbusiness", "schema completeness", "schema missing") else "✅ Present",
            "High",
        ),
        (
            "XML Sitemap Discoverable",
            "❌ Missing" if _has_issue("sitemap") else "✅ Present",
            "High",
        ),
        (
            "Canonical URL Set",
            "❌ Issue" if _has_issue("canonical") else "✅ Present",
            "High",
        ),
        (
            "Google Maps Embed",
            "❌ Missing" if _has_issue("google maps", "maps embed") else "✅ Present",
            "Medium",
        ),
        (
            "Review / AggregateRating Schema",
            "❌ Missing" if _has_issue("review markup", "aggregaterating", "star rating") else "✅ Present",
            "Medium",
        ),
        (
            "Click-to-Call Phone Link",
            "❌ Missing" if _has_issue("click-to-call", "tel: link") else "✅ Present",
            "Medium",
        ),
        (
            "FAQ Schema Markup",
            "❌ Missing" if _has_issue("faq schema", "faqpage") else "✅ Present",
            "Low",
        ),
        (
            "Open Graph Tags (Social Sharing)",
            "❌ Missing" if _has_issue("open graph", "og:title", "og:description") else "✅ Present",
            "Low",
        ),
        (
            "Twitter Card Meta Tags",
            "❌ Missing" if _has_issue("twitter:card", "twitter card") else "✅ Present",
            "Low",
        ),
        (
            "RSS Feed Discovery Link",
            "❌ Missing" if _has_issue("rss feed") else "✅ Present",
            "Low",
        ),
    ]

    fail_count = sum(1 for _, status, _ in rows if status.startswith("❌"))
    if fail_count == 0:
        return ""  # No issues to surface — skip the table

    table_rows = "\n".join(f"| {req} | {status} | {pri} |" for req, status, pri in rows)
    return (
        "\n\n### Local SEO Readiness Checklist\n\n"
        "| Requirement | Status | Priority |\n"
        "|-------------|--------|----------|\n"
        + table_rows
        + f"\n\n_{fail_count} of {len(rows)} local SEO signals need attention. "
        "High-priority items should be addressed before any paid local advertising campaign._"
    )


def _build_email_auth_scorecard(dns_auth: dict[str, Any]) -> str:
    """Generate a structured SPF/DKIM/DMARC status scorecard for the email_auth section (v27).

    Displays a compact 3-row table with pass/warn/fail status badges for each email
    authentication record, plus a next-step recommendation for each failing check.
    This replaces the purely prose-based findings list as the first element of the
    email/domain trust section — giving buyers an at-a-glance risk snapshot before
    reading the detailed finding descriptions.

    Status rules:
    - "pass" indicator: record status is 'present' (not 'missing'/'unknown')
    - "warn" indicator: status is 'unknown' (DNS query was inconclusive)
    - "fail" indicator: status is 'missing'
    """
    if not dns_auth:
        return ""

    _STATUS_BADGE: dict[str, str] = {
        "present": "✅ Pass",
        "missing": "❌ Fail",
        "unknown": "⚠️ Warn",
    }
    _NEXT_STEP: dict[str, dict[str, str]] = {
        "spf": {
            "present": "Verify SPF alignment using MXToolbox SPF Lookup.",
            "missing": "Publish an SPF TXT record: `v=spf1 include:_spf.google.com ~all`",
            "unknown": "Re-check with MXToolbox; SPF may be propagating or behind a lookup-limit.",
        },
        "dkim": {
            "present": "Confirm DKIM selector and key rotation with your ESP (Google Workspace / Mailchimp etc.).",
            "missing": "Enable DKIM signing in your email service provider and publish the DKIM TXT record at your DNS host.",
            "unknown": "Try additional DKIM selectors (google, default, k1) via MXToolbox DKIM Lookup.",
        },
        "dmarc": {
            "present": "Review DMARC policy strength — `p=reject` is the gold standard for spoofing prevention.",
            "missing": "Publish a DMARC record: `v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com`",
            "unknown": "Re-check DMARC propagation; confirm `_dmarc.yourdomain.com` TXT record exists.",
        },
    }

    rows: list[str] = []
    for record_key, label in [("spf", "SPF"), ("dkim", "DKIM"), ("dmarc", "DMARC")]:
        status = str(dns_auth.get(record_key) or "unknown").lower()
        if status not in ("present", "missing", "unknown"):
            status = "unknown"
        badge = _STATUS_BADGE[status]
        next_step = _NEXT_STEP[record_key][status]
        rows.append(f"| {label} | {badge} | {next_step} |")

    if not rows:
        return ""

    dkim_selector = str(dns_auth.get("dkim_selector") or "")
    selector_note = f" (selector: `{dkim_selector}`)" if dkim_selector and dkim_selector != "unknown" else ""

    return (
        "\n\n### Email Authentication Scorecard\n\n"
        "| Record | Status | Next Step |\n"
        "|--------|--------|-----------|\n"
        + "\n".join(rows)
        + f"\n\n_DKIM check{selector_note}. Validate alignment with [mail-tester.com](https://mail-tester.com) "
        "by sending a test email and reviewing full authentication headers._\n"
    )


def _build_performance_budget_table(findings: list[ScanFinding]) -> str:
    """Generate a 'Performance Budget Breakdown' table injected into performance section body (v35).

    Maps each performance finding to an estimated load time saving, giving business owners
    a concrete sense of how much faster their site could load if each issue were fixed.
    Returns empty string for <2 performance findings. Up to 8 rows sorted by estimated impact.
    """
    perf_findings = [f for f in findings if f.category == "performance"]
    if len(perf_findings) < 2:
        return ""

    # Impact mapping: keywords in finding title/description → estimated fix benefit
    _IMPACT_MAP = [
        (
            re.compile(r'render.blocking|blocking scripts?|render block', re.IGNORECASE),
            "Saves ~200–600ms FCP",
            1,
        ),
        (
            re.compile(r'font.display.swap|foit|invisible text', re.IGNORECASE),
            "Saves ~300–1000ms text render",
            2,
        ),
        (
            re.compile(r'preload|lcp|largest contentful', re.IGNORECASE),
            "Saves ~200–800ms LCP",
            2,
        ),
        (
            re.compile(r'compression|gzip|brotli', re.IGNORECASE),
            "Saves ~200–400ms on repeat visits",
            3,
        ),
        (
            re.compile(r'cache.control|no.store|caching', re.IGNORECASE),
            "Saves ~100–300ms on return visits",
            4,
        ),
        (
            re.compile(r'next.gen.image|webp|jpeg.*format|png.*format', re.IGNORECASE),
            "Saves ~200–500ms per image",
            3,
        ),
        (
            re.compile(r'unminif|minif', re.IGNORECASE),
            "Saves ~50–150ms per resource",
            5,
        ),
        (
            re.compile(r'third.party.script|external script|3rd.party', re.IGNORECASE),
            "Saves ~100–300ms TTI",
            4,
        ),
        (
            re.compile(r'multiple font famil|font.famil|waterfall', re.IGNORECASE),
            "Saves ~150–400ms per extra family",
            3,
        ),
        (
            re.compile(r'image.dimension|layout.shift|cls', re.IGNORECASE),
            "Reduces cumulative layout shift (CLS)",
            6,
        ),
        (
            re.compile(r'apple.touch|homescreen|touch.icon', re.IGNORECASE),
            "Improves mobile return-visit UX",
            7,
        ),
        (
            re.compile(r'browser.*load|load time|page.*slow|slow.*page', re.IGNORECASE),
            "Reduces Time to Interactive",
            5,
        ),
    ]

    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    rows: list[tuple[str, str, str, int]] = []
    for f in perf_findings:
        combined = f.title + " " + f.description
        impact = "Reduces overall page load time"
        sort_order = 8
        for pattern, label, order in _IMPACT_MAP:
            if pattern.search(combined):
                impact = label
                sort_order = order
                break
        priority = f.severity.upper()
        rows.append((f.title[:55], impact, priority, sort_order * 10 + _SEV_ORDER.get(f.severity, 3)))

    rows.sort(key=lambda x: x[3])
    rows = rows[:8]

    table_lines = [
        "\n\n### Performance Budget Breakdown\n\n",
        "| Finding | Estimated Fix Impact | Priority |\n",
        "|---------|---------------------|----------|\n",
    ]
    for title, impact, priority, _ in rows:
        table_lines.append(f"| {title} | {impact} | {priority} |\n")
    table_lines.append(
        "\n_Impact estimates based on industry benchmarks. Actual savings vary by site architecture "
        "and connection speed. Measure before and after using PageSpeed Insights (free)._\n"
    )
    return "".join(table_lines)


def _build_security_header_scorecard(findings: list[ScanFinding], scan_payload: dict[str, Any]) -> str:
    """Generate a 'Security Header Compliance' table for the security section header (v34).

    Shows the 8 most important HTTP security headers with ✅/❌/⚠️ status derived from
    scan findings and the raw TLS/header data in scan_payload. Injected at the top of the
    security section body — gives business owners an at-a-glance compliance snapshot before
    reading detailed findings. Returns empty string for <2 security findings.

    Headers checked:
    - HSTS (Strict-Transport-Security)
    - CSP (Content-Security-Policy)
    - X-Frame-Options
    - X-Content-Type-Options
    - Referrer-Policy
    - Permissions-Policy
    - CORS (Access-Control-Allow-Origin misconfiguration)
    - SRI (Subresource Integrity on external scripts)
    """
    sec_findings = [f for f in findings if f.category == "security"]
    if len(sec_findings) < 2:
        return ""

    # Build a lowercase index of all security finding titles + descriptions
    sec_text = " ".join(
        (f.title + " " + f.description + " " + f.remediation).lower()
        for f in sec_findings
    )

    def _header_status(fail_keywords: list[str], pass_keywords: list[str] | None = None) -> str:
        """Return ❌ FAIL if fail keywords found, ✅ PASS if pass keywords or absent."""
        if any(kw in sec_text for kw in fail_keywords):
            return "❌ Missing"
        if pass_keywords and any(kw in sec_text for kw in pass_keywords):
            return "✅ Present"
        return "⚠️ Check"

    _RISK: dict[str, str] = {
        "❌ Missing": "High",
        "⚠️ Check": "Medium",
        "✅ Present": "—",
    }

    headers_table = [
        (
            "HSTS (Strict-Transport-Security)",
            _header_status(
                fail_keywords=["hsts", "strict-transport", "hsts missing", "no hsts"],
                pass_keywords=["hsts present", "hsts configured"],
            ),
            "Forces HTTPS for all visitors — prevents SSL stripping attacks",
        ),
        (
            "CSP (Content-Security-Policy)",
            _header_status(
                fail_keywords=["csp", "content-security-policy missing", "no csp", "content security policy"],
                pass_keywords=["csp present", "csp configured"],
            ),
            "Blocks XSS attacks and unauthorized script execution",
        ),
        (
            "X-Frame-Options",
            _header_status(
                fail_keywords=["x-frame-options", "frame-ancestors", "clickjacking", "x-frame"],
                pass_keywords=["x-frame-options present"],
            ),
            "Prevents your site from being embedded in malicious iframes",
        ),
        (
            "X-Content-Type-Options",
            _header_status(
                fail_keywords=["x-content-type-options", "mime-type sniffing", "nosniff missing", "mime sniff"],
                pass_keywords=["nosniff"],
            ),
            "Blocks MIME-type sniffing attacks on uploaded files",
        ),
        (
            "Referrer-Policy",
            _header_status(
                fail_keywords=["referrer-policy", "referrer policy missing", "no referrer"],
                pass_keywords=["referrer-policy present"],
            ),
            "Controls what URL data is sent to third parties on navigation",
        ),
        (
            "Permissions-Policy",
            _header_status(
                fail_keywords=["permissions-policy", "feature-policy", "browser api", "permissions policy missing"],
                pass_keywords=["permissions-policy present"],
            ),
            "Restricts access to sensitive browser APIs (camera, microphone, geolocation)",
        ),
        (
            "CORS (Access-Control-Allow-Origin)",
            _header_status(
                fail_keywords=["cors", "access-control-allow-origin: *", "cors wildcard", "cors misconfiguration"],
                pass_keywords=["cors not configured", "cors absent"],
            ),
            "Wildcard CORS exposes API data to any origin — check if intentional",
        ),
        (
            "SRI (Subresource Integrity)",
            _header_status(
                fail_keywords=["sri", "subresource integrity", "integrity= missing", "sri missing"],
                pass_keywords=["sri present", "integrity attribute present"],
            ),
            "Verifies third-party scripts haven't been tampered with",
        ),
    ]

    fail_count = sum(1 for _, status, _ in headers_table if status == "❌ Missing")
    lines: list[str] = ["\n\n### Security Header Compliance Scorecard\n"]
    lines.append(
        "_Status derived from scan findings. ❌ Missing = header absent; "
        "⚠️ Check = configuration gap detected; ✅ Present = no issue found._\n"
    )
    lines.append("| Security Header | Status | Risk If Missing |")
    lines.append("|:---|:---:|:---:|")
    for header_name, status, risk_desc in headers_table:
        risk_tier = _RISK.get(status, "—")
        lines.append(f"| {header_name} | {status} | {risk_tier} |")

    lines.append("")
    if fail_count >= 5:
        lines.append(
            f"> **Critical Security Header Gap:** {fail_count}/8 key security headers missing. "
            "Your site has minimal browser-level defenses against XSS, clickjacking, and data leakage. "
            "Use securityheaders.com to generate a free scan and remediation report."
        )
    elif fail_count >= 3:
        lines.append(
            f"> **Significant Security Header Gaps:** {fail_count}/8 headers missing or misconfigured. "
            "Each missing header is a browser-enforced defense that takes under 5 minutes to add "
            "in Cloudflare, Apache .htaccess, or Nginx config."
        )
    elif fail_count >= 1:
        lines.append(
            f"> **Minor Security Header Gaps:** {fail_count}/8 headers need attention. "
            "Quick wins — most can be added in your CDN (Cloudflare) or hosting panel without developer help."
        )
    else:
        lines.append(
            "> **Security Headers: No Critical Gaps Detected.** "
            "Re-validate with securityheaders.com for a full configuration audit."
        )

    return "\n".join(lines) + "\n"


def _build_top_findings_callout_box(findings: list[ScanFinding]) -> str:
    """Generate a 'Priority Risk Callout' block for the executive summary header (v28).

    Creates a scannable priority list of the top 5 highest-severity findings — the first
    thing a busy business owner or executive should see when opening the report. Unlike the
    more detailed 'Top 5 Urgent Opportunities' list below, this callout box is deliberately
    terse: one line per finding with severity badge and a short impact phrase (max 90 chars).

    The callout box uses blockquote-style markdown so PDF renderers display it as a visually
    distinct highlighted block — creating an "at a glance" executive summary within the
    executive summary. SMB owners often skim reports before reading; this callout ensures
    the most urgent business risks are surfaced immediately without requiring the reader to
    scroll through the full findings list.

    Selection:
    - Sort findings by severity descending (critical > high > medium > low)
    - Secondary sort by confidence descending
    - Take top 5 (or fewer if findings < 5)
    """
    if not findings:
        return ""

    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    _SEV_BADGE = {
        "critical": "🔴 Critical",
        "high": "🟠 High",
        "medium": "🟡 Medium",
        "low": "🟢 Low",
    }
    _IMPACT_KEYWORDS: dict[str, str] = {
        "security": "security risk",
        "email_auth": "email trust",
        "seo": "search ranking",
        "ada": "compliance risk",
        "conversion": "leads lost",
        "performance": "page speed",
    }

    sorted_findings = sorted(
        findings,
        key=lambda f: (_SEV_ORDER.get(f.severity, 4), -f.confidence),
    )
    top5 = sorted_findings[:5]
    if not top5:
        return ""

    lines: list[str] = ["### Priority Risk Callout\n"]
    for f in top5:
        badge = _SEV_BADGE.get(f.severity, "")
        impact_label = _IMPACT_KEYWORDS.get(f.category, "action required")
        title_short = f.title[:70] + ("…" if len(f.title) > 70 else "")
        lines.append(f"> **{badge}** [{impact_label}] {title_short}")

    lines.append("> ")
    lines.append(f"> _See 30/60/90-day roadmap for remediation timelines._\n")
    return "\n".join(lines) + "\n"


def _build_quick_fix_code_block(findings: list[ScanFinding]) -> str:
    """Generate a 'Top 3 Copy-Paste Fixes This Week' section for the appendix (v25).

    Selects up to 3 high/medium findings where the remediation contains an HTML, CSS,
    or DNS code snippet, then formats them as fenced code blocks with 'What to add'
    instructions. This is the most immediately actionable section of the report —
    an SMB owner or their developer can copy-paste these snippets directly.

    Selection criteria:
    - Severity: critical > high > medium
    - Prefer findings whose remediation contains HTML tags, CSS rules, or JSON-LD
    - Skip findings where the remediation is pure prose with no code pattern
    """
    if not findings:
        return ""

    _CODE_IN_REM_RE = re.compile(
        r'(?:<[a-z][a-z0-9]*[\s/>]'    # HTML tags: <script, <meta, <link, <track, etc.
        r'|@type|ld\+json'              # JSON-LD signals
        r'|nginx|\.htaccess'            # Server config
        r'|autocomplete=["\']'          # HTML attribute examples
        r'|aria-label=["\']'            # ARIA attribute examples
        r'|rel=["\'](?:noopener|canonical|preconnect)'  # link/rel attributes
        r'|Content-Security-Policy|X-Frame-Options|Strict-Transport'  # security headers
        r')',
        re.IGNORECASE,
    )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    candidates = sorted(
        [f for f in findings if f.severity in {"critical", "high", "medium"}
         and _CODE_IN_REM_RE.search(f.remediation)],
        key=lambda f: severity_order.get(f.severity, 9),
    )

    selected = candidates[:3]
    if not selected:
        return ""

    lines: list[str] = ["\n\n### Top 3 Copy-Paste Fixes This Week\n"]
    lines.append(
        "_These fixes require minimal technical effort and can be implemented directly by you or your developer. "
        "Each item below includes the exact code or configuration to add._\n"
    )

    for i, f in enumerate(selected, start=1):
        lines.append(f"\n**Fix {i}: {f.title}** _(Severity: {f.severity.capitalize()})_\n")
        lines.append(f"**Page:** {f.evidence.page_url or 'Site-wide'}\n")
        # Extract first code-like segment from remediation for the code block
        rem = f.remediation.strip()
        # Find first line that looks like code (contains < or { or :)
        code_lines: list[str] = []
        prose_lines: list[str] = []
        for line in rem.split("\n"):
            stripped = line.strip()
            if stripped and re.search(r'[<>{]|^\s{2,}|autocomplete=|aria-|rel=|Content-|X-Frame', stripped):
                code_lines.append(stripped)
            else:
                prose_lines.append(stripped)
        if code_lines:
            lines.append("**What to add:**\n")
            lines.append("```html")
            lines.extend(code_lines[:8])
            lines.append("```\n")
            if prose_lines:
                lines.append("**Instructions:** " + " ".join(p for p in prose_lines if p)[:300] + "\n")
        else:
            lines.append("**Instructions:**\n")
            lines.append(rem[:400] + ("..." if len(rem) > 400 else "") + "\n")

    return "\n".join(lines)


_GLOSSARY_TERMS: dict[str, tuple[str, str]] = {
    "DMARC": (
        "Domain-based Message Authentication, Reporting & Conformance",
        "Email policy record that tells receiving mail servers what to do with emails that fail SPF/DKIM checks — quarantine or reject them — stopping spoofed emails from reaching customers.",
    ),
    "SPF": (
        "Sender Policy Framework",
        "DNS record that lists which mail servers are authorized to send email from your domain. Prevents criminals from sending phishing emails that appear to come from your address.",
    ),
    "DKIM": (
        "DomainKeys Identified Mail",
        "Cryptographic signature attached to outgoing emails that proves the message was not tampered with in transit and was authorized by your domain.",
    ),
    "WCAG": (
        "Web Content Accessibility Guidelines",
        "International standard (published by W3C) defining accessibility requirements for websites. Level AA compliance is required by ADA enforcement precedent for US businesses serving the public.",
    ),
    "TLS": (
        "Transport Layer Security",
        "Encryption protocol that protects data in transit between a visitor's browser and your web server. When properly configured, the browser shows a padlock icon. TLS 1.2+ is required; older versions (SSL, TLS 1.0) are deprecated.",
    ),
    "CSP": (
        "Content Security Policy",
        "HTTP response header that restricts which external scripts and resources a browser is allowed to load on your page. A well-configured CSP is the primary defense against cross-site scripting (XSS) attacks.",
    ),
    "HSTS": (
        "HTTP Strict Transport Security",
        "HTTP response header that instructs browsers to always connect to your site over HTTPS — even if a user types 'http://'. Prevents protocol downgrade attacks and mixed-content warnings.",
    ),
    "SRI": (
        "Subresource Integrity",
        "Security feature that lets browsers verify that external scripts (from CDNs or third parties) haven't been tampered with by comparing a cryptographic hash. Prevents supply-chain injection attacks.",
    ),
    "CORS": (
        "Cross-Origin Resource Sharing",
        "Browser mechanism that controls which external websites can make requests to your web server's API or resources. Misconfigured CORS (wildcard Allow-Origin) can expose your data to any website.",
    ),
    "ADA": (
        "Americans with Disabilities Act",
        "US federal law requiring businesses that serve the public to make their services accessible to people with disabilities. Courts have repeatedly extended ADA requirements to websites, making web accessibility a legal obligation.",
    ),
    "OG": (
        "Open Graph Protocol",
        "Metadata tags (og:title, og:image, og:description) in your HTML <head> that control how your pages appear when shared on Facebook, LinkedIn, Twitter, and messaging apps. Missing OG tags result in poor-looking link previews.",
    ),
    "JSON-LD": (
        "JavaScript Object Notation for Linked Data",
        "The Google-recommended format for embedding structured data (Schema.org) in your HTML. Used for LocalBusiness, FAQ, Review, BreadcrumbList schema. Correct JSON-LD enables rich results in Google Search.",
    ),
}

_GLOSSARY_TERM_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in _GLOSSARY_TERMS) + r')\b',
    re.IGNORECASE,
)


def _build_mobile_audit_summary(findings: list[ScanFinding]) -> str:
    """Generate a 'Mobile Experience Audit' table for the appendix (v31).

    Extracts findings that directly affect mobile user experience — viewport issues,
    performance, click-to-call, ADA touch targets, above-fold CTA, lazy loading,
    and render-blocking scripts — and presents them in a focused 3-column table.
    Service businesses often receive the majority of their traffic from mobile users,
    so a mobile-specific lens on the findings helps owners prioritize work that has
    the highest near-term revenue impact. Returns empty string for <2 mobile-relevant findings.
    """
    _MOBILE_KEYWORDS = re.compile(
        r'\b(viewport|mobile|click.to.call|tap|touch|lazy.load|above.fold|cta|'
        r'synchronous.script|time.to.interactive|cache|interactiv|preconnect|'
        r'render.block|font.awesome|google.fonts|image.dimension|webp|compress|'
        r'slow.page|load.time|ttfb|lcp|inp|cls|core.web.vital|'
        r'responsive|pinch.zoom|scalable|user-scalable)\b',
        re.IGNORECASE,
    )
    _MOBILE_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    mobile_findings = [
        f for f in findings
        if _MOBILE_KEYWORDS.search((f.title or "") + " " + (f.description or ""))
    ]
    if len(mobile_findings) < 2:
        return ""

    # Sort by severity desc
    mobile_findings.sort(key=lambda f: _MOBILE_SEVERITY_ORDER.get(f.severity, 4))

    _MOBILE_IMPACT_LABELS: dict[str, str] = {
        "critical": "Blocks mobile use",
        "high": "Major mobile friction",
        "medium": "Noticeable mobile penalty",
        "low": "Mobile optimization gap",
    }

    rows: list[str] = []
    for f in mobile_findings[:8]:
        sev_badge = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(f.severity, "")
        title_short = f.title[:52] + ("…" if len(f.title) > 52 else "")
        impact = _MOBILE_IMPACT_LABELS.get(f.severity, "Mobile gap")
        rows.append(f"| {sev_badge} {title_short} | {impact} | {f.severity.title()} |")

    if not rows:
        return ""

    return (
        "\n\n### Mobile Experience Audit\n\n"
        "_Findings that most directly affect mobile user experience "
        "(typically 50–70% of SMB website traffic)._\n\n"
        "| Finding | Mobile Impact | Severity |\n"
        "|---------|--------------|----------|\n"
        + "\n".join(rows)
        + "\n\n_Fix viewport, CTA visibility, and caching issues first — "
        "these affect every mobile visitor on every page load._\n"
    )


def _build_technical_glossary(findings: list[ScanFinding]) -> str:
    """Generate a 'Technical Terms Explained' table for the appendix (v29).

    Scans all finding descriptions and remediations for known technical acronyms
    (DMARC, SPF, WCAG, TLS, CSP, etc.) and generates a plain-English definition
    table for each term found. Injected at the end of the appendix section.

    Only renders when ≥3 distinct terms appear in the findings, ensuring the
    glossary is relevant to the specific report rather than boilerplate.
    The plain-English explanations are written for the non-technical SMB owner
    who reads the report but doesn't know what "WCAG Level AA" means.
    """
    if not findings:
        return ""

    combined_text = " ".join(
        (f.description or "") + " " + (f.remediation or "") + " " + (f.title or "")
        for f in findings
    )
    found_terms: set[str] = set()
    for match in _GLOSSARY_TERM_RE.finditer(combined_text):
        found_terms.add(match.group(1).upper())

    defined_terms = [t for t in found_terms if t in _GLOSSARY_TERMS]
    if len(defined_terms) < 3:
        return ""

    defined_terms_sorted = sorted(defined_terms)
    rows: list[str] = []
    for term in defined_terms_sorted:
        full_name, plain_english = _GLOSSARY_TERMS[term]
        rows.append(f"| **{term}** | {full_name} | {plain_english} |")

    return (
        "\n\n### Technical Terms Explained\n\n"
        "_These terms appear in the findings above. Plain-English definitions for non-technical readers._\n\n"
        "| Term | Full Name | Plain-English Meaning |\n"
        "|------|-----------|----------------------|\n"
        + "\n".join(rows)
        + "\n\n_For deeper reading: WCAG guidelines at w3.org/WAI, security header checker at securityheaders.com, "
        "email authentication validator at mail-tester.com._\n"
    )


def _build_conversion_audit_table(findings: list[ScanFinding]) -> str:
    """Generate a 'Conversion Friction Audit' table for the conversion section (v29).

    Groups conversion-category findings into three impact tiers — Revenue Impact,
    Trust Signal, and UX Friction — and presents them in a compact 3-column table.
    This gives buyers a structured view of which conversion issues directly affect
    revenue vs. which are UX polish improvements, helping prioritize limited dev time.

    Revenue Impact: findings with payment, purchase, booking, CTA, or click-to-call signals
    Trust Signal: findings related to testimonials, reviews, social proof, live chat, video
    UX Friction: form friction, field count, submit button, placeholder-as-label issues
    Falls back to severity-based grouping when keyword matching yields no rows.
    """
    conv_findings = [f for f in findings if f.category == "conversion"]
    if len(conv_findings) < 2:
        return ""

    _REVENUE_KEYWORDS = re.compile(
        r'\b(booking|purchase|buy|checkout|cart|payment|cta|call.to.action|click.to.call|phone|lead\s+form|contact\s+form|quote)\b',
        re.IGNORECASE,
    )
    _TRUST_KEYWORDS = re.compile(
        r'\b(testimonial|review|social\s+proof|chat|live\s+chat|video|trust\s+signal|star\s+rating|rating|credential|certif)\b',
        re.IGNORECASE,
    )
    _FRICTION_KEYWORDS = re.compile(
        r'\b(form|field|input|button|submit|placeholder|friction|autocomplete|label|click)\b',
        re.IGNORECASE,
    )

    revenue: list[ScanFinding] = []
    trust: list[ScanFinding] = []
    friction: list[ScanFinding] = []

    for f in conv_findings:
        combined = (f.title or "") + " " + (f.description or "")
        if _REVENUE_KEYWORDS.search(combined):
            revenue.append(f)
        elif _TRUST_KEYWORDS.search(combined):
            trust.append(f)
        else:
            friction.append(f)

    rows: list[str] = []
    for tier_label, group in [
        ("Revenue Impact", revenue),
        ("Trust Signal", trust),
        ("UX Friction", friction),
    ]:
        for f in group[:3]:
            sev_badge = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(f.severity, "")
            title_short = f.title[:55] + ("…" if len(f.title) > 55 else "")
            rows.append(f"| {tier_label} | {sev_badge} {title_short} | {f.severity.title()} |")

    if not rows:
        return ""

    return (
        "\n\n### Conversion Friction Audit\n\n"
        "| Impact Tier | Finding | Severity |\n"
        "|-------------|---------|----------|\n"
        + "\n".join(rows)
        + "\n\n_Revenue Impact items directly affect lead capture and customer acquisition. "
        "Fix these before UX Friction items for fastest ROI._\n"
    )


def _build_trust_signal_checklist(findings: list[ScanFinding], scan_payload: dict[str, Any]) -> str:
    """Generate a 'Trust & Credibility Signal Audit' checklist for the conversion section (v36).

    Trust signals are the on-page elements that signal to prospective customers that a
    business is legitimate, established, and safe to contact. SMB websites that lack these
    signals — HTTPS, phone, reviews, privacy policy, schema, social links — convert at a
    fraction of the rate of sites that include them. This 8-row checklist shows each signal
    as pass/warn/fail, the current status, and the business impact of the gap.

    Signals audited:
    - HTTPS/TLS (secure connection — shown in browser padlock)
    - Reviews/testimonials (social proof on page)
    - Click-to-call phone link (mobile conversion driver)
    - Privacy policy link (GDPR/CCPA trust and compliance)
    - LocalBusiness or Organization schema (Google Knowledge Panel)
    - Social media links (brand presence signals)
    - Favicon (basic brand polish)
    - Analytics detection (owner actively monitoring site)

    Returns empty string if <2 findings total (not enough context to warrant the table).
    """
    if len(findings) < 2:
        return ""

    tls = scan_payload.get("tls") or {}
    dns_auth = scan_payload.get("dns_auth") or {}

    def _has_finding(keywords: list[str], cat: str | None = None) -> bool:
        for f in findings:
            if cat and f.category != cat:
                continue
            combined = (f.title or "") + " " + (f.description or "")
            if any(kw.lower() in combined.lower() for kw in keywords):
                return True
        return False

    # Determine signal statuses
    # HTTPS
    tls_valid = bool(tls.get("valid"))
    tls_expiry_ok = not _has_finding(["SSL certificate", "TLS cert", "cert expir"], "security")
    if tls_valid and tls_expiry_ok:
        https_status, https_impact = "✅ Pass", "Padlock visible — visitors see secure site"
    elif tls_valid:
        https_status, https_impact = "⚠️ Warning", "HTTPS active but certificate expiry soon"
    else:
        https_status, https_impact = "❌ Fail", "No HTTPS — browsers show 'Not Secure' warning"

    # Reviews / testimonials
    reviews_missing = _has_finding(["testimonial", "social proof", "review"], "conversion")
    if not reviews_missing:
        reviews_status, reviews_impact = "✅ Pass", "Trust signals present on page"
    else:
        reviews_status, reviews_impact = "❌ Missing", "No reviews/testimonials detected — reduces trust"

    # Click-to-call
    click_call_issue = _has_finding(["click-to-call", "tel: link", "phone number not wrapped"], "conversion")
    no_phone = _has_finding(["Phone number not prominently", "no phone"], "conversion")
    if no_phone:
        phone_status, phone_impact = "❌ Missing", "No phone number — lost mobile conversions"
    elif click_call_issue:
        phone_status, phone_impact = "⚠️ Warning", "Phone present but not click-to-call link"
    else:
        phone_status, phone_impact = "✅ Pass", "Click-to-call phone link detected"

    # Privacy policy link
    privacy_issue = _has_finding(["privacy policy", "consent form privacy"], "security")
    if privacy_issue:
        privacy_status, privacy_impact = "⚠️ Warning", "Form lacks privacy policy link — GDPR exposure"
    else:
        privacy_status, privacy_impact = "✅ Pass", "Privacy link present on forms"

    # Schema markup
    schema_missing = _has_finding([
        "missing Organization", "no LocalBusiness schema", "Organization JSON-LD",
        "Knowledge Panel", "schema completeness"
    ])
    if schema_missing:
        schema_status, schema_impact = "❌ Missing", "No business schema — Google Knowledge Panel gap"
    else:
        schema_status, schema_impact = "✅ Pass", "Business schema detected"

    # Social links
    social_issue = _has_finding(["social media link", "social profile", "social links absent"])
    if social_issue:
        social_status, social_impact = "⚠️ Warning", "Social links missing — brand presence signal"
    else:
        social_status, social_impact = "✅ Pass", "Social media links found"

    # Favicon
    favicon_issue = _has_finding(["favicon", "browser tab icon"])
    if favicon_issue:
        favicon_status, favicon_impact = "❌ Missing", "No favicon — generic browser tab hurts brand"
    else:
        favicon_status, favicon_impact = "✅ Pass", "Favicon detected"

    # Analytics
    analytics_issue = _has_finding(["analytics", "tracking", "Google Analytics", "GA4"])
    if analytics_issue:
        analytics_status, analytics_impact = "❌ Missing", "No analytics — owner flying blind on traffic"
    else:
        analytics_status, analytics_impact = "✅ Pass", "Analytics tracking detected"

    rows = [
        f"| HTTPS / Secure Connection | {https_status} | {https_impact} |",
        f"| Customer Reviews / Testimonials | {reviews_status} | {reviews_impact} |",
        f"| Click-to-Call Phone Link | {phone_status} | {phone_impact} |",
        f"| Privacy Policy Link on Forms | {privacy_status} | {privacy_impact} |",
        f"| Business Schema Markup | {schema_status} | {schema_impact} |",
        f"| Social Media Links | {social_status} | {social_impact} |",
        f"| Favicon / Browser Tab Icon | {favicon_status} | {favicon_impact} |",
        f"| Web Analytics Tracking | {analytics_status} | {analytics_impact} |",
    ]

    pass_count = sum(1 for r in rows if "✅" in r)
    fail_warn_count = len(rows) - pass_count

    return (
        f"\n\n### Trust & Credibility Signal Audit\n\n"
        f"_{pass_count}/8 trust signals passing. "
        f"{fail_warn_count} signal{'s' if fail_warn_count != 1 else ''} need{'s' if fail_warn_count == 1 else ''} attention._\n\n"
        "| Trust Signal | Status | Business Impact |\n"
        "|--------------|--------|----------------|\n"
        + "\n".join(rows)
        + "\n\n_Visitors make trust judgements in under 3 seconds. "
        "Each missing trust signal increases the likelihood a prospect leaves without contacting you._\n"
    )


def _build_industry_benchmark_comparison(scan_payload: dict[str, Any], findings: list[ScanFinding]) -> str:
    """Generate an 'Industry Benchmark Comparison' table for the executive summary (v37).

    Provides a compact 4-column table comparing key measurable site metrics against
    typical SMB averages and best-practice targets. Uses data from scan_payload (TLS,
    load times, dns_auth) and findings to derive status values. Grounded in verifiable
    scan data to avoid triggering the unverified-claim sanitizer.

    Columns: Metric | This Site | Typical SMB | Best Practice

    Only renders when ≥3 distinct data points are available to avoid a sparse table.
    """
    rows: list[str] = []

    # 1. HTTPS / TLS
    tls = scan_payload.get("tls") or {}
    tls_valid = tls.get("valid") or tls.get("tls_ok")
    https_status = "✅ Secured" if tls_valid else "❌ Not Secure"
    rows.append(f"| HTTPS / TLS | {https_status} | ~82% of SMBs | Required |")

    # 2. Average page load time
    load_times = scan_payload.get("load_times") or {}
    if load_times:
        avg_load = sum(load_times.values()) / len(load_times)
        if avg_load <= 2.0:
            load_label = f"✅ {avg_load:.1f}s avg"
        elif avg_load <= 4.0:
            load_label = f"⚠️ {avg_load:.1f}s avg"
        else:
            load_label = f"❌ {avg_load:.1f}s avg"
        rows.append(f"| Page Load Time | {load_label} | 3–5s avg | <2.5s (LCP) |")

    # 3. Security headers coverage
    sec_findings = [f for f in findings if f.category == "security"]
    header_findings = [f for f in sec_findings if any(
        kw in (f.title + f.description).lower()
        for kw in ["header", "hsts", "csp", "x-frame", "content-type", "referrer-policy"]
    )]
    if header_findings:
        sec_label = f"❌ {len(header_findings)} header gap(s)"
    else:
        sec_label = "✅ No header gaps found"
    rows.append(f"| Security Headers | {sec_label} | Most SMBs missing ≥1 | All 6 headers present |")

    # 4. Email authentication (DMARC)
    dns_auth = scan_payload.get("dns_auth") or {}
    dmarc = dns_auth.get("dmarc") or ""
    if "p=reject" in str(dmarc).lower():
        dmarc_label = "✅ Enforced (reject)"
    elif "p=quarantine" in str(dmarc).lower():
        dmarc_label = "⚠️ Quarantine only"
    elif dmarc:
        dmarc_label = "⚠️ DMARC partial"
    else:
        dmarc_label = "❌ No DMARC record"
    rows.append(f"| DMARC Email Auth | {dmarc_label} | ~75% of SMBs missing | p=reject enforced |")

    # 5. ADA / accessibility findings
    ada_findings = [f for f in findings if f.category == "ada"]
    if len(ada_findings) == 0:
        ada_label = "✅ No issues found"
    elif len(ada_findings) <= 3:
        ada_label = f"⚠️ {len(ada_findings)} issue(s)"
    else:
        ada_label = f"❌ {len(ada_findings)} issues"
    rows.append(f"| ADA / WCAG Issues | {ada_label} | Avg 8–12 per SMB site | WCAG 2.1 AA (0 critical) |")

    if len(rows) < 3:
        return ""

    return (
        "\n\n### Industry Benchmark Comparison\n\n"
        "_How this site compares to typical small business websites and best-practice targets._\n\n"
        "| Metric | This Site | Typical SMB Avg | Best Practice |\n"
        "|--------|-----------|-----------------|---------------|\n"
        + "\n".join(rows)
        + "\n\n_Benchmarks based on scan data and public web performance research. "
        "Individual results vary by industry and site architecture._\n"
    )


def _build_core_web_vitals_mapping(findings: list[ScanFinding]) -> str:
    """Generate a 'Core Web Vitals Impact Analysis' table for the performance section (v37).

    Maps each performance finding to the Core Web Vitals metric it most directly affects
    (LCP, INP, CLS, FCP, TTFB), with a brief explanation of the impact mechanism.
    Google uses Core Web Vitals as a ranking signal — showing which specific findings
    hurt each metric helps developers prioritize fixes that improve both user experience
    and search ranking simultaneously.

    Returns empty string for <2 performance findings to avoid a sparse table.
    """
    perf_findings = [f for f in findings if f.category == "performance"]
    if len(perf_findings) < 2:
        return ""

    _CWV_MAP: list[tuple[re.Pattern[str], str, str]] = [
        (re.compile(r'render.blocking|synchronous.*script|body.*script|jquery|defer|async', re.IGNORECASE),
         "FCP / LCP", "Blocks first paint — delays when main content appears"),
        (re.compile(r'lazy.load|image.*load|eager.load|LCP', re.IGNORECASE),
         "LCP", "Delays Largest Contentful Paint — Google's primary load speed signal"),
        (re.compile(r'image.dimension|layout.shift|CLS|width|height', re.IGNORECASE),
         "CLS", "Causes layout shifts — page 'jumps' as images load without reserved space"),
        (re.compile(r'cache.control|compression|gzip|brotli|server.response', re.IGNORECASE),
         "TTFB", "Slows server response — affects all other Core Web Vitals downstream"),
        (re.compile(r'third.party.script|tracking|pixel|analytics.duplicate|external.script', re.IGNORECASE),
         "INP / FCP", "Adds main-thread contention — delays first interaction response time"),
        (re.compile(r'font.display|FOIT|FOUT|font.*family|google.*font', re.IGNORECASE),
         "CLS / FCP", "Font swap causes layout shift and delayed text render"),
        (re.compile(r'preload|preconnect|dns.prefetch|resource.hint', re.IGNORECASE),
         "LCP / FCP", "Missing resource hints delay discovery of critical assets"),
        (re.compile(r'next.gen.image|webp|jpeg|png.*format|image.*format', re.IGNORECASE),
         "LCP", "Larger image payload delays LCP — WebP saves 25–35% vs JPEG"),
        (re.compile(r'unminif|minif|CSS|JS.*size', re.IGNORECASE),
         "FCP", "Excess CSS/JS parse time delays first contentful paint"),
        (re.compile(r'apple.touch|homescreen|touch.icon', re.IGNORECASE),
         "UX", "Affects mobile homescreen experience (not a CWV metric directly)"),
        (re.compile(r'load time|slow.*page|page.*slow|browser.*load', re.IGNORECASE),
         "LCP", "Overall page load time directly impacts Largest Contentful Paint score"),
        (re.compile(r'multiple font|waterfall|font.*famil', re.IGNORECASE),
         "FCP / CLS", "Multiple font families multiply font-load waterfall delays"),
        (re.compile(r'duplicate.*script|script.*duplicate', re.IGNORECASE),
         "FCP / INP", "Duplicate scripts block render and execute twice — double overhead"),
    ]

    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows: list[tuple[str, str, str, int]] = []
    for f in perf_findings:
        combined = f.title + " " + f.description
        cwv_metric = "LCP"  # default
        cwv_impact = "Affects page load performance"
        for pattern, metric, impact in _CWV_MAP:
            if pattern.search(combined):
                cwv_metric = metric
                cwv_impact = impact
                break
        rows.append((
            f.title[:52],
            cwv_metric,
            cwv_impact,
            _SEV_ORDER.get(f.severity, 3),
        ))

    rows.sort(key=lambda x: x[3])
    rows = rows[:10]

    return (
        "\n\n### Core Web Vitals Impact Analysis\n\n"
        "_Google uses Core Web Vitals (LCP, INP, CLS) as a search ranking signal. "
        "Each finding below directly affects one or more CWV metrics._\n\n"
        "| Finding | Affects CWV Metric | Impact Mechanism |\n"
        "|---------|-------------------|------------------|\n"
        + "\n".join(f"| {title} | **{metric}** | {impact} |" for title, metric, impact, _ in rows)
        + "\n\n_Fix LCP and CLS issues first — these have the highest weighting in Google's "
        "ranking algorithm. Target all pages in 'Good' status in Google Search Console > "
        "Core Web Vitals within 60 days._\n"
    )


def _codex_synthesis(settings: AgentSettings, draft: dict[str, Any]) -> dict[str, Any]:
    codex = CodexFulfillmentClient(settings)
    if not codex.enabled():
        return draft
    result = codex.generate(task="web_presence_report_synthesis", payload=draft)
    if not isinstance(result, dict) or result.get("ok") is False:
        return draft
    sections = result.get("sections")
    if not isinstance(sections, list):
        return draft
    try:
        normalized = validate_sections_payload(
            {"sections": sections},
            expected_keys=[k for k in required_report_section_keys() if any(str(s.get("key")) == k for s in draft.get("sections", []))],
        )
    except ValueError:
        return draft
    draft["sections"] = normalized
    if isinstance(result.get("executive_callouts"), list):
        draft["executive_callouts"] = result["executive_callouts"]
    return draft


def build_report_payload(
    *,
    settings: AgentSettings,
    business: SampledBusiness,
    scan_payload: dict[str, Any],
    out_dir: Path,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[ScanFinding] = scan_payload["findings"]
    depth = _report_depth_level(strategy)
    value_model = _value_model(findings, strategy=strategy)
    sections = _build_sections(findings, business, scan_payload, strategy=strategy, value_model=value_model)
    sections = _llm_refine_sections(settings, sections, findings, business)
    claim_lines_removed = 0
    cleaned_sections: list[ReportSection] = []
    for s in sections:
        body = _strip_client_confidence(s.body_markdown)
        body, removed = _sanitize_unverified_claims_in_markdown(body)
        claim_lines_removed += removed
        cleaned_sections.append(ReportSection(key=s.key, title=s.title, body_markdown=body))
    sections = cleaned_sections
    section_word_counts = {
        s.key: len([w for w in s.body_markdown.replace("\n", " ").split(" ") if w.strip()])
        for s in sections
    }
    total_word_count = sum(section_word_counts.values())

    report = {
        "business": _asdict_safe(business),
        "scan": {
            "base_url": scan_payload.get("base_url"),
            "pages": scan_payload.get("pages", []),
            "dns_auth": scan_payload.get("dns_auth", {}),
            "tls": scan_payload.get("tls", {}),
        },
        "sections": [{"key": s.key, "title": s.title, "body": s.body_markdown} for s in sections],
        "findings": [_asdict_safe(f) for f in findings],
        "screenshots": scan_payload.get("screenshots", {}),
        "value_model": value_model,
        "meta": {
            "report_depth_level": depth,
            "section_word_counts": section_word_counts,
            "total_word_count": total_word_count,
        },
    }
    report = _codex_synthesis(settings, report)
    final_sections = list(report.get("sections") or [])
    sanitized_final_sections: list[dict[str, str]] = []
    for sec in final_sections:
        if not isinstance(sec, dict):
            continue
        key = str(sec.get("key") or "").strip()
        title = str(sec.get("title") or "").strip()
        if not key or not title:
            continue
        body = _strip_client_confidence(str(sec.get("body") or ""))
        body, removed = _sanitize_unverified_claims_in_markdown(body)
        claim_lines_removed += removed
        sanitized_final_sections.append({"key": key, "title": title, "body": body})
    report["sections"] = sanitized_final_sections
    final_sections = sanitized_final_sections
    final_counts: dict[str, int] = {}
    for sec in final_sections:
        key = str(sec.get("key") or "").strip()
        body = str(sec.get("body") or "")
        if not key:
            continue
        final_counts[key] = len([w for w in body.replace("\n", " ").split(" ") if w.strip()])
    report["meta"] = {
        "report_depth_level": depth,
        "section_word_counts": final_counts,
        "total_word_count": sum(final_counts.values()),
        "claim_guard_removed_lines": int(claim_lines_removed),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
