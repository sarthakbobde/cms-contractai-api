"""Background worker for A.7: convert each file, merge, add contents page, store."""
import logging
import math

import pymupdf

from ..schemas import CombineFile, ContentsEntry, FailedFile, JobStatus
from .converter import ConversionError, to_pdf
from .jobs import CombineJob, utcnow
from .storage import store_output

log = logging.getLogger(__name__)

A4 = pymupdf.paper_rect("a4")
ENTRIES_PER_CONTENTS_PAGE = 32


def run_combine_job(job: CombineJob, files: list[CombineFile], contents: list[bytes]) -> None:
    """Runs in a worker thread. Never raises; outcome is recorded on the job."""
    try:
        _run(job, files, contents)
    except Exception:
        log.exception("Combine job %s crashed", job.job_id)
        job.status = JobStatus.failed
        job.error_code = "INTERNAL_ERROR"
        job.error_message = "An unexpected error occurred. Contact support with request timestamp."
        job.message = job.error_message
        job.completed_at = utcnow()


def _run(job: CombineJob, files: list[CombineFile], contents: list[bytes]) -> None:
    job.status = JobStatus.running
    job.message = "Converting files to PDF."

    converted: list[tuple[CombineFile, bytes]] = []
    for f, data in sorted(zip(files, contents), key=lambda p: p[0].order):
        try:
            converted.append((f, to_pdf(data, f.file_type)))
        except ConversionError as exc:
            job.failed_files.append(FailedFile(file_name=f.file_name, reason=str(exc)))
        job.converted_files += 1

    if not converted:
        job.status = JobStatus.failed
        job.error_code = "CONVERSION_FAILED"
        job.error_message = "No files could be converted to PDF"
        job.message = job.error_message
        job.completed_at = utcnow()
        return

    job.message = "Merging PDFs."
    merged = pymupdf.open()
    entries: list[ContentsEntry] = []
    contents_pages = (
        math.ceil(len(converted) / ENTRIES_PER_CONTENTS_PAGE) if job.include_contents_page else 0
    )
    for f, pdf_bytes in converted:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as src:
            entries.append(ContentsEntry(file_name=f.file_name, start_page=contents_pages + merged.page_count + 1))
            merged.insert_pdf(src)

    if job.include_contents_page:
        _insert_contents_pages(merged, job, entries, contents_pages)

    _add_bookmarks(merged, entries, contents_pages)
    pdf_bytes = merged.tobytes(garbage=3, deflate=True)
    job.total_pages = merged.page_count
    merged.close()

    job.download_url = store_output(job.job_id, job.output_filename, pdf_bytes)
    job.contents = entries
    job.merged_files = len(converted)
    job.status = JobStatus.completed
    job.message = "Completed."
    job.completed_at = utcnow()


def _insert_contents_pages(doc: pymupdf.Document, job: CombineJob, entries: list[ContentsEntry], n_pages: int):
    margin = 60
    for p in range(n_pages):
        page = doc.new_page(pno=p, width=A4.width, height=A4.height)
        y = margin + 10
        if p == 0:
            page.insert_text((margin, y), "Contents", fontsize=20, fontname="hebo")
            y += 24
            page.insert_text((margin, y), f"Contract: {job.contract_id}", fontsize=10, fontname="helv",
                             color=(0.35, 0.35, 0.35))
            y += 30
        else:
            page.insert_text((margin, y), "Contents (continued)", fontsize=14, fontname="hebo")
            y += 30
        chunk = entries[p * ENTRIES_PER_CONTENTS_PAGE:(p + 1) * ENTRIES_PER_CONTENTS_PAGE]
        right = A4.width - margin
        for i, e in enumerate(chunk, start=p * ENTRIES_PER_CONTENTS_PAGE + 1):
            name = f"{i}. {e.file_name}"
            if len(name) > 80:
                name = name[:77] + "..."
            page_no = str(e.start_page)
            page.insert_text((margin, y), name, fontsize=11, fontname="helv")
            width = pymupdf.get_text_length(page_no, fontname="helv", fontsize=11)
            page.insert_text((right - width, y), page_no, fontsize=11, fontname="helv")
            # Clickable link to the section start (0-based page index)
            rect = pymupdf.Rect(margin, y - 11, right, y + 3)
            page.insert_link({"kind": pymupdf.LINK_GOTO, "from": rect, "page": e.start_page - 1})
            y += 20


def _add_bookmarks(doc: pymupdf.Document, entries: list[ContentsEntry], contents_pages: int):
    toc = [[1, "Contents", 1]] if contents_pages else []
    toc += [[1, e.file_name, e.start_page] for e in entries]
    doc.set_toc(toc)
