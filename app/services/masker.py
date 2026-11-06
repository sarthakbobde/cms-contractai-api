"""A.8: mask sensitive fields and return the document in its original format.

Supported formats: .docx, .pdf, .xlsx, .txt, .csv
Everything is processed in memory; the original file is never written to disk.
"""
import io
from collections import Counter

import pymupdf
from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from openpyxl import load_workbook

from ..config import get_settings
from .mask_rules import MASK_RULES

MASK_TYPES = ["docx", "pdf", "xlsx", "txt", "csv"]
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


class MaskingError(Exception):
    pass


# ----------------------------------------------------------------- text core
def find_spans(text: str) -> list[tuple[int, int, str]]:
    """Return non-overlapping (start, end, field_name) spans to mask."""
    taken: list[tuple[int, int, str]] = []
    for rule in MASK_RULES:
        for m in rule.pattern.finditer(text):
            start, end = m.span(rule.group)
            if start == end:
                continue
            value = m.group(rule.group)
            if rule.check and not rule.check(value):
                continue
            if any(start < e and s < end for s, e, _ in taken):
                continue
            taken.append((start, end, rule.name))
    return sorted(taken)


def mask_text(text: str, counts: Counter) -> str:
    """Mask matched values, keeping length and whitespace so layout is preserved."""
    spans = find_spans(text)
    if not spans:
        return text
    ch = get_settings().mask_char
    chars = list(text)
    for s, e, name in spans:
        counts[name] += 1
        for i in range(s, e):
            if not chars[i].isspace():
                chars[i] = ch
    return "".join(chars)


# ----------------------------------------------------------------- formats
def _mask_docx(data: bytes, counts: Counter) -> bytes:
    doc = Document(io.BytesIO(data))
    roots = [doc.element.body]
    seen = set()
    for section in doc.sections:
        for part in (section.header, section.first_page_header, section.even_page_header,
                     section.footer, section.first_page_footer, section.even_page_footer):
            el = part._element
            if id(el) not in seen and not part.is_linked_to_previous:
                seen.add(id(el))
                roots.append(el)
    # Footnotes / endnotes / comments parts, if present. Parts python-docx doesn't
    # model are plain blobs: parse them, mask, and write the XML back afterwards.
    raw_parts = []
    for rel in doc.part.rels.values():
        if rel.is_external or not rel.reltype.endswith(("/footnotes", "/endnotes", "/comments")):
            continue
        part = rel.target_part
        if hasattr(part, "element"):
            roots.append(part.element)
        else:
            el = etree.fromstring(part.blob)
            raw_parts.append((part, el))
            roots.append(el)

    p_tag, t_tags = qn("w:p"), (qn("w:t"), qn("w:delText"))
    for root in roots:
        for p in root.iter(p_tag):
            # Only text nodes whose nearest paragraph is this one (skip nested text boxes,
            # which are visited as their own paragraphs).
            nodes = [
                t for t in p.iter(*t_tags)
                if next(t.iterancestors(p_tag), None) is p
            ]
            if not nodes:
                continue
            original = "".join(t.text or "" for t in nodes)
            masked = mask_text(original, counts)
            if masked == original:
                continue
            pos = 0
            for t in nodes:
                n = len(t.text or "")
                t.text = masked[pos:pos + n]
                pos += n

    for part, el in raw_parts:
        part._blob = etree.tostring(el, xml_declaration=True, encoding="UTF-8", standalone=True)  # noqa: SLF001
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _mask_pdf(data: bytes, counts: Counter) -> bytes:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise MaskingError("File is corrupt or not a valid PDF")
    if doc.needs_pass:
        raise MaskingError("File is password protected")
    with doc:
        for page in doc:
            text = page.get_text()
            values = set()
            for s, e, name in find_spans(text):
                counts[name] += 1
                values.add(text[s:e])
            hit = False
            for value in values:
                rects = page.search_for(value)
                if not rects:  # value wrapped across lines: search each line separately
                    rects = [r for line in value.splitlines() if line.strip() for r in page.search_for(line.strip())]
                for r in rects:
                    page.add_redact_annot(r, fill=(0, 0, 0))
                    hit = True
            if hit:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        doc.set_metadata({})
        return doc.tobytes(garbage=3, deflate=True)


def _mask_xlsx(data: bytes, counts: Counter) -> bytes:
    try:
        wb = load_workbook(io.BytesIO(data))
    except Exception:
        raise MaskingError("File is corrupt or not a valid .xlsx workbook")
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and not cell.value.startswith("="):
                    cell.value = mask_text(cell.value, counts)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _mask_plain(data: bytes, counts: Counter) -> bytes:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return mask_text(text, counts).encode("utf-8")


def mask_document(data: bytes, file_type: str) -> tuple[bytes, Counter]:
    counts: Counter = Counter()
    handler = {
        "docx": _mask_docx,
        "pdf": _mask_pdf,
        "xlsx": _mask_xlsx,
        "txt": _mask_plain,
        "csv": _mask_plain,
    }[file_type]
    try:
        return handler(data, counts), counts
    except MaskingError:
        raise
    except Exception as exc:
        raise MaskingError(f"File is corrupt or could not be processed ({type(exc).__name__})")
