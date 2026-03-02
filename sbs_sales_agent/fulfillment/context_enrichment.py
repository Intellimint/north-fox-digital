from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import AgentSettings
from ..models import ProspectFeatures

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\\1>", flags=re.IGNORECASE | re.DOTALL)


def _clean_text(html: str) -> str:
    html = SCRIPT_STYLE_RE.sub(" ", html or "")
    no_tags = TAG_RE.sub(" ", html)
    return WS_RE.sub(" ", no_tags).strip()


def fetch_website_context(settings: AgentSettings, prospect: ProspectFeatures) -> dict[str, Any]:
    website = (prospect.website or "").strip()
    if not website:
        return {"ok": False, "reason": "no_website"}
    url = website if website.startswith(("http://", "https://")) else f"https://{website}"
    try:
        with httpx.Client(timeout=settings.website_research_timeout_seconds, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return {"ok": False, "reason": f"http_{resp.status_code}", "url": url}
            html = resp.text or ""
    except Exception as exc:
        return {"ok": False, "reason": f"fetch_error:{exc}", "url": url}

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        title = _clean_text(m.group(1))[:200]
    desc = ""
    m2 = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m2:
        desc = _clean_text(m2.group(1))[:400]

    text = _clean_text(html)
    snippets = []
    if text:
        for chunk in re.split(r"(?<=[.!?])\s+", text):
            c = chunk.strip()
            if 40 <= len(c) <= 220:
                snippets.append(c)
            if len(snippets) >= 6:
                break

    return {
        "ok": True,
        "url": url,
        "title": title,
        "description": desc,
        "snippets": snippets,
    }
