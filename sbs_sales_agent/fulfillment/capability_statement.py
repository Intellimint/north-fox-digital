from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import AgentSettings
from ..integrations.codex_fulfillment import CodexFulfillmentClient
from ..models import ProspectFeatures
from .pdf_render import render_capability_data_to_pdf, render_html_to_pdf


SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _clean_sentence(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    value = re.sub(r"\[[^\]]+\]", "", value).strip()
    return value


def _looks_service_phrase(text: str) -> bool:
    t = _clean_sentence(text)
    if len(t.split()) < 2:
        return False
    low = t.lower()
    blocked = [
        "initialrequesttimestamp",
        "performance.",
        "window.",
        "function(",
        "json",
        "javascript",
        "cookie",
        "{",
        "}",
        "=>",
        "http://",
        "https://",
    ]
    if any(b in low for b in blocked):
        return False
    alpha = sum(1 for ch in t if ch.isalpha())
    if alpha < max(8, int(len(t) * 0.55)):
        return False
    return True


def _extract_core_capabilities(prospect: ProspectFeatures, website_context: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for kw in prospect.keywords:
        token = _clean_sentence(str(kw))
        if token and _looks_service_phrase(token) and token.lower() not in {x.lower() for x in out}:
            out.append(token)
        if len(out) >= 6:
            return out
    narrative = _clean_sentence(prospect.capabilities_narrative or "")
    if narrative:
        chunks = [c.strip(" -;,.") for c in re.split(r",|;| and ", narrative) if c.strip()]
        for c in chunks:
            token = _clean_sentence(c)
            if not _looks_service_phrase(token):
                continue
            if token.lower() not in {x.lower() for x in out}:
                out.append(token)
            if len(out) >= 6:
                return out
    # Do not use raw website snippets as capabilities; too noisy and can leak non-service text.
    return out[:6]


def _build_capability_summary(prospect: ProspectFeatures, core_caps: list[str], website_context: dict[str, Any] | None) -> str:
    narrative = _clean_sentence(prospect.capabilities_narrative or "")
    if narrative:
        first = SENTENCE_RE.split(narrative)[0].strip()
        if len(first.split()) >= 8:
            return first
    if core_caps:
        lead = ", ".join(core_caps[:3])
        place = f" in {prospect.city}, {prospect.state}" if prospect.city and prospect.state else ""
        return f"{prospect.business_name} delivers {lead}{place}, with SBA-ready positioning for buyer and prime outreach."
    if website_context and website_context.get("description"):
        desc = _clean_sentence(str(website_context.get("description")))
        if len(desc.split()) >= 8:
            return desc
    return f"{prospect.business_name} delivers specialized services aligned to NAICS and procurement needs."


def _fallback_capability_from_business_name(prospect: ProspectFeatures) -> str:
    name = _clean_sentence(prospect.business_name or "")
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", name) if t]
    blocked = {"llc", "inc", "corp", "co", "ltd", "company", "services", "solutions"}
    usable = [t for t in tokens if t.lower() not in blocked]
    if not usable:
        return "Program and contract support aligned to listed NAICS"
    phrase = " ".join(usable[:3])
    return f"{phrase} support and delivery aligned to listed NAICS"


def _template_html(data: dict[str, Any]) -> str:
    bullets = "".join(f"<li>{item}</li>" for item in (data.get("core_capabilities") or []))
    naics = ", ".join(data.get("naics_codes") or [])
    certs = ", ".join(data.get("certifications") or [])
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ font-family: Georgia, serif; margin: 28px; color: #1f2328; }}
h1 {{ margin: 0 0 8px; font-size: 26px; }}
.sub {{ color: #4a5568; margin-bottom: 14px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.box {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; }}
ul {{ margin: 8px 0 0 18px; }}
</style>
</head>
<body>
<h1>{data.get("business_name","")}</h1>
<div class="sub">Capability Statement (draft) | UEI: {data.get("uei","")} | CAGE: {data.get("cage_code","")}</div>
<p><strong>Capability Summary:</strong> {data.get("capability_summary","")}</p>
<div class="grid">
  <div class="box">
    <strong>Core Capabilities</strong>
    <ul>{bullets}</ul>
  </div>
  <div class="box">
    <strong>Differentiators</strong>
    <div>{data.get("differentiators","")}</div>
  </div>
  <div class="box">
    <strong>NAICS</strong>
    <div>{naics}</div>
  </div>
  <div class="box">
    <strong>Certifications</strong>
    <div>{certs or 'N/A'}</div>
  </div>
</div>
<p><strong>Contact:</strong> {data.get("contact_name","")} | {data.get("email","")} | {data.get("phone","")}</p>
<p><strong>Website:</strong> {data.get("website","")}</p>
</body>
</html>"""


def build_capability_statement_artifacts(
    *,
    prospect: ProspectFeatures,
    out_dir: Path,
    settings: AgentSettings | None = None,
    website_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "business_name": prospect.business_name,
        "contact_name": prospect.contact_name_normalized or prospect.business_name,
        "email": prospect.email,
        "phone": prospect.phone or "",
        "website": prospect.website or "",
        "uei": prospect.uei or "",
        "cage_code": prospect.cage_code or "",
        "naics_codes": ([prospect.naics_primary] if prospect.naics_primary else []) + [c for c in prospect.naics_all_codes if c != prospect.naics_primary][:5],
        "certifications": prospect.certs[:8],
        "core_capabilities": _extract_core_capabilities(prospect, website_context),
        "capability_summary": "",
        "differentiators": "Fast response, clear communication, and practical execution for procurement teams.",
    }
    if not data["core_capabilities"]:
        data["core_capabilities"] = [_fallback_capability_from_business_name(prospect)]
    data["capability_summary"] = _build_capability_summary(prospect, data["core_capabilities"], website_context)
    if website_context and website_context.get("ok"):
        desc = str(website_context.get("description") or "").strip()
        if desc:
            data["differentiators"] = desc[:220]

    if settings is not None:
        codex = CodexFulfillmentClient(settings)
        if codex.enabled():
            payload = {
                "capability_data": data,
                "website_context": website_context or {},
            }
            generated = codex.generate(task="capability_statement", payload=payload)
            if generated.get("ok"):
                g_data = generated.get("capability_data")
                if isinstance(g_data, dict):
                    # Keep known keys only.
                    for key in (
                        "business_name",
                        "contact_name",
                        "email",
                        "phone",
                        "website",
                        "uei",
                        "cage_code",
                        "naics_codes",
                        "certifications",
                        "core_capabilities",
                        "capability_summary",
                        "differentiators",
                    ):
                        if key in g_data and g_data[key]:
                            data[key] = g_data[key]

    html = _template_html(data)
    html_path = out_dir / "capability_statement.html"
    html_path.write_text(html, encoding="utf-8")
    (out_dir / "capability_statement_data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    pdf_path = out_dir / "capability_statement.pdf"
    render_result = render_html_to_pdf(html_path, pdf_path)
    if render_result.get("renderer") == "fallback_minimal_pdf":
        # Replace generic fallback output with a real, business-readable capability PDF.
        render_result = render_capability_data_to_pdf(data, pdf_path)
    return {
        "artifacts": [str(pdf_path), str(html_path), str(out_dir / "capability_statement_data.json")],
        "render_result": render_result,
    }
