from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _contains_placeholder(text: str) -> bool:
    lowered = text.lower()
    return any(x in lowered for x in ["[your", "{{", "}}", "lorem ipsum"])


def validate_dsbs_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    artifacts = [Path(p) for p in (result.get("artifacts") or [])]
    required = {
        "short_narrative.txt",
        "long_narrative.txt",
        "keyword_clusters.json",
        "differentiators.txt",
        "paste_ready_dsbs_sections.md",
    }
    present = {p.name for p in artifacts}
    missing = sorted(required - present)
    if missing:
        return {"ok": False, "reason": "missing_artifacts", "missing": missing}
    try:
        short = next(p for p in artifacts if p.name == "short_narrative.txt").read_text(encoding="utf-8")
        long_text = next(p for p in artifacts if p.name == "long_narrative.txt").read_text(encoding="utf-8")
        clusters_raw = next(p for p in artifacts if p.name == "keyword_clusters.json").read_text(encoding="utf-8")
        clusters = json.loads(clusters_raw)
    except Exception as exc:
        return {"ok": False, "reason": f"read_error:{exc}"}
    if len(short.split()) < 8 or len(long_text.split()) < 16:
        return {"ok": False, "reason": "narrative_too_short"}
    if _contains_placeholder(short) or _contains_placeholder(long_text):
        return {"ok": False, "reason": "placeholder_text"}
    if not isinstance(clusters, dict) or not clusters.get("suggested_keywords"):
        return {"ok": False, "reason": "keyword_clusters_invalid"}
    return {"ok": True}


def validate_capability_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    artifacts = [Path(p) for p in (result.get("artifacts") or [])]
    required = {"capability_statement.pdf", "capability_statement.html", "capability_statement_data.json"}
    present = {p.name for p in artifacts}
    missing = sorted(required - present)
    if missing:
        return {"ok": False, "reason": "missing_artifacts", "missing": missing}
    try:
        pdf = next(p for p in artifacts if p.name == "capability_statement.pdf")
        html = next(p for p in artifacts if p.name == "capability_statement.html").read_text(encoding="utf-8")
        data = json.loads(next(p for p in artifacts if p.name == "capability_statement_data.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"read_error:{exc}"}
    if pdf.stat().st_size <= 64:
        return {"ok": False, "reason": "pdf_too_small"}
    if "Capability Statement" not in html:
        return {"ok": False, "reason": "html_missing_heading"}
    for key in ("business_name", "naics_codes", "core_capabilities"):
        if not data.get(key):
            return {"ok": False, "reason": f"data_missing_{key}"}
    if _contains_placeholder(html):
        return {"ok": False, "reason": "placeholder_text"}
    return {"ok": True}
