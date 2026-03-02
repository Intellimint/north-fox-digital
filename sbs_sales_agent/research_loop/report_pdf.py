from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..fulfillment.pdf_render import render_html_to_pdf


# ---------------------------------------------------------------------------
# Markdown → HTML (lightweight, no external deps)
# ---------------------------------------------------------------------------

def _md_to_html(text: str) -> str:
    """Convert a subset of markdown to HTML suitable for the report."""
    lines = str(text).splitlines()
    out: list[str] = []
    in_table = False
    in_ul = False

    def flush_ul() -> None:
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def flush_table() -> None:
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    def inline(s: str) -> str:
        # bold **text** and __text__
        s = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"__(.*?)__", r"<strong>\1</strong>", s)
        # italic *text* and _text_
        s = re.sub(r"\*(.*?)\*", r"<em>\1</em>", s)
        s = re.sub(r"(?<!\w)_(.*?)_(?!\w)", r"<em>\1</em>", s)
        # inline code
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        # URLs
        s = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    for raw_line in lines:
        line = raw_line.rstrip()
        escaped = html.escape(line)

        # horizontal rule
        if re.match(r"^---+$", line.strip()):
            flush_ul()
            flush_table()
            out.append("<hr>")
            continue

        # headings
        hm = re.match(r"^(#{1,4})\s+(.+)$", line)
        if hm:
            flush_ul()
            flush_table()
            level = len(hm.group(1))
            content = inline(html.escape(hm.group(2)))
            tag = f"h{level + 1}"  # h2 for ## etc.
            out.append(f"<{tag}>{content}</{tag}>")
            continue

        # table rows
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # skip separator rows |---|---|
            if all(re.match(r"^[-: ]+$", c) for c in cells if c):
                if not in_table:
                    # convert previous row to thead
                    if out and out[-1].startswith("<tr>"):
                        header_row = out.pop()
                        cells_content = re.findall(r"<td>(.*?)</td>", header_row)
                        thead_cells = "".join(f"<th>{c}</th>" for c in cells_content)
                        out.append(f"<table class='findings-table'><thead><tr>{thead_cells}</tr></thead><tbody>")
                        in_table = True
                else:
                    pass  # skip separator inside an existing table
                continue
            row_html = "<tr>" + "".join(f"<td>{inline(html.escape(c))}</td>" for c in cells) + "</tr>"
            if not in_table:
                out.append(row_html)  # will be converted if separator follows
            else:
                out.append(row_html)
            continue

        flush_table()

        # unordered list
        if re.match(r"^[-*]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            content = inline(html.escape(line.lstrip("-* ").strip()))
            out.append(f"<li>{content}</li>")
            continue

        flush_ul()

        # blank line → paragraph break
        if not line.strip():
            out.append("")
            continue

        # regular paragraph
        out.append(f"<p>{inline(escaped)}</p>")

    flush_ul()
    flush_table()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _make_fallback_chart_png(path: Path, title: str) -> None:
    """Write a valid labeled PNG for use when matplotlib is unavailable."""
    import struct
    import zlib

    width, height = 600, 260
    # Light blue-gray background with text label baked in as solid color block
    r, g, b = 240, 245, 251
    row = b'\x00' + bytes([r, g, b]) * width
    raw_data = row * height
    compressed = zlib.compress(raw_data, 6)

    def _chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    png = (
        b'\x89PNG\r\n\x1a\n'
        + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + _chunk(b'IDAT', compressed)
        + _chunk(b'IEND', b'')
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _chart_placeholder(path: Path, title: str) -> str:
    try:
        import matplotlib.pyplot as plt  # type: ignore

        fig, ax = plt.subplots(figsize=(7.5, 3.5))
        ax.axis("off")
        ax.set_facecolor("#f0f5fb")
        fig.patch.set_facecolor("#f0f5fb")
        ax.text(0.04, 0.6, title, fontsize=15, fontweight="bold", color="#13233a")
        ax.text(0.04, 0.35, "chart unavailable; fallback artifact", fontsize=10, color="#666")
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=120)
        plt.close(fig)
    except Exception:
        _make_fallback_chart_png(path, title)
    return str(path)


def _make_charts(report: dict[str, Any], out_dir: Path) -> list[str]:
    out: list[str] = []
    findings = report.get("findings") or []
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    # Chart 1: findings by category
    path1 = chart_dir / "findings_by_category.png"
    try:
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib  # type: ignore
        matplotlib.rcParams.update({"font.family": "DejaVu Sans"})

        categories = [str(f.get("category") or "unknown") for f in findings]
        c = Counter(categories)
        labels = [k.replace("_", " ").title() for k in list(c.keys())[:8]]
        values = [c[k] for k in list(c.keys())[:8]]
        colors = ["#e63946" if v >= 4 else "#f4a261" if v >= 2 else "#2a9d8f" for v in values]

        fig, ax = plt.subplots(figsize=(8, 4))
        bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=0.8)
        ax.bar_label(bars, padding=3, fontsize=10)
        ax.set_title("Findings by Category", fontsize=13, fontweight="bold", pad=10)
        ax.set_ylabel("Count")
        ax.tick_params(axis="x", labelrotation=20)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(path1, dpi=140, bbox_inches="tight")
        plt.close(fig)
    except Exception:
        _chart_placeholder(path1, "Findings by Category")
    out.append(str(path1))

    # Chart 2: severity distribution
    path2 = chart_dir / "severity_distribution.png"
    try:
        import matplotlib.pyplot as plt  # type: ignore

        sev_order = ["critical", "high", "medium", "low", "info"]
        sev_colors = {"critical": "#e63946", "high": "#f4722b", "medium": "#f4a261", "low": "#2a9d8f", "info": "#457b9d"}
        severities = [str(f.get("severity") or "info") for f in findings]
        c = Counter(severities)
        labels = [s for s in sev_order if s in c]
        values = [c[s] for s in labels]
        colors = [sev_colors.get(s, "#999") for s in labels]

        fig, ax = plt.subplots(figsize=(6, 4))
        wedges, texts, autotexts = ax.pie(
            values, labels=[l.title() for l in labels], autopct="%1.0f%%",
            colors=colors, startangle=140, pctdistance=0.8,
        )
        for at in autotexts:
            at.set_fontsize(10)
        ax.set_title("Severity Distribution", fontsize=13, fontweight="bold")
        fig.tight_layout()
        fig.savefig(path2, dpi=140, bbox_inches="tight")
        plt.close(fig)
    except Exception:
        _chart_placeholder(path2, "Severity Distribution")
    out.append(str(path2))

    # Chart 3: finding severity stacked bar by category
    path3 = chart_dir / "category_severity_breakdown.png"
    try:
        import matplotlib.pyplot as plt  # type: ignore

        cats = ["security", "email_auth", "seo", "ada", "conversion", "performance"]
        cat_labels = [c.replace("_", " ").title() for c in cats]
        sev_stack = ["critical", "high", "medium", "low"]
        sev_colors_stack = {"critical": "#e63946", "high": "#f4722b", "medium": "#f4a261", "low": "#2a9d8f"}

        # Build data without numpy — plain Python lists
        data: dict[str, list[float]] = {sev: [0.0] * len(cats) for sev in sev_stack}
        for f in findings:
            cat = str(f.get("category") or "")
            sev = str(f.get("severity") or "info")
            if cat in cats and sev in data:
                data[sev][cats.index(cat)] += 1.0

        x_pos = list(range(len(cats)))
        fig, ax = plt.subplots(figsize=(9, 3.5))
        bottom = [0.0] * len(cats)
        for sev in sev_stack:
            vals = data[sev]
            ax.bar(x_pos, vals, bottom=bottom, color=sev_colors_stack[sev],
                   label=sev.title(), edgecolor="white", linewidth=0.5)
            bottom = [b + v for b, v in zip(bottom, vals)]

        ax.set_xticks(x_pos)
        ax.set_xticklabels(cat_labels, fontsize=9, rotation=15, ha="right")
        ax.set_title("Finding Severity by Category", fontsize=13, fontweight="bold", pad=8)
        ax.set_ylabel("Finding count", fontsize=9)
        ax.legend(loc="upper right", fontsize=9, framealpha=0.85)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(path3, dpi=140, bbox_inches="tight")
        plt.close(fig)
    except Exception:
        _chart_placeholder(path3, "Finding Severity by Category")
    out.append(str(path3))

    # Chart 4: per-category risk heat map (deduction-based health score per category)
    path4 = chart_dir / "category_risk_scores.png"
    try:
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib.patches as mpatches  # type: ignore

        sev_deductions = {"critical": 18, "high": 10, "medium": 5, "low": 2, "info": 0}
        cats4 = ["security", "email_auth", "seo", "ada", "conversion", "performance"]
        cat_labels4 = [c.replace("_", " ").title() for c in cats4]

        cat_deductions: dict[str, int] = {c: 0 for c in cats4}
        for f in findings:
            cat = str(f.get("category") or "")
            sev = str(f.get("severity") or "info")
            if cat in cat_deductions:
                cat_deductions[cat] += sev_deductions.get(sev, 0)

        # Risk score per category: 100 - deductions, floored at 0
        scores4 = [max(0, 100 - cat_deductions[c]) for c in cats4]
        bar_colors4 = [
            "#e63946" if s < 50 else "#f4a261" if s < 70 else "#f4d47c" if s < 85 else "#2a9d8f"
            for s in scores4
        ]

        fig, ax = plt.subplots(figsize=(8.5, 3.5))
        bars4 = ax.barh(cat_labels4[::-1], scores4[::-1], color=bar_colors4[::-1],
                        edgecolor="white", linewidth=0.5, height=0.6)
        ax.bar_label(bars4, fmt="%d", padding=4, fontsize=10, color="#13233a")
        ax.set_xlim(0, 110)
        ax.set_xlabel("Category Health Score (100 = no issues)", fontsize=9)
        ax.set_title("Category Risk Health Scores", fontsize=13, fontweight="bold", pad=10)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", labelsize=10)
        # Legend patches
        legend_handles = [
            mpatches.Patch(color="#e63946", label="Critical risk (<50)"),
            mpatches.Patch(color="#f4a261", label="High risk (50–69)"),
            mpatches.Patch(color="#f4d47c", label="Medium risk (70–84)"),
            mpatches.Patch(color="#2a9d8f", label="Good (85+)"),
        ]
        ax.legend(handles=legend_handles, loc="lower right", fontsize=8, framealpha=0.85)
        ax.axvline(x=70, color="#ccc", linewidth=0.8, linestyle="--")
        fig.tight_layout()
        fig.savefig(path4, dpi=140, bbox_inches="tight")
        plt.close(fig)
    except Exception:
        _chart_placeholder(path4, "Category Risk Health Scores")
    out.append(str(path4))

    return out


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.65;
    color: #13233a;
    margin: 0;
    padding: 0;
    background: #fff;
}
.page-wrap { max-width: 920px; margin: 0 auto; padding: 20px 28px 34px; }
/* Header band */
.report-header {
    background: linear-gradient(135deg, #1a2f5a 0%, #0f4c81 100%);
    color: #fff;
    padding: 28px 36px 22px;
    border-radius: 8px 8px 0 0;
    margin-bottom: 0;
}
.report-header h1 { font-size: 24px; font-weight: 700; letter-spacing: -0.3px; }
.report-header .subtitle { font-size: 13px; opacity: 0.85; margin-top: 5px; }
/* Meta card */
.meta-card {
    background: #f0f5fb;
    border: 1px solid #d0dce8;
    border-radius: 0 0 0 0;
    padding: 14px 36px;
    display: flex;
    gap: 40px;
    flex-wrap: wrap;
    margin-bottom: 10px;
    font-size: 12px;
    color: #3a4f6a;
}
.meta-card strong { color: #13233a; }
/* Health scorecard */
.health-card {
    display: flex;
    gap: 0;
    border: 1px solid #d0dce8;
    border-top: none;
    border-radius: 0 0 8px 8px;
    margin-bottom: 14px;
    overflow: hidden;
}
.health-score-cell {
    background: #fff;
    padding: 16px 24px;
    border-right: 1px solid #d0dce8;
    min-width: 160px;
    text-align: center;
}
.hs-label { font-size: 11px; color: #6b7c93; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.hs-number { font-size: 36px; font-weight: 800; line-height: 1.1; }
.hs-sublabel { font-size: 11px; font-weight: 600; margin-top: 3px; }
.hs-critical { color: #c0392b; }
.hs-below-avg { color: #e63946; }
.hs-average { color: #f4722b; }
.hs-above-avg { color: #e8a800; }
.hs-strong { color: #2a9d8f; }
.sev-tally {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: space-around;
    padding: 12px 16px;
    flex-wrap: wrap;
    gap: 10px;
    background: #fafcff;
}
.sev-chip { display: flex; flex-direction: column; align-items: center; min-width: 56px; }
.sev-chip .count { font-size: 22px; font-weight: 800; }
.sev-chip .label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; }
.c-critical { color: #c0392b; }
.c-high { color: #e74c3c; }
.c-medium { color: #e67e22; }
.c-low { color: #27ae60; }
/* Quick wins */
.quick-wins {
    background: #fffbe6;
    border: 1px solid #f4d03f;
    border-left: 4px solid #f4d03f;
    border-radius: 6px;
    padding: 14px 18px;
    margin: 0 0 12px 0;
}
.quick-wins h3 { font-size: 13px; color: #7d6608; margin: 0 0 8px 0; }
.quick-wins ul { margin-left: 16px; }
.quick-wins li { font-size: 12px; color: #5d4e07; margin-bottom: 3px; }
/* Section */
section {
    margin-bottom: 16px;
    page-break-inside: avoid;
}
h2 {
    font-size: 17px;
    font-weight: 700;
    color: #0f4c81;
    border-bottom: 2px solid #d0dce8;
    padding-bottom: 6px;
    margin: 10px 0 8px;
}
h3 {
    font-size: 14px;
    font-weight: 700;
    color: #1a3a6a;
    margin: 18px 0 8px;
}
h4 { font-size: 13px; font-weight: 600; margin: 12px 0 6px; color: #2a4a7a; }
p { margin-bottom: 10px; }
ul { margin: 6px 0 10px 18px; }
ul li { margin-bottom: 4px; }
/* Tables */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0 12px;
    font-size: 12px;
}
table.findings-table th {
    background: #0f4c81;
    color: #fff;
    padding: 8px 10px;
    text-align: left;
    font-weight: 600;
}
table.findings-table td, table td {
    border: 1px solid #d0dce8;
    padding: 7px 10px;
    vertical-align: top;
}
table.findings-table tr:nth-child(even) td { background: #f7fafd; }
/* Charts */
.chart-grid {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin: 8px 0 10px;
}
.chart-grid img {
    max-width: 100%;
    border: 1px solid #e0e8f0;
    border-radius: 6px;
    flex: 1 1 280px;
}
/* Screenshots */
.screenshot-grid {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin: 8px 0 10px;
}
.screenshot-grid img {
    max-width: 49%;
    min-width: 320px;
    flex: 1 1 420px;
    border: 1px solid #d0dce8;
    border-radius: 6px;
}
@page { size: Letter; margin: 0.38in; }
@media print {
  h2, h3 { page-break-after: avoid; }
  table, .chart-grid, .screenshot-grid { page-break-inside: avoid; }
}
/* HR */
hr { border: none; border-top: 1px solid #e0e8f0; margin: 20px 0; }
/* Roadmap table */
.roadmap-table th { background: #1a3a6a; color: #fff; }
/* Code / evidence */
code {
    font-family: 'Courier New', monospace;
    font-size: 11px;
    background: #f0f5fb;
    padding: 1px 5px;
    border-radius: 3px;
}
strong { font-weight: 700; }
em { font-style: italic; }
/* Cover page */
.cover-page {
    background: linear-gradient(160deg, #0f2a4d 0%, #1a4a8a 60%, #1d6fa8 100%);
    min-height: 860px;
    padding: 80px 60px 60px;
    color: #ffffff;
    display: flex;
    flex-direction: column;
    page-break-after: always;
    margin: -20px -28px 0;
    width: calc(100% + 56px);
}
.cover-eyebrow {
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: rgba(255,255,255,0.55);
    margin-bottom: 14px;
}
.cover-report-title {
    font-size: 34px;
    font-weight: 800;
    line-height: 1.15;
    color: #ffffff;
    margin-bottom: 10px;
    letter-spacing: -0.5px;
}
.cover-report-sub {
    font-size: 15px;
    color: rgba(255,255,255,0.65);
    font-weight: 400;
    margin-bottom: 0;
}
.cover-biz-block {
    margin-top: auto;
    border-top: 1px solid rgba(255,255,255,0.18);
    padding-top: 32px;
}
.cover-biz-name {
    font-size: 28px;
    font-weight: 700;
    color: #ffffff;
    margin-bottom: 6px;
}
.cover-biz-url {
    font-size: 13px;
    color: rgba(255,255,255,0.6);
    margin-bottom: 28px;
    word-break: break-all;
}
.cover-score-row {
    display: flex;
    gap: 20px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 28px;
}
.cover-score-badge {
    background: rgba(255,255,255,0.10);
    border: 2px solid rgba(255,255,255,0.25);
    border-radius: 12px;
    padding: 14px 22px;
    text-align: center;
    min-width: 120px;
}
.cover-score-num {
    font-size: 38px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 4px;
}
.cover-score-lbl {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    opacity: 0.7;
}
.cover-score-status {
    font-size: 11px;
    margin-top: 5px;
    opacity: 0.85;
}
.cover-findings-badge {
    font-size: 14px;
    color: rgba(255,255,255,0.75);
    line-height: 1.4;
}
.cover-findings-badge strong {
    color: #ffffff;
    font-size: 26px;
    display: block;
}
.cover-meta-row {
    display: flex;
    gap: 28px;
    flex-wrap: wrap;
    border-top: 1px solid rgba(255,255,255,0.12);
    padding-top: 18px;
}
.cover-meta-item {
    font-size: 10px;
    color: rgba(255,255,255,0.45);
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
"""


def _compute_health_score(findings: list[dict]) -> int:
    """0–100 health score: higher is healthier."""
    deductions = 0
    for f in findings:
        deductions += {"critical": 18, "high": 10, "medium": 5, "low": 2, "info": 0}.get(
            str(f.get("severity") or ""), 0
        )
    return max(0, min(100, 100 - deductions))


def _severity_tally(findings: list[dict]) -> dict[str, int]:
    tally: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = str(f.get("severity") or "info")
        if sev in tally:
            tally[sev] += 1
    return tally


def _build_health_card_html(findings: list[dict]) -> str:
    score = _compute_health_score(findings)
    tally = _severity_tally(findings)
    if score < 70:
        cls, label = "hs-critical", "Failing (Urgent)"
    elif score < 85:
        cls, label = "hs-average", "Needs Improvement"
    elif score < 95:
        cls, label = "hs-above-avg", "Stable"
    else:
        cls, label = "hs-strong", "Strong"

    sev_chips = "".join([
        f"<div class='sev-chip'>"
        f"<span class='count c-{sev}'>{count}</span>"
        f"<span class='label'>{sev.title()}</span>"
        f"</div>"
        for sev, count in tally.items()
        if count > 0
    ]) or "<div class='sev-chip'><span class='count' style='color:#2a9d8f'>0</span><span class='label'>Issues</span></div>"

    return (
        "<div class='health-card'>"
        "<div class='health-score-cell'>"
        "<div class='hs-label'>Web Health Score</div>"
        f"<div class='hs-number {cls}'>{score}/100</div>"
        f"<div class='hs-sublabel {cls}'>{label}</div>"
        "</div>"
        f"<div class='sev-tally'>{sev_chips}</div>"
        "</div>"
    )


def _build_quick_wins_html(findings: list[dict]) -> str:
    sev_rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
    sorted_f = sorted(findings, key=lambda x: sev_rank.get(str(x.get("severity") or ""), 0), reverse=True)
    top = sorted_f[:4]
    if not top:
        return ""
    items = "".join(
        f"<li><strong>[{html.escape(str(f.get('severity', '')).upper())}]</strong> "
        f"{html.escape(str(f.get('title') or ''))}</li>"
        for f in top
    )
    return (
        "<div class='quick-wins'>"
        "<h3>⚡ Quick Win Priorities — Act on These First</h3>"
        f"<ul>{items}</ul>"
        "</div>"
    )


def _safe_img_tag(path: str, css_class: str = "", alt: str = "screenshot") -> str:
    p = Path(path)
    if p.exists() and p.stat().st_size > 100:
        escaped = html.escape(str(p.resolve()))
        cls = f" class='{css_class}'" if css_class else ""
        return f"<img src='file://{escaped}' alt='{alt}'{cls}/>"
    return ""


def _professional_base_filename(report: dict[str, Any]) -> str:
    biz = str((report.get("business") or {}).get("business_name") or "Business")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", biz).strip("_")
    slug = slug[:60] if slug else "Business"
    date_tag = datetime.now().strftime("%Y%m%d")
    return f"North_Fox_Web_Presence_Risk_Growth_Report_{slug}_{date_tag}"


def _render_sections(report: dict[str, Any]) -> str:
    sections = report.get("sections") or []
    blocks: list[str] = []
    skip_keys = {"roadmap"}  # rendered separately in the roadmap snapshot
    for sec in sections:
        key = str(sec.get("key") or "")
        if key in skip_keys:
            continue
        title = html.escape(str(sec.get("title") or "Section"))
        body_md = str(sec.get("body") or "")
        body_html = _md_to_html(body_md)
        blocks.append(f"<section><h2>{title}</h2>{body_html}</section>")
    return "\n".join(blocks)


def _roadmap_html(report: dict[str, Any]) -> str:
    for sec in report.get("sections") or []:
        if str(sec.get("key")) == "roadmap":
            return _md_to_html(str(sec.get("body") or ""))
    return "<p>No roadmap data available.</p>"


def _has_roadmap_table(report: dict[str, Any]) -> bool:
    for sec in report.get("sections") or []:
        if str(sec.get("key")) != "roadmap":
            continue
        body = str(sec.get("body") or "")
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        has_pipe_rows = sum(1 for ln in lines if ln.startswith("|") and ln.endswith("|"))
        has_separator = any(re.match(r"^\|?[\s:-]+\|[\s|:-]+\|?$", ln.replace("---", "-")) for ln in lines)
        if has_pipe_rows >= 3 and has_separator:
            return True
    return False


def _count_roadmap_buckets(report: dict[str, Any]) -> int:
    """Count how many distinct time-window buckets (0-30, 31-60, 61-90) appear in the roadmap table.

    Returns an integer 0-3. All three present means the report has comprehensive short/mid/long-term coverage.
    """
    for sec in report.get("sections") or []:
        if str(sec.get("key")) != "roadmap":
            continue
        body = str(sec.get("body") or "")
        buckets_seen: set[str] = set()
        for bucket_re, label in [
            (re.compile(r"0.30\s+day", re.IGNORECASE), "0-30"),
            (re.compile(r"31.60\s+day", re.IGNORECASE), "31-60"),
            (re.compile(r"61.90\s+day", re.IGNORECASE), "61-90"),
        ]:
            if bucket_re.search(body):
                buckets_seen.add(label)
        return len(buckets_seen)
    return 0


def _value_model_metrics(report: dict[str, Any]) -> dict[str, int]:
    value_model = report.get("value_model") or {}
    scenarios = list(value_model.get("scenarios") or []) if isinstance(value_model, dict) else []
    base = next(
        (row for row in scenarios if str(row.get("name") or "").strip().lower() == "base"),
        None,
    )
    return {
        "value_model_scenarios": len(scenarios),
        "value_model_base_monthly_upside": int(base.get("incremental_revenue_monthly_usd") or 0)
        if isinstance(base, dict)
        else 0,
        "value_model_base_payback_days": int(base.get("payback_days_for_report_fee") or 0)
        if isinstance(base, dict)
        else 0,
    }


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _build_cover_page_html(
    business_name: str,
    website: str,
    health_score: int,
    report_date: str,
    finding_count: int,
    high_critical_count: int,
) -> str:
    """Render a polished cover page as the first page of the PDF."""
    if health_score < 70:
        health_label = "Failing — Urgent Action Required"
        score_color = "#ff6b6b"
    elif health_score < 85:
        health_label = "Needs Improvement"
        score_color = "#ffa94d"
    elif health_score < 95:
        health_label = "Stable"
        score_color = "#ffd43b"
    else:
        health_label = "Strong"
        score_color = "#69db7c"

    return (
        "<div class='cover-page'>"
        "<div class='cover-eyebrow'>Confidential &nbsp;·&nbsp; Web Presence Audit</div>"
        "<div class='cover-report-title'>Web Presence Risk +<br>Revenue Growth Report</div>"
        "<div class='cover-report-sub'>Security · SEO · ADA Accessibility · Conversion Optimization</div>"
        "<div class='cover-biz-block'>"
        f"<div class='cover-biz-name'>{html.escape(business_name)}</div>"
        f"<div class='cover-biz-url'>{html.escape(website)}</div>"
        "<div class='cover-score-row'>"
        "<div class='cover-score-badge'>"
        f"<div class='cover-score-num' style='color:{score_color}'>{health_score}/100</div>"
        "<div class='cover-score-lbl'>Web Health Score</div>"
        f"<div class='cover-score-status'>{html.escape(health_label)}</div>"
        "</div>"
        "<div class='cover-findings-badge'>"
        f"<strong>{finding_count}</strong>"
        f"findings identified<br>"
        f"<span style='font-size:12px;color:rgba(255,255,255,0.65)'>"
        f"{high_critical_count} high/critical priority</span>"
        "</div>"
        "</div>"
        "<div class='cover-meta-row'>"
        f"<div class='cover-meta-item'>Prepared: {html.escape(report_date)}</div>"
        "<div class='cover-meta-item'>For Business Owner Use Only</div>"
        "<div class='cover-meta-item'>Report Value: $299</div>"
        "</div>"
        "</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_pdf_report(report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = _make_charts(report, out_dir)

    # Collect valid screenshots (up to 3 for the evidence section)
    screenshot_paths: list[str] = []
    for v in (report.get("screenshots") or {}).values():
        p = Path(str(v))
        if p.exists() and p.stat().st_size > 100:
            screenshot_paths.append(str(p))
    screenshot_paths = screenshot_paths[:3]

    sections_html = _render_sections(report)
    roadmap_html = _roadmap_html(report)
    roadmap_present = _has_roadmap_table(report)
    roadmap_bucket_count = _count_roadmap_buckets(report)
    value_model_meta = _value_model_metrics(report)

    chart_html = "<div class='chart-grid'>" + "".join([
        _safe_img_tag(c, alt="chart") for c in charts
    ]) + "</div>"

    shot_html = ""
    if screenshot_paths:
        shots = "".join([_safe_img_tag(s, alt="page screenshot") for s in screenshot_paths])
        shot_html = f"<div class='screenshot-grid'>{shots}</div>"

    business = report.get("business") or {}
    scan = report.get("scan") or {}
    findings = report.get("findings") or []
    high_count = sum(1 for f in findings if f.get("severity") in {"high", "critical"})

    biz_name = html.escape(str(business.get("business_name") or "Business"))
    website = html.escape(str(business.get("website") or scan.get("base_url") or ""))
    contact = html.escape(str(business.get("contact_name") or "Owner"))
    pages_count = len(scan.get("pages") or [])

    health_card_html = _build_health_card_html(findings)
    quick_wins_html = _build_quick_wins_html(findings)
    health_score = _compute_health_score(findings)
    report_date = datetime.now().strftime("%B %d, %Y")
    cover_html = _build_cover_page_html(
        business_name=str(business.get("business_name") or "Business"),
        website=str(business.get("website") or scan.get("base_url") or ""),
        health_score=health_score,
        report_date=report_date,
        finding_count=len(findings),
        high_critical_count=high_count,
    )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Web Presence Risk + Growth Report — {biz_name}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page-wrap">

{cover_html}

<div class="report-header">
  <h1>Web Presence Risk + Growth Report</h1>
  <div class="subtitle">{biz_name} &nbsp;|&nbsp; {website}</div>
</div>

<div class="meta-card">
  <div><strong>Prepared for:</strong> {contact}</div>
  <div><strong>Website:</strong> {website}</div>
  <div><strong>Pages analyzed:</strong> {pages_count}</div>
  <div><strong>Total findings:</strong> {len(findings)} ({high_count} high/critical)</div>
</div>

{health_card_html}

{quick_wins_html}

{sections_html}

<h2>Visual Evidence</h2>
{shot_html if shot_html else "<p><em>Screenshots were not captured for this scan pass.</em></p>"}

<h2>Risk Overview Charts</h2>
{chart_html}

<h2>30/60/90 Day Action Roadmap</h2>
{roadmap_html}

</div>
</body>
</html>
"""

    base_name = _professional_base_filename(report)
    html_path = out_dir / f"{base_name}.html"
    html_path.write_text(html_doc, encoding="utf-8")
    json_path = out_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    pdf_path = out_dir / f"{base_name}.pdf"
    render_result = render_html_to_pdf(html_path, pdf_path)

    # Backward-compatible aliases for existing tooling.
    (out_dir / "report.html").write_text(html_doc, encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    if pdf_path.exists():
        (out_dir / "report.pdf").write_bytes(pdf_path.read_bytes())

    return {
        "html_path": str(html_path),
        "json_path": str(json_path),
        "pdf_path": str(pdf_path),
        "renderer": str(render_result.get("renderer") or "unknown"),
        "chart_paths": charts,
        "screenshot_count": str(len(screenshot_paths)),
        "roadmap_present": roadmap_present,
        "roadmap_bucket_count": roadmap_bucket_count,
        "cover_page_present": True,
        **value_model_meta,
    }
