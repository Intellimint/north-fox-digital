from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import AgentSettings
from ..integrations.codex_fulfillment import CodexFulfillmentClient
from ..models import ProspectFeatures
from .pdf_render import render_html_to_pdf


def _write_dsbs_outputs(out_dir: Path, *, short: str, long_text: str, clusters: dict[str, Any], diffs: list[str], paste: str) -> dict[str, Any]:
    (out_dir / "short_narrative.txt").write_text(short.strip() + "\n", encoding="utf-8")
    (out_dir / "long_narrative.txt").write_text(long_text.strip() + "\n", encoding="utf-8")
    (out_dir / "keyword_clusters.json").write_text(json.dumps(clusters, indent=2), encoding="utf-8")
    (out_dir / "differentiators.txt").write_text("\n".join(f"- {d}" for d in diffs) + "\n", encoding="utf-8")
    (out_dir / "paste_ready_dsbs_sections.md").write_text(paste.strip() + "\n", encoding="utf-8")
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: Georgia, serif; margin: 28px; color: #1f2328; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
h2 {{ margin-top: 16px; font-size: 16px; }}
ul {{ margin-left: 20px; }}
</style></head><body>
<h1>DSBS Profile Rewrite Deliverable</h1>
<h2>Short Narrative</h2><p>{short}</p>
<h2>Long Narrative</h2><p>{long_text}</p>
<h2>Differentiators</h2><ul>{''.join(f'<li>{d}</li>' for d in diffs)}</ul>
<h2>Suggested Keywords</h2><p>{', '.join(clusters.get('suggested_keywords') or [])}</p>
</body></html>"""
    html_path = out_dir / "dsbs_rewrite_deliverable.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = out_dir / "dsbs_rewrite_deliverable.pdf"
    render_result = render_html_to_pdf(html_path, pdf_path)
    return {
        "artifacts": [
            str(pdf_path),
            str(html_path),
            str(out_dir / "short_narrative.txt"),
            str(out_dir / "long_narrative.txt"),
            str(out_dir / "keyword_clusters.json"),
            str(out_dir / "differentiators.txt"),
            str(out_dir / "paste_ready_dsbs_sections.md"),
        ],
        "render_result": render_result,
    }


def build_dsbs_rewrite_artifacts(
    *,
    prospect: ProspectFeatures,
    out_dir: Path,
    settings: AgentSettings | None = None,
    website_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if settings is not None:
        codex = CodexFulfillmentClient(settings)
        if codex.enabled():
            payload = {
                "business_name": prospect.business_name,
                "contact_name": prospect.contact_name_normalized,
                "naics_primary": prospect.naics_primary,
                "naics_all_codes": prospect.naics_all_codes[:10],
                "keywords": prospect.keywords[:20],
                "certifications": prospect.certs[:12],
                "capabilities_narrative": prospect.capabilities_narrative or "",
                "website_context": website_context or {},
            }
            generated = codex.generate(task="dsbs_rewrite", payload=payload)
            if generated.get("ok") and isinstance(generated, dict):
                short = str(generated.get("short_narrative") or "").strip()
                long_text = str(generated.get("long_narrative") or "").strip()
                clusters = generated.get("keyword_clusters")
                diffs = generated.get("differentiators")
                if (
                    short
                    and long_text
                    and isinstance(clusters, dict)
                    and isinstance(diffs, list)
                    and all(isinstance(x, str) and x.strip() for x in diffs)
                ):
                    paste = str(generated.get("paste_ready_sections") or f"Short narrative:\n{short}\n\nLong narrative:\n{long_text}")
                    out = _write_dsbs_outputs(
                        out_dir,
                        short=short,
                        long_text=long_text,
                        clusters=clusters,
                        diffs=[str(x).strip() for x in diffs][:8],
                        paste=paste,
                    )
                    out["generation_path"] = "codex"
                    return out

    short = (
        f"{prospect.business_name} provides {', '.join(prospect.keywords[:4]) or 'specialized services'} "
        f"with a focus on NAICS {prospect.naics_primary or 'alignment'}."
    )
    if website_context and website_context.get("ok"):
        title = str(website_context.get("title") or "").strip()
        if title:
            short = (
                f"{prospect.business_name} provides {', '.join(prospect.keywords[:4]) or 'specialized services'} "
                f"with a focus on {title} and NAICS {prospect.naics_primary or 'alignment'}."
            )
    long_text = (
        f"{prospect.business_name} is a small business"
        f"{' in ' + prospect.city if prospect.city else ''}"
        f"{', ' + prospect.state if prospect.state else ''} offering "
        f"{', '.join(prospect.keywords[:8]) or 'capability support'}."
    )
    clusters = {
        "naics_primary": prospect.naics_primary,
        "suggested_keywords": prospect.keywords[:12],
        "naics_all_codes": prospect.naics_all_codes[:10],
    }
    diffs = [
        "Clearer capability language for buyer search matching",
        "NAICS-aligned keyword phrasing",
        "Stronger one-paragraph DSBS positioning summary",
    ]
    paste = f"Short narrative:\n{short}\n\nLong narrative:\n{long_text}\n"
    out = _write_dsbs_outputs(out_dir, short=short, long_text=long_text, clusters=clusters, diffs=diffs, paste=paste)
    out["generation_path"] = "deterministic_fallback"
    return out
