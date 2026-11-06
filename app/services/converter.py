"""Convert supported input files to PDF bytes.

- PDF            -> used as-is (validated: must open and must not be encrypted)
- Word/Excel/PPT -> LibreOffice headless
- PNG/JPG/JPEG   -> single-page PDF via PyMuPDF
"""
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pymupdf

from ..config import get_settings
from ..schemas import IMAGE_TYPES, OFFICE_TYPES, PDF_TYPES


class ConversionError(Exception):
    """A single file could not be converted; the job continues with the others."""


_WINDOWS_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def find_libreoffice() -> str | None:
    configured = get_settings().libreoffice_path
    if configured:
        return configured if Path(configured).exists() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _WINDOWS_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _check_pdf(data: bytes) -> bytes:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ConversionError("Conversion failed: file is corrupt or password protected")
            if doc.page_count == 0:
                raise ConversionError("Conversion failed: PDF has no pages")
    except ConversionError:
        raise
    except Exception:
        raise ConversionError("Conversion failed: file is corrupt or password protected")
    return data


def _image_to_pdf(data: bytes, file_type: str) -> bytes:
    try:
        with pymupdf.open(stream=data, filetype=file_type) as img:
            pdf_bytes = img.convert_to_pdf()
        return _check_pdf(pdf_bytes)
    except ConversionError:
        raise
    except Exception:
        raise ConversionError("Conversion failed: image is corrupt or unreadable")


def _office_to_pdf(data: bytes, file_type: str) -> bytes:
    soffice = find_libreoffice()
    if not soffice:
        raise ConversionError("Conversion failed: LibreOffice is not available on the server")
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="lo-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / f"input.{file_type}"
        src.write_bytes(data)
        # A unique profile dir per call lets several conversions run in parallel.
        profile = (tmp_path / f"profile-{uuid.uuid4().hex}").resolve().as_uri()
        cmd = [
            soffice,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_path),
            str(src),
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=settings.libreoffice_timeout_seconds,
                check=False,
                env={**os.environ, "HOME": tmp},
            )
        except subprocess.TimeoutExpired:
            raise ConversionError("Conversion failed: timed out converting file")
        out = tmp_path / "input.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise ConversionError("Conversion failed: file is corrupt or password protected")
        return _check_pdf(out.read_bytes())


def to_pdf(data: bytes, file_type: str) -> bytes:
    if file_type in PDF_TYPES:
        return _check_pdf(data)
    if file_type in IMAGE_TYPES:
        return _image_to_pdf(data, file_type)
    if file_type in OFFICE_TYPES:
        return _office_to_pdf(data, file_type)
    raise ConversionError(f"Conversion failed: unsupported file type .{file_type}")
