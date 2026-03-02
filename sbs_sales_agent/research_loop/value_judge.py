from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .types import ReportScore, ScanFinding, validate_report_score

# Detect remediations and descriptions that cite security/accessibility standards by name.
# Findings that reference specific standards (OWASP, WCAG criterion numbers, CVE IDs, CWE IDs,
# CVSS) are more trustworthy to technical evaluators and compliance-focused buyers: they signal
# that findings are grounded in publicly documented vulnerability research, not just heuristics.
_OWASP_CVSS_RE = re.compile(
    r'\b(?:OWASP|CWE-\d+|CVE-\d{4}-\d+|CVSS|WCAG\s+\d+\.\d+|ISO\s+27001|NIST|RFC\s+\d{4,})\b',
    re.IGNORECASE,
)

# Detect remediations that describe quick, low-effort fixes — "add", "enable", "update X to Y",
# "use free", "install", "activate", "turn on". These contrast with heavy refactors ("rebuild",
# "redesign", "migrate", "rewrite"). Reports with many quick-win remediations are more immediately
# actionable and have higher perceived value to the SMB owner reading on a Sunday evening.
_QUICK_WIN_RE = re.compile(
    r'\b(?:add\s+(?:a\s+|the\s+)?|enable\s+|update\s+(?:the\s+)?|install\s+|activate\s+'
    r'|turn\s+on\s+|use\s+(?:the\s+)?free\s+|configure\s+|include\s+|set\s+(?:the\s+)?)\b',
    re.IGNORECASE,
)
_HEAVY_REFACTOR_RE = re.compile(
    r'\b(?:rebuild|redesign|migrate\s+(?:your|the)|rewrite|overhaul|re-architect)\b',
    re.IGNORECASE,
)

# Detect remediations that include concrete code/config examples — the most immediately
# actionable form of guidance. Patterns: HTML tags, server directives, CLI commands, SRI tools.
_CODE_EXAMPLE_RE = re.compile(
    r"(?:<[a-z][a-z0-9]*[\s>]"        # HTML opening tag: <script, <main, <meta, <iframe
    r"|nginx\.conf|\.htaccess|httpd\.conf|php\.ini"  # config file references
    r"|\bserver_tokens\b|\bgzip\s+on\b|\bmod_deflate\b"  # nginx/Apache directives
    r"|\bapp\.disable\(|helmet\.js"    # Node.js/Express
    r"|\bopenssl\s+dgst\b|srihash\.org"  # CLI / SRI tools
    r"|\bnpm\s+install\b|\bapt-get\b"  # package managers
    r")",
    re.IGNORECASE,
)

# Detect remediations that cite concrete tool names — tools like certbot, Cloudflare,
# Google Search Console, and specific config files signal that the guidance is actionable
# and immediately usable rather than generic advice. Tool-cited remediations are more
# convincing to technical operators and owners with existing tooling familiarity.
_TOOL_CITATION_RE = re.compile(
    r'\b(?:certbot|cloudflare|lets?\s+encrypt|google\s+search\s+console|'
    r'axe(?:\s+devtools?|\s+core)?|securityheaders\.com|ssllabs\.com|mxtoolbox|'
    r'gtmetrix|pagespeed\s+insights|screaming\s+frog|lighthouse|webpagetest|'
    r'ga4|google\s+analytics|elementor|wix|squarespace|wordpress|yoast|'
    r'nginx\.conf|\.htaccess|httpd\.conf|rich\s+results\s+test|jsonlint|'
    r'mail-tester\.com|google\s+business\s+profile|search\s+console)\b',
    re.IGNORECASE,
)

# Minimum findings expected per category at base strategy
_BASE_MIN_FINDINGS: dict[str, int] = {
    "security": 2,
    "email_auth": 1,
    "seo": 3,
    "ada": 1,
    "conversion": 2,
}


def _count_by_category(findings: list[ScanFinding]) -> Counter[str]:
    c: Counter[str] = Counter()
    for f in findings:
        c[f.category] += 1
    return c


def evaluate_report(
    *,
    findings: list[ScanFinding],
    pdf_info: dict[str, Any],
    min_findings: dict[str, int] | None = None,
    min_per_category: dict[str, int] | None = None,
) -> ReportScore:
    # Accept either parameter name for backwards compatibility
    if min_findings is None and min_per_category is None:
        min_findings = {}
    elif min_findings is None:
        min_findings = min_per_category or {}
    reasons: list[str] = []
    counts = _count_by_category(findings)
    effective_min = {**_BASE_MIN_FINDINGS, **{k: v for k, v in min_findings.items() if v > 0}}

    # Base scores
    accuracy = 55.0
    value = 55.0
    aesthetic = 50.0

    # --- Cover page bonus (professional first impression) ---
    if bool(pdf_info.get("cover_page_present", False)):
        aesthetic += 5
        value += 2

    # --- Screenshots ---
    screenshot_count = int(pdf_info.get("screenshot_count") or "0")
    if screenshot_count >= 3:
        aesthetic += 12
        value += 8
    elif screenshot_count >= 1:
        aesthetic += 5
        value += 3
        reasons.append("insufficient_screenshots")
    else:
        reasons.append("insufficient_screenshots")

    # --- Charts ---
    chart_count = len(pdf_info.get("chart_paths") or [])
    if chart_count >= 4:
        aesthetic += 16   # full bonus + extra polish for 4-chart comprehensive visual suite
        value += 10
    elif chart_count >= 3:
        aesthetic += 14   # full bonus + extra polish for 3rd data visualization
        value += 8
    elif chart_count >= 2:
        aesthetic += 12
        value += 7
    elif chart_count == 1:
        aesthetic += 5
        reasons.append("insufficient_charts")
    else:
        reasons.append("insufficient_charts")

    # --- Roadmap table hard requirement ---
    roadmap_present = bool(pdf_info.get("roadmap_present", False))
    if roadmap_present:
        value += 6
        accuracy += 4
    else:
        reasons.append("missing_roadmap_table")
        value -= 8
        accuracy -= 8

    # --- Report depth / comprehensiveness ---
    report_word_count = int(pdf_info.get("report_word_count") or 0)
    report_depth_level = max(1, min(5, int(pdf_info.get("report_depth_level") or 1)))
    if report_word_count >= 2400:
        value += 7
        accuracy += 4
    elif report_word_count >= 1800:
        value += 4
        accuracy += 2
    elif report_word_count >= 1300:
        value += 2
    elif report_word_count > 0 and report_word_count < 1000:
        reasons.append("report_too_brief")
        value -= 6
        accuracy -= 4
    if report_depth_level >= 4:
        value += 2
        accuracy += 1

    # --- Critical severity bonus (urgency driver) ---
    critical_count = sum(1 for f in findings if f.severity == "critical")
    if critical_count >= 1:
        value += 5
        accuracy += 2

    # --- High/critical findings ---
    high_count = sum(1 for f in findings if f.severity in {"high", "critical"})
    if high_count >= 5:
        value += 10
    elif high_count >= 3:
        value += 7
    elif high_count >= 1:
        value += 3
    else:
        reasons.append("no_high_urgency_findings")

    # --- Confidence quality ---
    total = len(findings)
    low_confidence = sum(1 for f in findings if float(f.confidence) < 0.70)
    avg_confidence = (sum(float(f.confidence) for f in findings) / float(total)) if total else 0.0
    if avg_confidence >= 0.82:
        accuracy += 4
        value += 2
    elif avg_confidence < 0.72:
        reasons.append("low_confidence_findings")
        accuracy -= 8
        value -= 5
    if total > 0 and low_confidence > max(1, int(total * 0.20)):
        reasons.append(f"too_many_low_confidence_findings:{low_confidence}")
        accuracy -= min(12, low_confidence * 2)
        value -= min(8, low_confidence)

    # --- Evidence snippet and metadata quality ---
    with_snippet = sum(1 for f in findings if f.evidence.snippet and len(str(f.evidence.snippet).strip()) > 20)
    with_metadata = sum(
        1 for f in findings
        if f.evidence.metadata and isinstance(f.evidence.metadata, dict) and f.evidence.metadata
    )
    if total > 0:
        snippet_ratio = with_snippet / total
        meta_ratio = with_metadata / total
        if snippet_ratio >= 0.40:
            accuracy += 6
            value += 3
        elif snippet_ratio >= 0.20:
            accuracy += 3
        if meta_ratio >= 0.50:
            accuracy += 4
        elif meta_ratio >= 0.25:
            accuracy += 2

    # --- High-severity concentration bonus (v20) ---
    # Reports where a meaningful proportion (20–80%) of findings are high/critical demonstrate
    # genuine risk depth rather than volume padding with low-severity notes. The upper bound
    # excludes degenerate cases where nearly all findings are high/critical severity — a real-world
    # report has a natural mix. SMB owners perceive more urgency — and thus higher purchase intent
    # — when a realistic share of findings are clearly impactful.
    if total > 0:
        high_crit_pct = high_count / total
        if 0.30 <= high_crit_pct < 0.90:
            value += 4
            accuracy += 3
        elif 0.20 <= high_crit_pct < 0.90:
            value += 2
            accuracy += 1

    # --- High/critical findings must be evidence-backed with remediation ---
    weak_urgent = 0
    for f in findings:
        if f.severity not in {"high", "critical"}:
            continue
        has_url = bool((f.evidence.page_url or "").startswith("http"))
        has_remediation = bool(f.remediation.strip()) and len(f.remediation.strip()) >= 24
        if not (has_url and has_remediation):
            weak_urgent += 1
    if weak_urgent > 0:
        reasons.append(f"urgent_findings_incomplete:{weak_urgent}")
        accuracy -= min(25, weak_urgent * 6)
        value -= min(18, weak_urgent * 4)

    # --- Deduplicate findings ---
    title_counts = Counter((f.title or "").strip().lower() for f in findings if (f.title or "").strip())
    duplicate_count = sum(max(0, n - 1) for n in title_counts.values() if n > 1)
    if duplicate_count > 0:
        reasons.append(f"duplicate_findings:{duplicate_count}")
        accuracy -= min(15, duplicate_count * 3)
        value -= min(10, duplicate_count * 2)

    # --- Category coverage ---
    required_categories = {"security", "email_auth", "seo", "ada", "conversion"}
    categories_present = {f.category for f in findings}
    missing_categories = required_categories - categories_present
    for cat in missing_categories:
        reasons.append(f"category_absent:{cat}")
        accuracy -= 5
        value -= 5
    # Bonus for full category coverage (all 5 required + performance = comprehensive report)
    if not missing_categories:
        value += 4
        accuracy += 3

    # --- Minimum findings per category ---
    for cat, min_n in effective_min.items():
        actual = counts.get(cat, 0)
        if actual < int(min_n):
            reasons.append(f"min_findings_not_met:{cat}")
            accuracy -= 5
            value -= 3

    # --- Finding type diversity bonus ---
    # Reports where many distinct (category, check-type) pairs appear confirm that the scanner
    # ran a broad set of checks rather than generating N variants of the same check.
    distinct_check_types = len({
        (f.category, (f.title or "")[:40].strip().lower()) for f in findings
    })
    if distinct_check_types >= 15:
        value += 4
        accuracy += 3
    elif distinct_check_types >= 10:
        value += 2
        accuracy += 2
    elif distinct_check_types >= 6:
        value += 1

    # --- Evidence quality ---
    with_url = sum(1 for f in findings if f.evidence.page_url and f.evidence.page_url.startswith("http"))
    with_remediation = sum(1 for f in findings if f.remediation.strip() and len(f.remediation) > 30)
    if findings:
        url_ratio = with_url / len(findings)
        rem_ratio = with_remediation / len(findings)
    else:
        url_ratio = 0.0
        rem_ratio = 0.0

    accuracy += 20 * url_ratio
    accuracy += 10 * rem_ratio

    # --- Detailed remediation quality ---
    # Reward reports where remediations are specific and actionable (> 80 chars), confirming
    # the report delivers implementable guidance rather than vague one-line advice.
    detailed_rem_count = sum(
        1 for f in findings if f.remediation.strip() and len(f.remediation.strip()) > 80
    )
    if findings:
        detailed_rem_ratio = detailed_rem_count / len(findings)
        if detailed_rem_ratio >= 0.50:
            accuracy += 5
            value += 3
        elif detailed_rem_ratio >= 0.30:
            accuracy += 3
            value += 1

    # --- Code example / copy-paste remediation bonus (v18) ---
    # Reward remediations that include actual configuration snippets, HTML examples, or CLI commands.
    # These are the most immediately actionable guidance — a developer can copy-paste the fix directly.
    # This bonus stacks on top of the detailed-remediation bonus to reward both length AND specificity.
    code_example_count = sum(
        1 for f in findings
        if f.remediation.strip() and _CODE_EXAMPLE_RE.search(f.remediation)
    )
    if total > 0:
        code_example_ratio = code_example_count / total
        if code_example_ratio >= 0.35:
            value += 5
            accuracy += 3
        elif code_example_ratio >= 0.20:
            value += 3
            accuracy += 2
        elif code_example_ratio >= 0.10:
            value += 1
            accuracy += 1

    # --- Finding actionability / quick-wins ratio bonus (v19) ---
    # Reports where a high proportion of remediations describe low-effort, immediately executable fixes
    # ("add loading=lazy", "enable gzip", "update the title tag") have higher immediate SMB value than
    # reports dominated by heavy refactor instructions. Quick wins = fast ROI = easier close.
    # Only count findings with non-trivial remediations to avoid false positive on stub text.
    remediations_evaluated = [
        f.remediation.strip() for f in findings
        if f.remediation.strip() and len(f.remediation.strip()) > 30
    ]
    if remediations_evaluated:
        quick_win_count = sum(
            1 for r in remediations_evaluated
            if _QUICK_WIN_RE.search(r) and not _HEAVY_REFACTOR_RE.search(r)
        )
        quick_win_ratio = quick_win_count / len(remediations_evaluated)
        if quick_win_ratio >= 0.50:
            value += 5
            accuracy += 2
        elif quick_win_ratio >= 0.35:
            value += 3
            accuracy += 1
        elif quick_win_ratio >= 0.20:
            value += 1

    # --- Multi-page evidence depth ---
    # Bonus when findings reference multiple distinct pages — confirms scan breadth, not just homepage
    distinct_pages = len({
        f.evidence.page_url for f in findings
        if f.evidence.page_url and str(f.evidence.page_url).startswith("http")
    })
    if distinct_pages >= 4:
        accuracy += 6
        value += 4
    elif distinct_pages >= 2:
        accuracy += 3
        value += 2

    # --- Total finding volume ---
    if total >= 25:
        value += 14
        accuracy += 8
    elif total >= 18:
        value += 10
        accuracy += 5
    elif total >= 12:
        value += 6
    elif total >= 8:
        value += 2
    elif total < 6:
        value -= 10
        reasons.append("too_few_findings")

    # --- Cross-category urgency spread bonus ---
    # Reports where 3+ categories each have at least one high/critical finding confirm
    # multi-dimensional risk — increasing commercial urgency for the business owner.
    # Performance is included (v16): render-blocking scripts, CLS, load latency are genuine
    # business risks that belong in the urgency picture alongside security and SEO.
    _urgency_cats = {"security", "email_auth", "seo", "ada", "conversion", "performance"}
    cats_with_urgent = {
        f.category for f in findings
        if f.severity in {"high", "critical"} and f.category in _urgency_cats
    }
    # v17: add a 5+ tier for reports with maximum urgency spread across all tracked categories
    if len(cats_with_urgent) >= 5:
        value += 8
        accuracy += 5
    elif len(cats_with_urgent) >= 4:
        value += 5
        accuracy += 3
    elif len(cats_with_urgent) >= 3:
        value += 3
        accuracy += 2

    # --- Performance depth bonus (v15) ---
    # Reports that surface 3+ distinct performance findings (render-blocking scripts, browser load
    # timing, lazy loading, image dimensions, payload size) demonstrate deeper technical analysis
    # and are more useful to developers optimising for Core Web Vitals.
    perf_count = sum(1 for f in findings if f.category == "performance")
    if perf_count >= 3:
        value += 3
        accuracy += 2
    elif perf_count >= 2:
        value += 1
        accuracy += 1

    # --- Roadmap bucket coverage bonus ---
    # Reward reports with items in all three time windows (0-30, 31-60, 61-90 days):
    # indicates the report addresses urgent, medium-term, and strategic priorities.
    roadmap_bucket_count = int(pdf_info.get("roadmap_bucket_count") or 0)
    if roadmap_bucket_count >= 3:
        value += 4
        accuracy += 2
    elif roadmap_bucket_count >= 2:
        value += 2

    # --- ROI/value quantification bonus ---
    # Reward reports that include explicit low/base/upside value scenarios tied to payback framing.
    value_model_scenarios = int(pdf_info.get("value_model_scenarios") or 0)
    if value_model_scenarios >= 3:
        value += 4
        accuracy += 2
    elif value_model_scenarios >= 1:
        value += 2

    # --- Commercial viability gate signals ---
    # Reward fast payback and meaningful upside; penalize reports that look hard to justify commercially.
    base_upside = int(pdf_info.get("value_model_base_monthly_upside") or 0)
    base_payback_days = int(pdf_info.get("value_model_base_payback_days") or 0)
    if value_model_scenarios >= 1:
        if base_payback_days > 0:
            if base_payback_days <= 45:
                value += 6
                accuracy += 2
            elif base_payback_days <= 90:
                value += 3
                accuracy += 1
            elif base_payback_days <= 180:
                value -= 4
                reasons.append("weak_commercial_model:slow_payback")
            else:
                value -= 8
                accuracy -= 2
                reasons.append("weak_commercial_model:very_slow_payback")
        if base_upside >= 2500:
            value += 3
            accuracy += 1
        elif 0 < base_upside < 500:
            value -= 5
            reasons.append("weak_commercial_model:low_upside")

    # --- Standards citation quality bonus (v21) ---
    # Reports that reference specific, publicly documented security/accessibility standards
    # (OWASP Top 10, WCAG criterion numbers, CVE IDs, CWE classifications) are perceived as
    # more trustworthy and authoritative by technical buyers and compliance-focused SMB owners.
    # This bonus rewards scans that ground findings in known frameworks rather than vague heuristics.
    if total > 0:
        cited_standards_count = sum(
            1 for f in findings
            if _OWASP_CVSS_RE.search((f.description or "") + " " + (f.remediation or ""))
        )
        cited_standards_ratio = cited_standards_count / total
        if cited_standards_ratio >= 0.25:
            accuracy += 4
            value += 3
        elif cited_standards_ratio >= 0.10:
            accuracy += 2
            value += 1

    # --- Severity calibration bonus (v21) ---
    # A credible, well-calibrated scan produces a natural mix of finding severities — not all low
    # (scanner was too lenient) nor all high/critical (inflation). Reports with 3+ distinct
    # severity levels (e.g. low + medium + high) demonstrate genuine risk stratification and are
    # more trustworthy to developers who will implement fixes based on priority order.
    if total > 0:
        sev_counts = Counter(f.severity for f in findings)
        distinct_sev = sum(1 for k in ("low", "medium", "high", "critical") if sev_counts[k] > 0)
        if total >= 8 and distinct_sev >= 3:
            accuracy += 3
            value += 2
        elif total >= 6 and distinct_sev >= 2:
            accuracy += 1

    # --- Remediation tool citation bonus (v22) ---
    # Reports that name specific, concrete tools in their remediations ("certbot", "Cloudflare",
    # "Google Search Console", "securityheaders.com") are more immediately actionable than generic
    # advice. SMB owners and their developers need to know exactly where to go and what to click.
    # Tool-citing remediations also signal that the analyst knows the actual fix workflow, not just
    # the theory — this strongly differentiates from cheaper/automated reports.
    if total > 0:
        tool_cited_count = sum(
            1 for f in findings
            if f.remediation.strip() and _TOOL_CITATION_RE.search(f.remediation)
        )
        tool_cited_ratio = tool_cited_count / total
        if tool_cited_ratio >= 0.40:
            value += 4
            accuracy += 3
        elif tool_cited_ratio >= 0.20:
            value += 2
            accuracy += 1

    # --- Category breadth bonus (v22) ---
    # A report covering all 6 tracked categories (security, email_auth, seo, ada, conversion,
    # performance) with at least one finding each demonstrates comprehensive analysis rather than
    # single-category depth. Even one finding per category signals that the scan pipeline ran end-
    # to-end and checked all major risk domains — increasing the report's defensibility and perceived
    # completeness to buyers who evaluate thoroughness before purchasing.
    _all_cats = {"security", "email_auth", "seo", "ada", "conversion", "performance"}
    cats_with_any_finding = {f.category for f in findings if f.category in _all_cats}
    if len(cats_with_any_finding) >= 6:
        value += 4
        accuracy += 2
    elif len(cats_with_any_finding) >= 5:
        value += 2
        accuracy += 1

    # --- Finding confidence quality bonus (v23) ---
    # Reports where the scan pipeline assigned high average confidence signal that checks were
    # deterministic and evidence-backed rather than speculative heuristics. A high average
    # confidence (≥0.80) means the majority of findings are immediately actionable without
    # manual developer validation — the $299 report pays for itself faster when the findings
    # don't need to be filtered for false positives before implementation.
    if findings:
        avg_confidence = sum(f.confidence for f in findings) / len(findings)
        if avg_confidence >= 0.80:
            accuracy += 4
            value += 2
        elif avg_confidence >= 0.70:
            accuracy += 2
            value += 1

    # --- Remediation average length quality bonus (v23) ---
    # Distinct from the 80-char detailed remediation ratio bonus: this rewards the cumulative
    # specificity of the entire remediation corpus. When the average remediation across all
    # findings exceeds 200 characters, the report consistently delivers page-by-page, step-by-step
    # guidance rather than terse one-line advice. This is the primary differentiator between a
    # $299 report and a free automated scanner output.
    if findings:
        rem_lengths = [len(f.remediation.strip()) for f in findings if f.remediation.strip()]
        if rem_lengths:
            avg_rem_length = sum(rem_lengths) / len(rem_lengths)
            if avg_rem_length >= 200:
                accuracy += 3
                value += 2
            elif avg_rem_length >= 140:
                accuracy += 2
                value += 1

    # --- Description depth bonus (v24) ---
    # The primary differentiator between a $299 report and a free scanner is description quality:
    # descriptions that explain WHY a finding matters to the business (not just what was detected)
    # and HOW it affects customers. Detailed descriptions reduce the buyer's cognitive work of
    # connecting technical issues to business impact — critical for SMB owners who are not
    # technical. Average description length ≥300 chars signals consistently detailed explanations.
    if findings:
        desc_lengths = [len(f.description.strip()) for f in findings if f.description.strip()]
        if desc_lengths:
            avg_desc_length = sum(desc_lengths) / len(desc_lengths)
            if avg_desc_length >= 300:
                accuracy += 3
                value += 2
            elif avg_desc_length >= 200:
                accuracy += 2
                value += 1

    # --- Evidence richness bonus (v24) ---
    # Reports where findings include BOTH a specific text snippet (the raw evidence extracted
    # from the page) AND structured metadata (key-value pairs with counts, lists, etc.)
    # are the most defensible and immediately actionable. The combination allows the buyer to:
    # (1) verify the finding themselves by searching for the snippet, and (2) quantify the
    # impact via metadata. This dual-evidence pattern is rare in free tools and signals
    # analyst-grade rigor. Rewards scans that collect rich evidence, not just binary presence/absence.
    if total > 0:
        with_full_evidence = sum(
            1 for f in findings
            if (f.evidence.snippet and len(str(f.evidence.snippet).strip()) > 20)
            and (f.evidence.metadata and isinstance(f.evidence.metadata, dict) and f.evidence.metadata)
        )
        full_evidence_ratio = with_full_evidence / total
        if full_evidence_ratio >= 0.35:
            accuracy += 4
            value += 2
        elif full_evidence_ratio >= 0.20:
            accuracy += 2
            value += 1

    # --- Finding outcome language bonus (v25) ---
    # Reports where high/critical findings explicitly articulate business consequences — lost
    # revenue, ranking penalties, compliance lawsuits, customer churn — resonate far more strongly
    # with SMB owners than purely technical descriptions. When ≥30% of high/critical finding
    # descriptions use concrete outcome language, the report reads as a business consultant's
    # deliverable rather than a technical scanner output. This is a core differentiator for
    # justifying the $299 price point to a non-technical owner reading the report on a Sunday.
    _OUTCOME_RE = re.compile(
        r'\b(?:revenue|ranking|penalt(?:y|ies)|lawsuit|litigation|demand\s+letter|customer|'
        r'compliance|enforcement|settlement|conversion|abandonment|churn|lost\s+business|'
        r'google\s+penalt|deindex|organic\s+(?:traffic|ranking)|ADA\s+(?:lawsuit|action))\b',
        re.IGNORECASE,
    )
    high_crit = [f for f in findings if f.severity in ("high", "critical")]
    if high_crit:
        outcome_count = sum(
            1 for f in high_crit
            if _OUTCOME_RE.search(f.description or "")
        )
        outcome_ratio = outcome_count / len(high_crit)
        if outcome_ratio >= 0.30:
            value += 3
            accuracy += 2
        elif outcome_ratio >= 0.15:
            value += 1
            accuracy += 1

    # --- Report section completeness bonus (v25) ---
    # The full 10-section report structure (exec summary, risk dashboard, security, email_auth,
    # ADA, SEO, conversion, competitor context, roadmap, KPI measurement, appendix) signals a
    # professional-grade deliverable. Reports containing kpi_measurement, appendix, AND
    # competitor_context sections have clearly followed the full template and are more likely
    # to satisfy a discerning buyer who would compare the TOC against a $1,500 agency audit.
    # This bonus rewards the builder for not skipping optional sections.
    report_sections = pdf_info.get("sections") or []
    if isinstance(report_sections, list) and report_sections:
        section_names = {str(s).lower() for s in report_sections}
        has_kpi = any("kpi" in s or "measurement" in s or "metric" in s for s in section_names)
        has_appendix = any("appendix" in s for s in section_names)
        has_competitor = any("competitor" in s or "competi" in s or "context" in s for s in section_names)
        completeness_count = sum([has_kpi, has_appendix, has_competitor])
        if completeness_count >= 3:
            value += 3
            accuracy += 2
        elif completeness_count >= 2:
            value += 1
            accuracy += 1

    # --- Page coverage depth bonus (v26) ---
    # Reports where findings reference ≥4 distinct page URLs demonstrate that the scan explored
    # beyond the homepage and uncovered issues across the full customer journey. SMB owners
    # and developers reviewing the report immediately notice when every finding points to "/" —
    # it signals a surface-level scan. Multi-page coverage adds defensibility: if a client
    # disputes a finding, inner-page URL evidence is harder to dismiss than a homepage-only check.
    if total > 0:
        finding_page_urls = {
            str(f.evidence.page_url or "").rstrip("/")
            for f in findings
            if f.evidence.page_url and str(f.evidence.page_url).strip()
        }
        unique_page_count = len(finding_page_urls)
        if unique_page_count >= 4:
            value += 5
            accuracy += 3
        elif unique_page_count >= 2:
            value += 2
            accuracy += 2

    # --- Multi-severity per-category bonus (v26) ---
    # A well-calibrated scan produces a natural severity mix within each category — not all
    # "medium" security findings or all "low" SEO findings. When ≥3 categories each contain
    # findings at 2+ distinct severity levels, it demonstrates granular risk stratification:
    # the scanner didn't flatten everything to one severity band but genuinely assessed each
    # issue on its actual risk level. This is a hallmark of analyst-grade work vs. automated
    # scanner output and directly supports the $299 price justification to a discerning buyer.
    if total > 0:
        from collections import defaultdict
        cat_sevs: dict[str, set[str]] = defaultdict(set)
        for f in findings:
            cat_sevs[f.category].add(f.severity)
        multi_sev_cats = sum(1 for sevs in cat_sevs.values() if len(sevs) >= 2)
        if multi_sev_cats >= 3:
            accuracy += 3
            value += 2
        elif multi_sev_cats >= 2:
            accuracy += 1

    # --- Numeric specificity bonus (v27) ---
    # Reports where findings describe issues with concrete numeric data — load times in milliseconds,
    # page counts, character counts, score values, bytes — are far more persuasive than vague
    # descriptions ("this is slow", "some pages are missing"). Specific numbers give SMB owners
    # and their developers an objective baseline to measure improvement against, and signal that
    # the scan gathered real measurement data rather than applying generic templates.
    # Pattern matches: digits followed by common measurement units or standalone percentage/count values.
    _NUMERIC_DATA_RE = re.compile(
        r'\b\d+(?:\.\d+)?\s*(?:%|ms|s\b|KB|MB|px|pt|em|rem|bps|words?|pages?|items?|fields?|images?|scripts?|nodes?|tags?)\b'
        r'|\b\d{2,}\b(?=\s+(?:pages?|images?|violations?|findings?|issues?|scripts?|fields?))',
        re.IGNORECASE,
    )
    if total > 0:
        numeric_count = sum(
            1 for f in findings
            if _NUMERIC_DATA_RE.search(f.description or "")
        )
        numeric_ratio = numeric_count / total
        if numeric_ratio >= 0.40:
            value += 4
            accuracy += 2
        elif numeric_ratio >= 0.25:
            value += 2
            accuracy += 1

    # --- Remediation specificity bonus (v28) ---
    # Reports whose remediation instructions name concrete technical items — specific HTTP header
    # names, HTML attribute values, DNS record type strings, CSS properties, or config file
    # references — are far more credible and actionable than generic advice ("add security headers",
    # "fix your forms"). A remediation that says "Add the Strict-Transport-Security header with
    # max-age=31536000; includeSubDomains" tells a developer exactly what to type. This bonus
    # rewards the granular technical precision that differentiates analyst-grade reports from
    # automated scanner output and directly supports the $299 price justification.
    _SPECIFIC_TECH_RE = re.compile(
        r'\b(?:'
        r'Strict-Transport-Security|Content-Security-Policy|X-Frame-Options'
        r'|X-Content-Type-Options|Referrer-Policy|Permissions-Policy'
        r'|v=spf1|p=quarantine|p=reject|k=rsa'
        r'|autocomplete=["\']?(?:on|off|email|tel|current-password|new-password)'
        r'|rel=["\']?noopener|dns-prefetch|preconnect'
        r'|prefers-reduced-motion|@keyframes|@media'
        r'|aria-label|aria-labelledby|tabindex=["\']?[0-9]'
        r'|loading=["\']?lazy'
        r'|SameSite|HttpOnly|integrity='
        r'|type=["\']?application/ld\+json|BreadcrumbList|FAQPage|AggregateRating|LocalBusiness'
        r'|max-age=\d|includeSubDomains'
        r'|method=["\']?post|action=["\']?https'
        r')\b',
        re.IGNORECASE,
    )
    if total > 0:
        specific_tech_count = sum(
            1 for f in findings
            if f.remediation.strip() and _SPECIFIC_TECH_RE.search(f.remediation)
        )
        specific_tech_ratio = specific_tech_count / total
        if specific_tech_ratio >= 0.50:
            accuracy += 4
            value += 2
        elif specific_tech_ratio >= 0.30:
            accuracy += 2
            value += 1

    # --- Evidence URL density bonus (v29) ---
    # The strongest form of finding evidence is one where every finding is anchored to a
    # specific page URL — not just a generic mention of "the site". When ≥80% of findings
    # include a non-empty page_url in their evidence, the report is auditable: each finding
    # can be independently verified by the business owner or their developer by simply
    # visiting the URL. This is a direct proxy for scan depth and evidence quality, and
    # signals analyst-grade work rather than domain-level assessments with no page attribution.
    if total > 0:
        with_page_url = sum(
            1 for f in findings
            if f.evidence.page_url and str(f.evidence.page_url).strip()
        )
        url_density_ratio = with_page_url / total
        if url_density_ratio >= 0.80:
            accuracy += 3
            value += 1
        elif url_density_ratio >= 0.60:
            accuracy += 1

    # --- Finding title precision bonus (v29) ---
    # A finding title like "jQuery 1.9.1 detected (CVE-2019-11358)" is far more credible and
    # actionable than "Outdated JavaScript library". Precise titles that include version numbers,
    # counts, URLs, or measurement values reduce the cognitive load for the business owner and
    # their developer — they immediately know the specific issue without reading the full body.
    # This bonus rewards scans that produce titles with embedded specifics rather than generic
    # category-level descriptions.
    _TITLE_SPECIFICITY_RE = re.compile(
        r'(?:\d+\s*(?:ms|s\b|KB|MB|px|%|pages?|instances?|fields?|scripts?|handlers?|urls?|images?)\b'  # numeric + unit
        r'|\b(?:CVE|WCAG|OWASP|CWE|RFC)\s*[-–]?\d+'  # standard citation with number
        r'|v?\d+\.\d+[\.\d]*\b'  # version number like 1.9.1 or v3.2
        r'|`[^`]+`'  # backtick-quoted technical value
        r"|\b\d{4,}\b)",  # 4+ digit standalone number (port, year, count)
        re.IGNORECASE,
    )
    if total > 0:
        precise_title_count = sum(
            1 for f in findings
            if _TITLE_SPECIFICITY_RE.search(f.title or "")
        )
        title_precision_ratio = precise_title_count / total
        if title_precision_ratio >= 0.40:
            value += 3
            accuracy += 2
        elif title_precision_ratio >= 0.25:
            value += 1
            accuracy += 1

    # --- Full category depth bonus (v30) ---
    # When ALL 5 required categories each contain at least 3 findings, the report demonstrates
    # systematic, multi-check depth in every risk domain — not just token coverage. An SMB owner
    # reviewing the table of contents will see 3+ evidence-backed issues per section, which makes
    # each section section feel substantive rather than padded. This bonus stacks with the existing
    # full-category-coverage bonus (all 5 present with ≥1 finding each) to reward both presence
    # AND depth. Reports with 3+ findings in 4+ categories exceed a threshold that free scanners
    # almost never reach, directly justifying the $299 price point.
    _REQUIRED_CATS = {"security", "email_auth", "seo", "ada", "conversion"}
    cats_with_three_plus = {
        cat for cat in _REQUIRED_CATS
        if counts.get(cat, 0) >= 3
    }
    if len(cats_with_three_plus) >= 5:
        value += 5
        accuracy += 3
    elif len(cats_with_three_plus) >= 4:
        value += 3
        accuracy += 2
    elif len(cats_with_three_plus) >= 3:
        value += 1
        accuracy += 1

    # --- Category × severity pair breadth bonus (v30) ---
    # The number of distinct (category, severity) combinations present in a report is a
    # proxy for the granularity and calibration of the scan. A report with findings at
    # security:low + security:medium + security:high + seo:low + seo:medium + ada:medium
    # demonstrates 6 distinct risk strata — signalling to a technical buyer that the scanner
    # does not flatten everything to a single severity band. This bonus rewards reports that
    # produce fine-grained risk stratification across multiple categories simultaneously,
    # differentiating analyst-grade output from one-size-fits-all automated scanners.
    cat_sev_pairs: set[tuple[str, str]] = {(f.category, f.severity) for f in findings}
    pair_count = len(cat_sev_pairs)
    if pair_count >= 12:
        value += 4
        accuracy += 3
    elif pair_count >= 8:
        value += 2
        accuracy += 2
    elif pair_count >= 5:
        value += 1
        accuracy += 1

    # --- Risk narrative quality bonus (v31) ---
    # Finding descriptions that articulate the *consequence* of a vulnerability — what an
    # attacker could do, what ranking impact could result, what compliance penalty is at risk —
    # are dramatically more persuasive to business owners than dry technical observations.
    # "Missing HSTS header" is a fact; "Without HSTS, an attacker on public Wi-Fi can intercept
    # login sessions via SSL stripping" tells a story that motivates action and justifies the
    # $299 price. This bonus rewards reports where a meaningful share of finding descriptions
    # use consequence language, elevating them above automated scanner output.
    _RISK_NARRATIVE_RE = re.compile(
        r'\b(?:could allow|allows attacker|enables|risks?|exposes?|attacke?r|breach|'
        r'penalties?|lawsuit|compliance failure|blocked by google|ranked lower|'
        r'indexing issue|hijack|intercept|spoof|phish|impersonate|misissuance?|'
        r'conversion rate drops?|bounce rate|revenue loss|data leak|unauthorized access|'
        r'silently|session hijack|man.in.the.middle|credentials? harvest)\b',
        re.IGNORECASE,
    )
    if total > 0:
        risk_narrative_count = sum(
            1 for f in findings
            if f.description.strip() and _RISK_NARRATIVE_RE.search(f.description)
        )
        risk_narrative_ratio = risk_narrative_count / total
        if risk_narrative_ratio >= 0.35:
            value += 3
            accuracy += 2
        elif risk_narrative_ratio >= 0.20:
            value += 1
            accuracy += 1

    # --- Remediation URL citation bonus (v31) ---
    # A remediation that includes a specific URL — a tool to validate the fix, official docs,
    # a free online checker, or a configuration reference — tells the reader exactly where to
    # go next. "Run Google's Rich Results Test at https://search.google.com/test/rich-results"
    # is 10× more actionable than "test your structured data". This bonus rewards remediations
    # that close the loop by giving the business owner and their developer a concrete next click.
    # It also signals analyst-grade work — automated scanners never cite specific tool URLs.
    _REMEDIATION_URL_RE = re.compile(r'https?://\S{10,}', re.IGNORECASE)
    if total > 0:
        url_remediation_count = sum(
            1 for f in findings
            if f.remediation.strip() and _REMEDIATION_URL_RE.search(f.remediation)
        )
        url_remediation_ratio = url_remediation_count / total
        if url_remediation_ratio >= 0.20:
            value += 3
            accuracy += 2
        elif url_remediation_ratio >= 0.10:
            value += 1
            accuracy += 1

    # --- Remediation timeframe clarity bonus (v32) ---
    # Remediations that include explicit time/effort estimates ("takes under 5 minutes",
    # "within 24 hours", "2-minute fix") are dramatically more motivating to business owners
    # than open-ended guidance. A specific timeframe signals that the analyst has scoped the
    # work and eliminates the "this will take weeks of developer time" fear that stalls action.
    # Reports where many remediations include effort estimates feel more consultative and
    # immediately actionable — closer to a developer quoting work than a generic scanner output.
    _TIMEFRAME_RE = re.compile(
        r'\b(?:takes?\s+(?:under\s+|about\s+|only\s+)?|in\s+(?:under\s+|less\s+than\s+|about\s+)?'
        r'|within\s+|less\s+than\s+|under\s+)'
        r'(?:\d+\s*[-–]?\s*\d*\s*)?(?:seconds?|minutes?|hours?|days?)\b'
        r'|\b(?:24|48|72)\s*-?\s*hour|\bsame[-\s]day\b|\bno\s+developer\b|\bno\s+coding\b',
        re.IGNORECASE,
    )
    if total > 0:
        timeframe_count = sum(
            1 for f in findings
            if f.remediation.strip() and _TIMEFRAME_RE.search(f.remediation)
        )
        timeframe_ratio = timeframe_count / total
        if timeframe_ratio >= 0.30:
            value += 3
            accuracy += 2
        elif timeframe_ratio >= 0.15:
            value += 1
            accuracy += 1

    # --- Remediation effort distinction bonus (v32) ---
    # Remediations that explicitly distinguish skill tier — flagging whether a fix requires
    # a developer, a CMS admin, a plugin install, or just a WordPress settings change — are
    # more immediately useful to non-technical SMB owners. They reduce the cognitive load of
    # converting a finding into an action item: the owner knows whether to forward it to their
    # developer or handle it themselves. This is a key differentiator between an analyst-written
    # report and an automated scanner, which never contextualises the implementation path.
    _EFFORT_DISTINCTION_RE = re.compile(
        r'\b(?:no\s+(?:developer|coding|backend|server\s+access|programming)'
        r'|requires?\s+(?:a\s+)?(?:developer|server|backend)\s+(?:access|change|update|config)'
        r'|in\s+(?:WordPress|Elementor|Wix|Squarespace|Shopify|HubSpot|cPanel)\s*:'
        r'|plugin\s+install|CMS\s+(?:admin|setting|plugin)'
        r'|ask\s+your\s+(?:developer|hosting\s+provider|web\s+designer))\b',
        re.IGNORECASE,
    )
    if total > 0:
        effort_count = sum(
            1 for f in findings
            if f.remediation.strip() and _EFFORT_DISTINCTION_RE.search(f.remediation)
        )
        effort_ratio = effort_count / total
        if effort_ratio >= 0.35:
            accuracy += 3
            value += 2
        elif effort_ratio >= 0.20:
            accuracy += 1
            value += 1

    # --- Platform specificity bonus (v33) ---
    # Remediations that name specific CMS platforms, hosting dashboards, or plugin paths are
    # dramatically more actionable for SMB owners who use those platforms — they can follow
    # the instructions immediately without translating generic advice. "In WordPress: go to
    # Yoast SEO → Advanced tab" is infinitely more useful than "update your meta robots setting".
    # This bonus rewards analysts who tailor guidance to common SMB website stacks rather than
    # writing generic server-admin instructions that non-technical owners cannot act on.
    _PLATFORM_SPECIFIC_RE = re.compile(
        r'\b(?:wordpress|squarespace|wix|shopify|elementor|divi|beaver\s+builder|'
        r'godaddy|hostgator|bluehost|namecheap|cpanel|plesk|dreamhost|kinsta|siteground|'
        r'plugins?\s+(?:page|editor|settings)|wp-admin|wp-content|yoast|rank\s+math|'
        r'appearance\s*[→>]|settings\s*[→>]\s*(?:general|reading|discussion|permalink)|'
        r'theme\s+editor|page\s+(?:builder|settings)|dashboard\s*[→>])\b',
        re.IGNORECASE,
    )
    if total > 0:
        platform_count = sum(
            1 for f in findings
            if f.remediation.strip() and _PLATFORM_SPECIFIC_RE.search(f.remediation)
        )
        platform_ratio = platform_count / total
        if platform_ratio >= 0.30:
            value += 3
            accuracy += 2
        elif platform_ratio >= 0.15:
            value += 1
            accuracy += 1

    # --- Buyer-centric language bonus (v33) ---
    # Findings written in second-person possessive ("your site", "your visitors", "your customers")
    # signal that the report is addressed to the business owner — not a generic technical output.
    # This increases the perceived personalization of the report and its value as a sales tool.
    # SMB owners respond more to "your visitors cannot tap to call" than "click-to-call links
    # are absent", because the former frames the finding as a direct impact on their business.
    # Reports with consistent buyer-centric language feel like a real consultant's assessment
    # rather than an automated scanner dump, which is a key differentiator at the $299 price.
    _BUYER_CENTRIC_RE = re.compile(
        r'\b(?:your\s+(?:site|website|page|business|visitors?|customers?|clients?|users?|leads?|'
        r'rankings?|revenue|brand|competitors?|forms?|links?|images?|content|domain|server|'
        r'email|score|audit|report|traffic|conversions?|prospects?))\b',
        re.IGNORECASE,
    )
    if total > 0:
        buyer_centric_count = sum(
            1 for f in findings
            if f.description.strip() and _BUYER_CENTRIC_RE.search(f.description)
        )
        buyer_centric_ratio = buyer_centric_count / total
        if buyer_centric_ratio >= 0.40:
            value += 3
            accuracy += 2
        elif buyer_centric_ratio >= 0.25:
            value += 1
            accuracy += 1

    # --- Finding headline impact bonus (v34) ---
    # Finding titles that reference business-outcome language (breach, penalty, ranking,
    # conversion, revenue) are more compelling and credible to SMB owners scanning the
    # report — they immediately connect the technical finding to a business consequence.
    # This differs from description-level outcome language (v25) which checks body text.
    _HEADLINE_IMPACT_RE = re.compile(
        r'\b(?:breach|penalty|penalties|ranking|rankings|conversion|revenue|lawsuit|'
        r'compliance|blocked|exposed|lost|dropped|failed|risk|vulnerable|missing|absent)\b',
        re.IGNORECASE,
    )
    if total > 0:
        headline_impact_count = sum(
            1 for f in findings
            if f.title.strip() and _HEADLINE_IMPACT_RE.search(f.title)
        )
        headline_impact_ratio = headline_impact_count / total
        if headline_impact_ratio >= 0.25:
            value += 3
            accuracy += 2
        elif headline_impact_ratio >= 0.15:
            value += 1
            accuracy += 1

    # --- Structured remediation steps bonus (v34) ---
    # Remediations that include a numbered step sequence, 'Step N:' markers, or a clear
    # imperative chain (Check → Then → Verify) are significantly more actionable than
    # prose paragraphs. This rewards guidance that a non-technical SMB owner can follow
    # as a literal checklist rather than interpreting vague instructions.
    _STEP_SEQUENCE_RE = re.compile(
        r'(?:Step\s+\d+[:\.]|^\d+[\.\)]\s|\bfirst[,:]?\s+\w|\bthen\s+(?:add|enable|set|configure|install|open|navigate|go to|check|verify)\b'
        r'|\bNext[,:]?\s+(?:add|enable|set|configure|install|open)\b|\bFinally[,:]?\s+(?:verify|validate|test|check)\b)',
        re.IGNORECASE | re.MULTILINE,
    )
    if total > 0:
        step_count = sum(
            1 for f in findings
            if f.remediation.strip() and _STEP_SEQUENCE_RE.search(f.remediation)
        )
        step_ratio = step_count / total
        if step_ratio >= 0.35:
            value += 2
            accuracy += 3
        elif step_ratio >= 0.20:
            value += 1
            accuracy += 1

    # --- Comparison benchmark bonus (v35) ---
    # Finding descriptions that reference industry benchmarks, Google recommendations, or competitive
    # comparisons ("Google recommends", "industry average", "competing sites", "most sites", "compared
    # to competitors") make urgency tangible for SMB owners who need external context to judge severity.
    _BENCHMARK_RE = re.compile(
        r'\b(?:google recommends|industry (?:average|standard|benchmark)|competing sites?|'
        r'competitors?|most sites?|other sites?|industry average|compares? to|best practice|'
        r'compared to|peers|above the fold industry|google\'s recommendation|SERP competitors?|'
        r'according to google|google states?|data shows?|studies show|research shows)\b',
        re.IGNORECASE,
    )
    if total > 0:
        bench_count = sum(1 for f in findings if _BENCHMARK_RE.search(f.description))
        bench_ratio = bench_count / total
        if bench_ratio >= 0.15:
            value += 3
            accuracy += 2
        elif bench_ratio >= 0.08:
            value += 1
            accuracy += 1

    # --- Confidence calibration bonus (v35) ---
    # Reports that use calibrated, distinct confidence values (not a uniform 0.80 for everything)
    # demonstrate genuine per-finding risk assessment rather than mechanical defaults.
    # ≥5 distinct confidence values signals thoughtful measurement rigor.
    if total >= 5:
        distinct_confidences = len({round(f.confidence, 2) for f in findings if hasattr(f, "confidence")})
        if distinct_confidences >= 5:
            accuracy += 2
            value += 1
        elif distinct_confidences >= 3:
            accuracy += 1

    # --- Specific numeric impact language bonus (v36) ---
    # Findings that include specific numeric outcomes (percentages, multipliers, time values)
    # in their descriptions are dramatically more credible and persuasive than qualitative
    # claims. "Reduces conversion rate by 10–15% per extra form field" is quantifiably
    # actionable; "forms may reduce conversions" is not. This bonus rewards reports that
    # ground their business impact claims in concrete measurements rather than vague
    # language — the hallmark of a consultant's analysis vs. an automated scanner.
    _NUMERIC_IMPACT_RE = re.compile(
        r'\b(?:\d+[\-–]\d+\s*%'             # ranges: 15-30%
        r'|\d+\s*%'                          # simple: 53%
        r'|\d+x\s+(?:higher|lower|faster|slower|more|less)'  # multipliers: 3x higher
        r'|\d+\s*ms|\d+\s*s\s+(?:of|delay|faster|slower)'   # time: 400ms, 2s faster
        r'|\d+[\-–]\d+\s*(?:seconds?|ms)'   # time ranges: 300-800ms
        r'|\d+\s+(?:per|every)\s+\d+\s+(?:field|page|request)'  # per-N patterns
        r'|\$\s*\d[\d,]+\s+(?:per|monthly|annually))\b',    # dollar amounts
        re.IGNORECASE,
    )
    if total > 0:
        numeric_impact_count = sum(
            1 for f in findings
            if f.description.strip() and _NUMERIC_IMPACT_RE.search(f.description)
        )
        numeric_impact_ratio = numeric_impact_count / total
        if numeric_impact_ratio >= 0.35:
            value += 3
            accuracy += 2
        elif numeric_impact_ratio >= 0.20:
            value += 1
            accuracy += 1

    # --- All six categories populated bonus (v36) ---
    # A report that surfaces at least one finding in every major category (security,
    # email_auth, seo, ada, conversion, performance) demonstrates comprehensive coverage
    # of the full web presence risk spectrum. A single-category or narrow report lacks
    # the breadth to justify $299 — it reads like a focused SEO audit, not a full
    # web presence risk assessment. This bonus incentivises multi-domain coverage
    # without requiring deep depth in every category.
    _ALL_SIX_CATS = {"security", "email_auth", "seo", "ada", "conversion", "performance"}
    populated_cats = {f.category for f in findings} & _ALL_SIX_CATS
    populated_count = len(populated_cats)
    if populated_count == 6:
        value += 4
        accuracy += 3
        reasons.append("all_six_categories_populated")
    elif populated_count == 5:
        value += 2
        accuracy += 2
    elif populated_count == 4:
        value += 1
        accuracy += 1

    # --- Category finding balance bonus (v37) ---
    # A well-balanced report covers multiple risk domains without any single category
    # monopolising the findings. When one category holds more than 70% of all findings,
    # the report reads like a narrow single-domain audit (e.g., an SEO-only report)
    # rather than a comprehensive web presence risk assessment. Balanced multi-domain
    # coverage is the primary differentiator from free single-purpose tools like
    # securityheaders.com or Screaming Frog. The bonus rewards balance; the penalty
    # flags reports that are so lopsided they undercut the $299 value proposition.
    if total >= 6:
        cat_counts_list = list(_count_by_category(findings).values())
        max_cat_count = max(cat_counts_list) if cat_counts_list else 0
        max_cat_fraction = max_cat_count / total
        if max_cat_fraction <= 0.55:
            value += 2
            accuracy += 2
        elif max_cat_fraction > 0.70:
            value -= 1

    # --- Finding severity distribution bonus (v37) ---
    # A well-calibrated report has a healthy spread across severity levels — not all
    # critical/high (which signals severity inflation and undermines credibility) and
    # not all low (which signals a shallow scan that fails to surface real risks).
    # The "medium" tier typically represents the bulk of genuine issues: real problems
    # that require attention but don't warrant emergency response. Reports where
    # 35–70% of findings are medium severity demonstrate calibrated judgment — the
    # hallmark of a skilled analyst rather than an automated scanner that flags
    # everything as high or flags nothing as serious.
    if total >= 6:
        medium_count = sum(1 for f in findings if f.severity == "medium")
        medium_fraction = medium_count / total
        if 0.35 <= medium_fraction <= 0.70:
            accuracy += 2
            value += 1

    # --- Remediation persona voice bonus (v38) ---
    # Remediations written in direct second-person voice ("your site", "your server",
    # "your developer") feel more like personal consultant advice than boilerplate
    # tool output. SMB owners report higher perceived value when guidance addresses
    # them personally rather than using impersonal passive constructions ("it should be",
    # "one should"). This bonus rewards reports where the majority of remediations
    # speak directly to the reader — a key differentiator between automated tool output
    # and premium consultant-grade deliverables.
    _PERSONA_VOICE_RE = re.compile(
        r'\b(?:your\s+(?:site|server|developer|team|page|form|domain|business|hosting|theme|plugin|admin)|'
        r'you(?:\s+can|\s+should|\s+will|\s+need|\s+may))\b',
        re.IGNORECASE,
    )
    if findings:
        persona_voice_count = sum(
            1 for f in findings
            if f.remediation and _PERSONA_VOICE_RE.search(f.remediation)
        )
        persona_voice_ratio = persona_voice_count / total
        if persona_voice_ratio >= 0.55:
            value += 3
            accuracy += 2
            reasons.append("remediation_persona_voice_high")
        elif persona_voice_ratio >= 0.35:
            value += 1
            accuracy += 1

    # --- Finding impact tiering bonus (v38) ---
    # A credible web presence audit surfaces issues across the full severity spectrum —
    # from quick hygiene fixes (low) through significant risks (medium) to urgent threats
    # (high/critical). Reports where at least 3 distinct severity levels each have ≥2
    # findings demonstrate genuine depth of analysis rather than a shallow scan that
    # clusters everything at one level. This rewards calibrated, multi-tier risk
    # stratification that mirrors how a real consultant would triage findings.
    if total >= 6:
        sev_level_counts = Counter(f.severity for f in findings)
        tiers_with_two_plus = sum(1 for cnt in sev_level_counts.values() if cnt >= 2)
        if tiers_with_two_plus >= 3:
            value += 3
            accuracy += 2
            reasons.append("finding_impact_tiering_3plus")
        elif tiers_with_two_plus == 2:
            value += 1
            accuracy += 1

    # --- Finding title action trigger bonus (v39) ---
    # Finding titles that lead with strong action-trigger words ("Missing", "Exposed",
    # "Broken", "Outdated", "Misconfigured") create immediate urgency and relevance for
    # the SMB reader. These titles scan faster in the table-of-contents view and convert
    # better in sales demos — the owner can immediately see that something is wrong,
    # not just that "there is an issue with X". Action-trigger titles are a hallmark of
    # analyst-grade audits vs. tool-generated reports that use neutral technical noun
    # phrases. A report where the majority of titles use this style feels more urgent,
    # buyer-centric, and worth the $299 asking price.
    _TITLE_ACTION_TRIGGER_RE = re.compile(
        r'\b(?:Missing|Exposed|Broken|Outdated|Misconfigured|Absent|Vulnerable|'
        r'Unsecured|Detected|Failed|Disabled|Weak|Leaking|Insecure|No\s+\w)',
        re.IGNORECASE,
    )
    if total > 0:
        action_trigger_count = sum(
            1 for f in findings
            if f.title and _TITLE_ACTION_TRIGGER_RE.search(f.title)
        )
        action_trigger_ratio = action_trigger_count / total
        if action_trigger_ratio >= 0.50:
            value += 2
            accuracy += 2
            reasons.append("finding_title_action_triggers_50pct")
        elif action_trigger_ratio >= 0.30:
            value += 1
            accuracy += 1

    # --- Remediation sentence depth bonus (v39) ---
    # Remediations that consist of multiple distinct sentences or steps — not just a
    # single directive — demonstrate that the analyst has thought through implementation
    # context, edge cases, and platform-specific guidance. A three-sentence remediation
    # covering "what to do", "where to do it", and "how to verify" is far more useful
    # than a single-sentence instruction. Reports with consistently multi-sentence
    # remediations signal professional-grade guidance and justify premium pricing better
    # than terse automated tool suggestions.
    if total > 0:
        sentence_counts = [
            len([s for s in re.split(r'(?<=[.!?])\s+', f.remediation.strip()) if len(s.strip()) > 10])
            if f.remediation else 0
            for f in findings
        ]
        avg_sentences = sum(sentence_counts) / total
        if avg_sentences >= 3.0:
            accuracy += 2
            value += 1
            reasons.append("remediation_sentence_depth_3plus")
        elif avg_sentences >= 2.0:
            accuracy += 1

    # --- Local SEO geo-relevance bonus (v40) ---
    # Reports that explicitly reference geographic / local SEO context (city/location/maps/
    # near me/local pack/NAP/Google Maps) signal that the analyst has tailored the audit
    # to a local business owner's actual acquisition channel, not just run generic tool checks.
    # SMB owners running service businesses (plumbers, dentists, restaurants) derive most of
    # their revenue from local search. When findings directly reference how issues affect local
    # pack rankings, Google Maps visibility, or NAP consistency, the report immediately feels
    # more relevant and worth the $299 price point. Generic reports that could apply to any
    # website have lower perceived value than ones that acknowledge the business's local context.
    _GEO_RELEVANCE_RE = re.compile(
        r'\b(?:local\s+(?:pack|search|seo|ranking|business)|google\s+maps|near\s+me'
        r'|nap\s+(?:consistency|citation)|map\s+(?:listing|embed|pin)|city|location'
        r'|neighborhood|radius|zip\s+code|local\s+3.pack|google\s+business\s+profile'
        r'|local\s+competitor|service\s+area)\b',
        re.IGNORECASE,
    )
    if total > 0:
        geo_count = sum(
            1 for f in findings
            if _GEO_RELEVANCE_RE.search(f.description or "")
            or _GEO_RELEVANCE_RE.search(f.remediation or "")
        )
        geo_ratio = geo_count / total
        if geo_ratio >= 0.30:
            value += 3
            accuracy += 2
            reasons.append("geo_local_relevance_high")
        elif geo_ratio >= 0.15:
            value += 2
            accuracy += 1

    # --- Report section diversity bonus (v40) ---
    # Reports with a rich variety of named sections (executive summary, risk dashboard,
    # security, email auth, ADA, SEO, conversion, competitor context, roadmap, kpi,
    # appendix, etc.) are perceived as more comprehensive and consultant-grade than
    # reports with only 3–4 sections. The sections list in pdf_info reflects what the
    # PDF rendering layer actually included — a proxy for structural completeness.
    # Having ≥8 distinct sections signals thorough coverage of all report areas and
    # justifies the premium price through visible structural depth in the TOC/headers.
    _section_keys = pdf_info.get("sections") or []
    _distinct_section_count = len(set(str(s) for s in _section_keys if s))
    if _distinct_section_count >= 8:
        value += 3
        accuracy += 1
        reasons.append("report_section_diversity_8plus")
    elif _distinct_section_count >= 6:
        value += 1

    # --- Mobile UX coverage bonus (v41) ---
    # Reports that include mobile-specific findings (viewport, touch targets, PWA manifest,
    # responsive design, pinch-to-zoom) demonstrate that the analyst evaluated the site
    # through a mobile-first lens — the primary access pattern for 60–70% of SMB visitors.
    # When findings explicitly reference mobile UX issues, the report resonates more strongly
    # with SMB owners who know that "most of my customers find me on their phones." A report
    # that only flags desktop-visible issues while ignoring mobile rendering problems under-
    # serves the majority of the business's web audience and feels less tailored and relevant.
    _MOBILE_UX_RE = re.compile(
        r'\b(?:mobile|viewport|touch\s+target|responsive|pwa|manifest|homescreen|'
        r'pinch.to.zoom|mobile.device|iphone|android|small\s+screen)\b',
        re.IGNORECASE,
    )
    if total > 0:
        mobile_ux_count = sum(
            1 for f in findings
            if _MOBILE_UX_RE.search(f.title or "")
            or _MOBILE_UX_RE.search(f.description or "")
            or _MOBILE_UX_RE.search(f.remediation or "")
        )
        if mobile_ux_count >= 3:
            value += 2
            accuracy += 2
            reasons.append("mobile_ux_coverage_3plus")
        elif mobile_ux_count >= 2:
            value += 1
            accuracy += 1

    # --- Remediation outcome verb bonus (v41) ---
    # Remediations that explicitly state the OUTCOME of fixing the issue — using verbs like
    # "reduces", "improves", "prevents", "increases", "eliminates" — are significantly more
    # persuasive than remediations that only describe the action ("add X", "enable Y").
    # When a remediation says "Prevents email spoofing of your domain by fraudulent senders"
    # vs. "Add an SPF record to your DNS", the first variant directly connects the technical
    # action to the business outcome the SMB owner cares about. Outcome-oriented remediations
    # justify premium pricing by making the value of each fix explicit and buyer-centric,
    # turning the remediation section from a task list into a business case for action.
    _OUTCOME_VERB_RE = re.compile(
        r'\b(?:reduces?|improves?|prevents?|increases?|eliminates?|stops?|ensures?|enables?|'
        r'protects?|secures?|boosts?|fixes?|resolves?|removes?)\s+\w+',
        re.IGNORECASE,
    )
    if total > 0:
        outcome_verb_count = sum(
            1 for f in findings
            if f.remediation and _OUTCOME_VERB_RE.search(f.remediation)
        )
        outcome_verb_ratio = outcome_verb_count / total
        if outcome_verb_ratio >= 0.40:
            value += 3
            accuracy += 2
            reasons.append("remediation_outcome_verb_high")
        elif outcome_verb_ratio >= 0.25:
            value += 1
            accuracy += 1

    # --- Renderer quality bonus ---
    renderer = str(pdf_info.get("renderer") or "")
    if renderer == "weasyprint":
        aesthetic += 8
    elif renderer in {"reportlab", "pdfkit"}:
        aesthetic += 4  # structured multi-page output; no penalty
    elif renderer == "fallback_minimal_pdf":
        aesthetic -= 10
        reasons.append("pdf_fallback_renderer")

    # --- Clamp and build score ---
    score = ReportScore(
        value_score=max(0.0, min(100.0, value)),
        accuracy_score=max(0.0, min(100.0, accuracy)),
        aesthetic_score=max(0.0, min(100.0, aesthetic)),
        pass_gate=False,
        reasons=reasons,
    )
    score.pass_gate = bool(
        score.value_score >= 75.0
        and score.accuracy_score >= 70.0
        and score.aesthetic_score >= 65.0
        and "insufficient_screenshots" not in score.reasons
        and "insufficient_charts" not in score.reasons
        and "missing_roadmap_table" not in score.reasons
        and "report_too_brief" not in score.reasons
        and "too_few_findings" not in score.reasons
        and not any(r.startswith("min_findings_not_met:") for r in score.reasons)
        and not any(r.startswith("category_absent:") for r in score.reasons)
        and not any(r.startswith("urgent_findings_incomplete:") for r in score.reasons)
        and not any(r.startswith("too_many_low_confidence_findings:") for r in score.reasons)
        and not any(r.startswith("duplicate_findings:") for r in score.reasons)
        and not any(r.startswith("weak_commercial_model:") for r in score.reasons)
    )
    validate_report_score(score)
    return score


def adapt_strategy(*, previous_memory: dict[str, Any], score: ReportScore, sales_scores: dict[str, float] | None = None) -> dict[str, Any]:
    mem = dict(previous_memory)
    notes: list[str] = list(mem.get("notes") or [])
    weights: dict[str, float] = dict(mem.get("weights") or {})
    min_findings: dict[str, int] = dict(mem.get("min_findings") or {})
    history: list[dict[str, Any]] = list(mem.get("score_history") or [])
    category_miss_count: dict[str, int] = dict(mem.get("category_miss_count") or {})
    report_depth_level = max(1, min(5, int(mem.get("report_depth_level", 1) or 1)))
    report_word_target = int(mem.get("report_word_target", 1200) or 1200)
    sales_sim_target_count = max(6, min(10, int(mem.get("sales_sim_target_count", 6) or 6)))
    sales_turn_count = max(4, min(8, int(mem.get("sales_turn_count", 5) or 5)))
    persona_pressure: dict[str, int] = dict(mem.get("persona_pressure") or {})

    # Record score for trend analysis
    history.append({
        "value": round(score.value_score, 1),
        "accuracy": round(score.accuracy_score, 1),
        "aesthetic": round(score.aesthetic_score, 1),
        "pass": score.pass_gate,
    })
    history = history[-20:]  # keep last 20 scores

    if not score.pass_gate:
        if "insufficient_screenshots" in score.reasons:
            notes.append("priority:raise_screenshot_capture")
        if "insufficient_charts" in score.reasons:
            notes.append("priority:force_chart_generation")
        if "pdf_fallback_renderer" in score.reasons:
            notes.append("warning:pdf_renderer_fallback_used")
        if "no_high_urgency_findings" in score.reasons:
            notes.append("priority:dig_deeper_for_high_severity")
        if "too_few_findings" in score.reasons:
            notes.append("priority:increase_scan_depth")
        if "report_too_brief" in score.reasons:
            notes.append("priority:expand_report_depth")
        if any(r.startswith("weak_commercial_model:") for r in score.reasons):
            notes.append("priority:strengthen_commercial_case")
            mem["value_model_lead_bias"] = min(24, int(mem.get("value_model_lead_bias", 0) or 0) + 3)
            mem["value_model_urgency_bias"] = min(0.18, float(mem.get("value_model_urgency_bias", 0.0) or 0.0) + 0.02)
        if "low_confidence_findings" in score.reasons or any(
            r.startswith("too_many_low_confidence_findings:") for r in score.reasons
        ):
            notes.append("priority:raise_finding_confidence_threshold")
        if any(r.startswith("duplicate_findings:") for r in score.reasons):
            notes.append("priority:deduplicate_findings_before_report")
        if "no_high_urgency_spread" in score.reasons:
            notes.append("priority:target_high_severity_across_categories")

        for reason in score.reasons:
            if reason.startswith("min_findings_not_met:"):
                cat = reason.split(":", 1)[1]
                current = int(min_findings.get(cat, 2))
                min_findings[cat] = min(current + 1, 8)  # cap at 8
                weights[cat] = round(float(weights.get(cat, 1.0)) + 0.05, 2)
                notes.append(f"adjusted:min_findings_{cat}={min_findings[cat]}")
            elif reason.startswith("category_absent:"):
                cat = reason.split(":", 1)[1]
                weights[cat] = round(float(weights.get(cat, 1.0)) + 0.10, 2)
                notes.append(f"boosted:weight_{cat}={weights[cat]}")
                # Track consecutive category misses to trigger scan-depth escalation
                category_miss_count[cat] = int(category_miss_count.get(cat, 0)) + 1
                if category_miss_count[cat] >= 2:
                    notes.append(f"scan_depth_escalate:{cat}=missed_{category_miss_count[cat]}_times")
    else:
        notes.append(f"pass:value={score.value_score:.0f}_accuracy={score.accuracy_score:.0f}")
        # If consistently passing, relax min_findings slightly to not over-constrain
        if len(history) >= 5 and all(h["pass"] for h in history[-5:]):
            for cat in list(min_findings.keys()):
                if min_findings[cat] > 3:
                    min_findings[cat] -= 1
                    notes.append(f"relaxed:min_findings_{cat}={min_findings[cat]}")
        # On a pass, decrement category miss counts (improving coverage reduces concern)
        for cat in list(category_miss_count.keys()):
            if category_miss_count[cat] > 0:
                category_miss_count[cat] = max(0, category_miss_count[cat] - 1)

    # Depth progression policy: increase toward richer reports across overnight runs.
    if (not score.pass_gate) or score.value_score < 85.0 or "report_too_brief" in score.reasons:
        report_depth_level = min(5, report_depth_level + 1)
    elif score.pass_gate and report_depth_level < 5 and len(history) >= 2 and all(h["pass"] for h in history[-2:]):
        report_depth_level = min(5, report_depth_level + 1)
    report_word_target = 1200 + ((report_depth_level - 1) * 350)
    if "report_too_brief" in score.reasons:
        report_word_target += 200
    notes.append(f"target:report_depth={report_depth_level}")
    notes.append(f"target:report_words={report_word_target}")

    # --- Sales sim feedback ---
    if sales_scores:
        avg_trust = float(sales_scores.get("avg_trust", 100.0))
        avg_close = float(sales_scores.get("avg_close", 100.0))
        avg_objection = float(sales_scores.get("avg_objection", 100.0))
        worst_scenario_key = str(sales_scores.get("worst_scenario_key") or "").strip()
        worst_scenario_total = float(sales_scores.get("worst_scenario_total", 100.0))
        if avg_trust < 72:
            notes.append(f"sales_weakness:low_trust={avg_trust:.0f}_improve_evidence_language")
        if avg_close < 70:
            notes.append(f"sales_weakness:low_close={avg_close:.0f}_improve_next_step_prompts")
        if avg_objection < 70:
            notes.append(f"sales_weakness:low_objection={avg_objection:.0f}_improve_value_framing")
        if worst_scenario_key and worst_scenario_total < 72:
            notes.append(f"sales_weakness:scenario={worst_scenario_key}_score={worst_scenario_total:.0f}_target_persona_playbook")
        if avg_trust < 74 or avg_close < 72 or avg_objection < 72 or worst_scenario_total < 72:
            sales_sim_target_count = min(10, sales_sim_target_count + 1)
            sales_turn_count = min(8, sales_turn_count + 1)
        elif score.pass_gate and avg_trust >= 80 and avg_close >= 80 and avg_objection >= 80:
            sales_turn_count = max(5, sales_turn_count - 1)

        if worst_scenario_key:
            if worst_scenario_total < 75:
                persona_pressure[worst_scenario_key] = min(
                    6, int(persona_pressure.get(worst_scenario_key, 0) or 0) + 2
                )
                notes.append(f"priority:persona_pressure_{worst_scenario_key}={persona_pressure[worst_scenario_key]}")
            else:
                for key in list(persona_pressure.keys()):
                    if key == worst_scenario_key:
                        persona_pressure[key] = max(0, int(persona_pressure.get(key, 0) or 0) - 1)
        for key in list(persona_pressure.keys()):
            if persona_pressure[key] <= 0:
                persona_pressure.pop(key, None)

        notes.append(f"target:sales_sim_scenarios={sales_sim_target_count}")
        notes.append(f"target:sales_turn_count={sales_turn_count}")
        # Track sales history for trend visibility
        sales_history: list[dict[str, Any]] = list(mem.get("sales_history") or [])
        sales_history.append({
            "trust": round(avg_trust, 1),
            "close": round(avg_close, 1),
            "objection": round(avg_objection, 1),
        })
        mem["sales_history"] = sales_history[-20:]

    mem["notes"] = notes[-50:]
    mem["weights"] = weights
    mem["min_findings"] = min_findings
    mem["score_history"] = history
    mem["category_miss_count"] = category_miss_count
    mem["report_depth_level"] = report_depth_level
    mem["report_word_target"] = report_word_target
    mem["sales_sim_target_count"] = sales_sim_target_count
    mem["sales_turn_count"] = sales_turn_count
    mem["persona_pressure"] = persona_pressure
    return mem
