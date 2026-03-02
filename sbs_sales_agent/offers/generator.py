from __future__ import annotations

import re
from urllib.parse import urlparse
from typing import Any

from ..config import AgentSettings
from ..integrations.ollama_client import OllamaClient
from ..models import Offer, OfferVariant, ProspectFeatures

TOKEN_RE = re.compile(r"\b[\w']+\b")


def count_words(text: str) -> int:
    return len(TOKEN_RE.findall(text))


def render_template(template: str, context: dict[str, Any]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def outreach_footer(settings: AgentSettings) -> str:
    return f"{settings.unsubscribe_footer}\n{settings.sender_address_footer}"


def _llm_initial_outreach(
    *,
    settings: AgentSettings,
    offer: Offer,
    prospect: ProspectFeatures,
    fallback_subject: str,
    light_findings: list[dict[str, str]] | None = None,
) -> tuple[str, str] | None:
    if not settings.use_llm_first_touch:
        return None
    client = OllamaClient(settings)
    offer_name = {
        "DSBS_REWRITE": "DSBS profile rewrite",
        "CAPABILITY_STATEMENT": "1-page capability statement",
        "WEB_PRESENCE_REPORT": "Web Presence Risk + Revenue Growth Report",
    }.get(offer.offer_type, "fixed-price service")
    price = f"${offer.price_cents/100:.0f}"
    max_words = int(offer.sales_constraints.get("max_main_words", 100))
    personalization = {
        "contact_name": prospect.contact_name_normalized or "",
        "first_name": prospect.first_name_for_greeting,
        "business_name": prospect.business_name,
        "city": prospect.city or "",
        "state": prospect.state or "",
        "naics_primary": prospect.naics_primary or "",
        "uei": prospect.uei or "",
        "cage_code": prospect.cage_code or "",
        "keywords": prospect.keywords[:8],
        "certs": prospect.certs[:6],
        "website": prospect.website or "",
        "offer_type": offer.offer_type,
        "offer_name": offer_name,
        "price": price,
        "light_findings": light_findings or [],
    }
    pattern_guide = {
        "DSBS_REWRITE": (
            "Pattern: relevance from SBA/DSBS listing -> pain (hard to find in search) -> "
            "what you deliver (rewrite + keyword alignment) -> flat price -> soft CTA."
        ),
        "CAPABILITY_STATEMENT": (
            "Pattern: acknowledge UEI/CAGE/NAICS pieces -> gap (missing one-page forwardable doc) -> "
            "outcome (professional PDF in 24 hours) -> low-friction CTA."
        ),
        "SUPPLIER_DIVERSITY_KIT": (
            "Pattern: certification advantage -> procurement routing friction -> "
            "landing page + outreach sequence deliverable -> ask to chat."
        ),
        "NAICS_AUDIT": (
            "Pattern: misaligned NAICS/keywords -> diluted findability -> "
            "quick audit with top 2-3 codes and headline -> small flat fee CTA."
        ),
        "DMARC_TRUST_FIX": (
            "Pattern: clear warning with proof -> safe mode remediation -> "
            "flat fee with optional bundle -> no-pressure CTA."
        ),
        "WEB_PRESENCE_REPORT": (
            "Pattern: quick heads-up from public scan -> business risk (spoofing, trust, search visibility, lost leads) -> "
            "what you deliver (evidence-based PDF with screenshots and prioritized action plan) -> flat price -> email-only CTA."
        ),
    }.get(offer.offer_type, "Pattern: personalize, identify concrete gap, offer specific fixed-price deliverable, ask one easy reply question.")
    result = client.chat_json(
        system=(
            "Write a cold email first-touch for a small business in a natural hometown-sales-guy tone. "
            "It must feel 1:1 personalized from the provided data, not templated. "
            "Use cold-email best practices: personalization, clear claim, a small proof/evidence line, and one clear next step. "
            "Do NOT fabricate facts. Only use provided data. Avoid hype and AI-sounding phrasing. "
            "Return strict JSON with keys: subject, body_main. "
            f"Constraints for body_main: 70-{max_words} words, short paragraphs, ask one easy reply question."
        ),
        user=(
            "Draft a first-touch email to sell a fixed-price service over email only.\n\n"
            f"Personalization data: {personalization}\n\n"
            f"Offer playbook: {pattern_guide}\n\n"
            "Strong DSBS opener examples to mirror in tone/style when relevant:\n"
            "1) \"Hey Michael, quick question - when's the last time you looked at your DSBS profile? "
            "I pulled it up for [Business] and I think it might be costing you visibility with contracting officers.\"\n"
            "2) \"Hey Michael, I'm going to be honest - I found your company on the SBA site and almost scrolled past it. "
            "Not because of what you do, but because of how your profile reads.\"\n\n"
            "Use a subject that feels like an internal email subject (simple, natural) and avoid clickbait.\n"
            "Do NOT use NAICS code numbers as weak personalization in the opener unless industry language is unavailable.\n"
            "Do NOT ask for a call, chat, meeting, Zoom, or 10-minute conversation. CTA must be email-only."
            "\nIf light_findings are provided, mention 1-2 concrete findings naturally (no list formatting)."
        ),
        schema_hint={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body_main": {"type": "string"},
            },
        },
    )
    if not isinstance(result, dict) or result.get("ok") is False:
        return None
    subject = str(result.get("subject") or "").strip()
    body_main = str(result.get("body_main") or "").strip()
    if not subject or not body_main:
        return None
    wc = count_words(body_main)
    if wc < 50 or wc > max_words:
        return None
    if "[your name]" in body_main.lower():
        return None
    if re.search(r"\b(call|chat|meeting|zoom|phone)\b", body_main, flags=re.IGNORECASE):
        return None
    if prospect.business_name and prospect.business_name.lower() not in body_main.lower():
        # Ensure it feels 1:1.
        return None
    return subject, body_main


def _select_light_findings(light_findings: list[dict[str, str]] | None, *, max_items: int = 2) -> list[dict[str, str]]:
    if not light_findings:
        return []
    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    uniq: dict[str, dict[str, str]] = {}
    for row in light_findings:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        sev = str(row.get("severity") or "medium").lower()
        key = title.lower()
        prev = uniq.get(key)
        if prev is None or sev_rank.get(sev, 0) > sev_rank.get(str(prev.get("severity") or "").lower(), 0):
            uniq[key] = {
                "title": title,
                "severity": sev,
                "category": str(row.get("category") or ""),
            }
    rows = sorted(
        list(uniq.values()),
        key=lambda r: sev_rank.get(str(r.get("severity") or "").lower(), 0),
        reverse=True,
    )
    return rows[:max_items]


def _light_findings_line(light_findings: list[dict[str, str]] | None) -> str:
    def _translate_owner_risk(finding: dict[str, str]) -> str:
        title = str(finding.get("title") or "").lower()
        category = str(finding.get("category") or "").lower()
        if any(k in title for k in ("dmarc", "spf", "dkim", "email auth")) or category == "email_auth":
            return "your domain can be easier to spoof, which can hurt trust and inbox placement"
        if "security header" in title or category == "security":
            return "basic website security protections look incomplete"
        if "https" in title or "tls" in title or "certificate" in title:
            return "parts of the site may look less secure to visitors and browsers"
        if "noindex" in title or category == "seo":
            return "important pages may be harder for Google to index and rank"
        if "no h1" in title or "title" in title or "meta description" in title:
            return "search engines may struggle to understand what your core pages are about"
        if category == "ada" or "accessibility" in title:
            return "some visitors can hit avoidable accessibility blockers on key pages"
        if category == "conversion" or "cta" in title or "form" in title:
            return "contact flow friction may be costing you qualified leads"
        pretty = str(finding.get("title") or "").strip()
        return f"there are visible website issues that likely impact trust and lead flow ({pretty[:70]})"

    picks = _select_light_findings(light_findings)
    if not picks:
        return ""
    if len(picks) == 1:
        return f"Quick heads up: I noticed {_translate_owner_risk(picks[0])}."
    return (
        f"Quick heads up: I noticed {_translate_owner_risk(picks[0])}, and {_translate_owner_risk(picks[1])}."
    )


def _truncate_to_words(text: str, max_words: int) -> str:
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if len(words) <= max_words:
        return text
    kept = words[:max_words]
    truncated = " ".join(kept).strip()
    if truncated and not truncated.endswith((".", "!", "?")):
        truncated += "."
    return truncated


def _domain_from_website(website: str | None) -> str:
    if not website:
        return "your domain"
    w = website.strip()
    if not w:
        return "your domain"
    if not w.startswith(("http://", "https://")):
        w = f"https://{w}"
    try:
        host = (urlparse(w).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or "your domain"
    except Exception:
        return "your domain"


def _scariest_finding(light_findings: list[dict[str, str]] | None) -> dict[str, str] | None:
    if not light_findings:
        return None
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    def _risk_rank(title: str, category: str, severity: str) -> int:
        t = title.lower()
        c = category.lower()
        s = severity.lower()
        if "could not be verified" in t or "inconclusive" in t:
            return 35
        if any(k in t for k in (".env", "wp-config", "backup", "exposed config", "publicly accessible")):
            return 100
        if "noindex" in t:
            return 95
        if any(k in t for k in ("dmarc", "spf", "dkim", "email authentication")) or c == "email_auth":
            return 90
        if c == "ada" and s in {"high", "critical"}:
            return 88
        if "https" in t and "not enforced" in t:
            return 80
        if "security header" in t or c == "security":
            return 75
        if c == "seo" or "h1" in t or "meta description" in t:
            return 60
        if c == "ada" or "accessibility" in t:
            return 55
        if c == "conversion":
            return 50
        return 40

    rows = []
    for f in light_findings:
        title = str(f.get("title") or "").strip()
        if not title:
            continue
        sev = str(f.get("severity") or "medium").lower()
        cat = str(f.get("category") or "")
        rows.append(
            (
                severity_rank.get(sev, 0),
                _risk_rank(title, cat, sev),
                {"title": title, "severity": sev, "category": cat},
            )
        )
    if not rows:
        return None
    # Choose by severity first, then business-risk rank.
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return rows[0][2]


def _total_findings_count(light_findings: list[dict[str, str]] | None) -> int | None:
    if not light_findings:
        return None
    for row in light_findings:
        raw = str(row.get("total_findings") or "").strip()
        if raw.isdigit():
            return int(raw)
    return None


def _specific_subject_and_opener(*, finding: dict[str, str] | None, prospect: ProspectFeatures) -> tuple[str, str]:
    first_name = prospect.first_name_for_greeting
    business_name = (prospect.business_name or "your company").strip()
    title = str((finding or {}).get("title") or "").lower()
    category = str((finding or {}).get("category") or "").lower()
    website_ref = f"the {business_name} website"

    def _missing_from_title(raw_title: str) -> list[str]:
        m = re.search(r"\(([^)]+)\)", raw_title or "")
        if not m:
            return []
        return [p.strip().upper() for p in m.group(1).split(",") if p.strip()]

    if any(k in title for k in (".env", "wp-config", "backup", "publicly accessible", "exposed config")):
        return (
            "Your site has exposed config files",
            (
                f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                f"I found exposed config/backup files on {website_ref} "
                "that should not be publicly reachable."
            ),
        )
    if "noindex" in title:
        return (
            "Google may not be indexing your site",
            (
                f"Hey {first_name}, I noticed a critical issue with your website and wanted to let you know. "
                f"Pages on {website_ref} appear to be set to noindex, "
                "which can keep you out of Google results."
            ),
        )
    if any(k in title for k in ("dmarc", "spf", "dkim", "email authentication")) or category == "email_auth":
        missing = _missing_from_title(str((finding or {}).get("title") or ""))
        missing_text = ", ".join(missing) if missing else "email authentication records"
        if "policy set to none" in title or "monitoring only" in title:
            return (
                f"Your DMARC policy is monitor-only for {business_name}",
                (
                    f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                    f"{website_ref} has DMARC in monitoring mode (`p=none`), "
                    "so mailbox providers are not instructed to block spoofed mail yet."
                ),
            )
        if "spf uses soft-fail policy" in title:
            return (
                f"{business_name}: SPF is set to soft-fail",
                (
                    f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                    f"Email auth for {website_ref} uses SPF soft-fail (`~all`), which is valid but more permissive."
                ),
            )
        if "could not be verified" in title:
            return (
                "Quick domain-auth check for your email setup",
                (
                    f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                    f"I found an email-auth inconsistency on {website_ref} "
                    "that should be verified to avoid spoofing and inbox issues."
                ),
            )
        return (
            f"Someone could spoof {business_name} right now",
            (
                f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                f"My live DNS check found {missing_text} missing for {website_ref}."
            ),
        )
    if "https" in title and "not enforced" in title:
        return (
            "Your site may allow insecure HTTP traffic",
            (
                f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                f"{website_ref} does not appear to force HTTPS redirects, "
                "so some visitors can still hit insecure HTTP pages."
            ),
        )
    if "security header" in title or category == "security":
        return (
            "Your website is missing key security protections",
            (
                f"Hey {first_name}, I noticed a critical security issue with your website and wanted to let you know. "
                f"{website_ref} appears to be missing key security headers "
                "that browsers and scanners expect."
            ),
        )
    if category == "seo" or "h1" in title or "meta description" in title:
        return (
            "Your site has search visibility gaps",
            (
                f"Hey {first_name}, I noticed a critical issue with your website and wanted to let you know. "
                f"I found basic SEO gaps on {website_ref} "
                "that can reduce your visibility in search."
            ),
        )
    if category == "ada" or "accessibility" in title or "alt text" in title:
        return (
            f"{business_name} may have ADA accessibility blockers",
            (
                f"Hey {first_name}, I noticed a critical issue with your website and wanted to let you know. "
                f"I found accessibility issues on {website_ref} "
                "that can block some visitors and create compliance risk."
            ),
        )
    return (
        "Quick heads up on your website",
        f"Hey {first_name}, I noticed a critical issue with your website and wanted to let you know.",
    )


def _build_web_report_outreach(
    *,
    settings: AgentSettings,
    offer: Offer,
    prospect: ProspectFeatures,
    light_findings: list[dict[str, str]] | None,
) -> tuple[str, str]:
    def _risk_impact_line(f: dict[str, str] | None) -> str:
        title = str((f or {}).get("title") or "").lower()
        category = str((f or {}).get("category") or "").lower()
        if any(k in title for k in ("dmarc", "spf", "dkim", "email authentication")) or category == "email_auth":
            return (
                "That means someone can send emails that look like they came from your company, "
                "and your real emails are more likely to land in spam."
            )
        if any(k in title for k in (".env", "wp-config", "backup", "publicly accessible", "exposed config")):
            return "That creates a real security and trust risk if sensitive files are publicly reachable."
        if "noindex" in title or category == "seo":
            return "That can hide important pages from Google and suppress inbound lead flow."
        if category == "conversion":
            return "That can create friction in your lead flow and reduce conversions from existing traffic."
        if category == "ada" or "accessibility" in title:
            return "That can block parts of your audience and increase avoidable compliance risk."
        if category == "security" or "https" in title or "tls" in title or "certificate" in title:
            return "That can reduce visitor trust and expose the site to avoidable security risk."
        return "That can quietly hurt trust, visibility, and lead flow if left unresolved."

    finding = _scariest_finding(light_findings)
    subject, opener = _specific_subject_and_opener(finding=finding, prospect=prospect)
    issue_count = _total_findings_count(light_findings) or len(light_findings or [])
    if issue_count < 3:
        issue_count = 3
    body_main = (
        f"{opener}\n"
        f"{_risk_impact_line(finding)}\n"
        f"I found {issue_count} issues across security, SEO visibility, accessibility, and conversion.\n"
        "If you'd like, I can send a free summary with the top fixes in order.\n"
        "Want me to send it?"
    )
    body_main = _truncate_to_words(body_main, int(offer.sales_constraints.get("max_main_words", 100)))
    return subject, body_main


def build_initial_outreach(
    *,
    settings: AgentSettings,
    offer: Offer,
    variant: OfferVariant,
    prospect: ProspectFeatures,
    light_findings: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    if offer.offer_type == "WEB_PRESENCE_REPORT":
        subject, body_main = _build_web_report_outreach(
            settings=settings,
            offer=offer,
            prospect=prospect,
            light_findings=light_findings,
        )
        full_body = f"{body_main}\n\nSincerely,\n{settings.sender_name}\n\n{outreach_footer(settings)}"
        return subject.strip(), full_body.strip()

    industry_hint = ""
    if prospect.keywords:
        industry_hint = prospect.keywords[0]
    elif prospect.naics_primary:
        industry_hint = f"NAICS {prospect.naics_primary}"
    context = {
        "business_name": (prospect.business_name or "your business").rstrip("."),
        "first_name": prospect.first_name_for_greeting,
        "state": prospect.state or "",
        "naics_primary": prospect.naics_primary or "",
        "industry_hint": industry_hint,
    }
    fallback_subject = render_template(variant.subject_template, context)
    llm_draft = _llm_initial_outreach(
        settings=settings,
        offer=offer,
        prospect=prospect,
        fallback_subject=fallback_subject,
        light_findings=light_findings,
    )
    if llm_draft is not None:
        subject, body_main = llm_draft
    else:
        subject = fallback_subject
        body_main = render_template(variant.body_template, context)
        if count_words(body_main) > int(offer.sales_constraints.get("max_main_words", 100)):
            raise ValueError(f"main body exceeds word limit for {variant.variant_key}")
    pain_line = _light_findings_line(light_findings)
    if pain_line:
        lines = body_main.splitlines()
        if not any(str(f.get("title") or "").lower() in body_main.lower() for f in (light_findings or [])):
            insert_at = 1 if len(lines) > 1 else len(lines)
            lines.insert(insert_at, pain_line)
            body_main = "\n".join(lines)
    body_main = _truncate_to_words(body_main, int(offer.sales_constraints.get("max_main_words", 100)))
    full_body = f"{body_main}\n\nSincerely,\n{settings.sender_name}\n\n{outreach_footer(settings)}"
    return subject.strip(), full_body.strip()
