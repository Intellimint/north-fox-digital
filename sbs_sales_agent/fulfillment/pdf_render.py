from __future__ import annotations

import os
import re
import html as _html_module
from pathlib import Path
from typing import Any


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_minimal_pdf(*, pdf_path: Path, lines: list[str]) -> None:
    line_width = 105
    lines_per_page = 48
    wrapped: list[str] = []
    for raw in lines:
        txt = (raw or "").replace("\r", " ").replace("\t", " ")
        if not txt:
            wrapped.append("")
            continue
        while len(txt) > line_width:
            wrapped.append(txt[:line_width])
            txt = txt[line_width:]
        wrapped.append(txt)
    if not wrapped:
        wrapped = [" "]

    pages = [wrapped[i:i + lines_per_page] for i in range(0, len(wrapped), lines_per_page)]

    objects: list[bytes] = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")

    # Placeholder Pages object; kids and count are patched after page object numbers are known.
    objects.append(b"2 0 obj << /Type /Pages /Count 0 /Kids [] >> endobj\n")

    font_obj_num = 3
    objects.append(b"3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")

    page_obj_nums: list[int] = []
    for idx, page_lines in enumerate(pages):
        page_obj_num = 4 + (idx * 2)
        content_obj_num = 5 + (idx * 2)
        page_obj_nums.append(page_obj_num)

        content_lines = ["BT", "/F1 10 Tf", "54 770 Td", "14 TL"]
        for line_idx, line in enumerate(page_lines):
            escaped = _escape_pdf_text(line if line else " ")
            if line_idx == 0:
                content_lines.append(f"({escaped}) Tj")
            else:
                content_lines.append(f"T* ({escaped}) Tj")
        content_lines.append("ET")
        content_stream = ("\n".join(content_lines) + "\n").encode("utf-8")

        objects.append(
            f"{page_obj_num} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> /Contents {content_obj_num} 0 R >> endobj\n".encode("ascii")
        )
        objects.append(
            f"{content_obj_num} 0 obj << /Length {len(content_stream)} >> stream\n".encode("ascii")
            + content_stream
            + b"endstream endobj\n"
        )

    kids = " ".join([f"{n} 0 R" for n in page_obj_nums])
    objects[1] = f"2 0 obj << /Type /Pages /Count {len(page_obj_nums)} /Kids [{kids}] >> endobj\n".encode("ascii")

    header = b"%PDF-1.4\n"
    offset = len(header)
    body = b""
    xref_offsets = [0]
    for obj in objects:
        xref_offsets.append(offset)
        body += obj
        offset += len(obj)

    xref_start = len(header) + len(body)
    xref_rows = ["0000000000 65535 f "]
    for off in xref_offsets[1:]:
        xref_rows.append(f"{off:010d} 00000 n ")
    xref = ("xref\n0 " + str(len(objects) + 1) + "\n" + "\n".join(xref_rows) + "\n").encode("ascii")
    trailer = (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    pdf_path.write_bytes(header + body + xref + trailer)


def _extract_sections_from_html(html_content: str) -> list[str]:
    """Extract meaningful text lines from HTML for text-only fallback rendering."""
    # Strip scripts and styles
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    # Convert block-level elements to newlines for readability
    text = re.sub(r'<(h[1-6]|p|div|section|li|tr|thead|tbody)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<(br|hr)[^>]*/?>','\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities
    text = _html_module.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln and len(ln) > 1][:1000]


def _write_multipage_text_pdf(*, pdf_path: Path, lines: list[str]) -> None:
    """Write a multi-page PDF from lines of text using pure Python."""
    lines_per_page = 50
    page_count = max(1, (len(lines) + lines_per_page - 1) // lines_per_page)

    def _make_content_stream(page_lines: list[str]) -> bytes:
        parts = ["BT", "/F1 9 Tf", "36 750 Td", "12 TL"]
        for idx, line in enumerate(page_lines):
            safe = line[:180].replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if idx == 0:
                parts.append(f"({safe}) Tj")
            else:
                parts.append(f"T* ({safe}) Tj")
        parts.append("ET")
        return ("\n".join(parts) + "\n").encode("utf-8")

    # Build all page objects
    # Object layout:
    #   1: Catalog -> Pages
    #   2: Pages (kids = page_count page objects)
    #   3..2+page_count: Page objects
    #   3+page_count: Font
    #   4+page_count..end: Content streams

    n = page_count
    font_obj_id = 3 + n
    content_streams: list[bytes] = []
    for i in range(n):
        page_lines = lines[i * lines_per_page:(i + 1) * lines_per_page]
        content_streams.append(_make_content_stream(page_lines))

    kid_refs = " ".join(f"{3 + i} 0 R" for i in range(n))
    objects: list[bytes] = []
    objects.append(f"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n".encode())
    objects.append(f"2 0 obj << /Type /Pages /Count {n} /Kids [{kid_refs}] >> endobj\n".encode())

    for i in range(n):
        content_id = font_obj_id + 1 + i
        objects.append(
            f"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj_id} 0 R >> >> /Contents {content_id} 0 R >> endobj\n"
            .replace("3 0 obj", f"{3 + i} 0 obj")
            .encode()
        )

    objects.append(
        f"{font_obj_id} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n".encode()
    )
    for i, cs in enumerate(content_streams):
        obj_id = font_obj_id + 1 + i
        objects.append(
            f"{obj_id} 0 obj << /Length {len(cs)} >> stream\n".encode() + cs + b"endstream endobj\n"
        )

    total_objects = 1 + len(objects)  # 0 + all objects
    header = b"%PDF-1.4\n"
    offset = len(header)
    xref_offsets: list[int] = [0]
    body = b""
    for obj_bytes in objects:
        xref_offsets.append(offset)
        body += obj_bytes
        offset += len(obj_bytes)

    xref_start = len(header) + len(body)
    xref_rows = ["0000000000 65535 f "]
    for off in xref_offsets[1:]:
        xref_rows.append(f"{off:010d} 00000 n ")
    xref = (f"xref\n0 {total_objects}\n" + "\n".join(xref_rows) + "\n").encode("ascii")
    trailer = (
        f"trailer << /Size {total_objects} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n"
        .encode("ascii")
    )
    pdf_path.write_bytes(header + body + xref + trailer)


def _render_with_reportlab(html_path: Path, pdf_path: Path) -> None:
    """Attempt to render HTML content as a structured PDF using ReportLab."""
    from reportlab.lib.pagesizes import letter  # type: ignore
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
    from reportlab.lib.units import inch  # type: ignore
    from reportlab.lib import colors  # type: ignore
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable  # type: ignore

    html_content = html_path.read_text(encoding="utf-8", errors="ignore")
    lines = _extract_sections_from_html(html_content)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    heading_style = ParagraphStyle(
        "Heading",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#0f4c81"),
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        spaceAfter=4,
    )
    story = []
    for line in lines[:600]:
        if len(line) > 120 or line.isupper() or line.startswith("##"):
            clean = re.sub(r'^#+\s*', '', line)
            story.append(Paragraph(clean[:200], heading_style))
        elif line.startswith("-") or line.startswith("•"):
            story.append(Paragraph(f"&bull; {line.lstrip('-• ')[:200]}", body_style))
        elif line.startswith("|"):
            story.append(Paragraph(line[:200], body_style))
        else:
            story.append(Paragraph(line[:300], body_style))
        story.append(Spacer(1, 2))
    doc.build(story)


def _render_with_playwright(html_path: Path, pdf_path: Path) -> None:
    """Render HTML to PDF using Playwright Chromium for high-fidelity layout."""
    from playwright.sync_api import sync_playwright  # type: ignore

    html_content = html_path.read_text(encoding="utf-8", errors="ignore")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})
        page.set_content(html_content, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            print_background=True,
            margin={"top": "0.4in", "right": "0.4in", "bottom": "0.45in", "left": "0.4in"},
            prefer_css_page_size=True,
        )
        browser.close()


def render_capability_data_to_pdf(data: dict[str, Any], pdf_path: Path) -> dict[str, str]:
    caps = [str(x).strip() for x in (data.get("core_capabilities") or []) if str(x).strip()]
    certs = [str(x).strip() for x in (data.get("certifications") or []) if str(x).strip()]
    naics = [str(x).strip() for x in (data.get("naics_codes") or []) if str(x).strip()]
    lines = [
        f"{str(data.get('business_name') or '').strip()}",
        "Capability Statement",
        f"UEI: {str(data.get('uei') or '').strip()}    CAGE: {str(data.get('cage_code') or '').strip()}",
        "",
        "Capability Summary:",
        str(data.get("capability_summary") or "").strip() or "N/A",
        "",
        "Core Capabilities:",
    ]
    if caps:
        lines.extend([f"- {c}" for c in caps[:8]])
    else:
        lines.append("- N/A")
    lines.extend(
        [
            "",
            f"NAICS: {', '.join(naics) if naics else 'N/A'}",
            f"Certifications: {', '.join(certs) if certs else 'N/A'}",
            f"Differentiators: {str(data.get('differentiators') or '').strip() or 'N/A'}",
            "",
            f"Contact: {str(data.get('contact_name') or '').strip()} | {str(data.get('email') or '').strip()} | {str(data.get('phone') or '').strip()}",
            f"Website: {str(data.get('website') or '').strip()}",
        ]
    )
    _write_minimal_pdf(pdf_path=pdf_path, lines=lines)
    return {"ok": "true", "renderer": "capability_data_pdf", "pdf_path": str(pdf_path)}


def render_html_to_pdf(html_path: Path, pdf_path: Path) -> dict[str, str]:
    # On macOS, WeasyPrint's ffi loader often can't see Homebrew dylibs
    # unless fallback search paths are set.
    if os.uname().sysname == "Darwin":
        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        parts = [p for p in existing.split(":") if p]
        for candidate in (
            "/opt/homebrew/lib",
            "/opt/homebrew/opt/libffi/lib",
            "/usr/local/lib",
            "/usr/local/opt/libffi/lib",
        ):
            if candidate not in parts and Path(candidate).exists():
                parts.append(candidate)
        if parts:
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)

    # Tier 1: WeasyPrint (best visual fidelity)
    try:
        from weasyprint import HTML  # type: ignore

        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return {"ok": "true", "renderer": "weasyprint", "pdf_path": str(pdf_path)}
    except Exception:
        pass

    # Tier 2: Playwright Chromium (high fidelity, CSS-aware)
    try:
        _render_with_playwright(html_path, pdf_path)
        return {"ok": "true", "renderer": "playwright", "pdf_path": str(pdf_path)}
    except Exception:
        pass

    # Tier 3: ReportLab (structured text, good readability without CSS rendering)
    try:
        _render_with_reportlab(html_path, pdf_path)
        return {"ok": "true", "renderer": "reportlab", "pdf_path": str(pdf_path)}
    except Exception:
        pass

    # Tier 4: pdfkit / wkhtmltopdf if available in PATH
    try:
        import pdfkit  # type: ignore

        pdfkit.from_file(str(html_path), str(pdf_path))
        return {"ok": "true", "renderer": "pdfkit", "pdf_path": str(pdf_path)}
    except Exception:
        pass

    # Tier 5: Multi-page text PDF — full structured content, no excerpt placeholder
    try:
        html_content = html_path.read_text(encoding="utf-8", errors="ignore")
        lines = _extract_sections_from_html(html_content)
        _write_multipage_text_pdf(pdf_path=pdf_path, lines=lines)
        return {"ok": "true", "renderer": "fallback_minimal_pdf", "pdf_path": str(pdf_path)}
    except Exception:
        # Last resort: single-page minimal PDF with full text extraction
        try:
            html_content = html_path.read_text(encoding="utf-8", errors="ignore")
            lines = _extract_sections_from_html(html_content)
        except Exception:
            lines = ["Web Presence Report — render error; see HTML artifact."]
        _write_minimal_pdf(pdf_path=pdf_path, lines=lines[:43])
        return {"ok": "true", "renderer": "fallback_minimal_pdf", "pdf_path": str(pdf_path)}
