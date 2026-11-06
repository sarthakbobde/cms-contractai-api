import base64
import io
import os
import tempfile

os.environ["JWT_SECRET"] = "test-secret-0123456789abcdef0123456789"
os.environ["WORK_DIR"] = tempfile.mkdtemp(prefix="cms-test-")
os.environ["AUTH_ENABLED"] = "true"

import jwt  # noqa: E402
import pymupdf  # noqa: E402
import pytest  # noqa: E402
from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.converter import find_libreoffice  # noqa: E402

client = TestClient(app)


def token(*roles):
    return jwt.encode({"sub": "test", "roles": list(roles)}, "test-secret-0123456789abcdef0123456789", algorithm="HS256")


AUTH_C = {"Authorization": f"Bearer {token('Documents.Combine')}"}
AUTH_M = {"Authorization": f"Bearer {token('Documents.Mask')}"}


def make_pdf(pages=1, text="Hello") -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"{text} page {i + 1}")
    return doc.tobytes()


def make_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), 0)
    pix.set_rect(pix.irect, (200, 30, 30))
    return pix.tobytes("png")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def combine_body(files, **kw):
    return {
        "contract_id": "CMS-CONTRACT-12345",
        "output_filename": "ACME_MSA_2026_Supporting_Documents.pdf",
        "include_contents_page": True,
        "files": files,
        **kw,
    }


# ------------------------------------------------------------------ auth
def test_missing_token_401():
    r = client.post("/api/v1/documents/combine", json=combine_body([]))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_invalid_token_401():
    r = client.get("/api/v1/documents/combine/x/status", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_wrong_role_403():
    r = client.get("/api/v1/documents/combine/x/status", headers=AUTH_M)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


# ------------------------------------------------------------------ combine
def test_empty_files_validation_error():
    r = client.post("/api/v1/documents/combine", json=combine_body([]), headers=AUTH_C)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert "files must contain at least one item" in err["message"]


def test_unsupported_type():
    files = [{"file_name": "a.zip", "file_type": "zip", "order": 1, "content_base64": b64(b"x")}]
    r = client.post("/api/v1/documents/combine", json=combine_body(files), headers=AUTH_C)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert r.json()["error"]["message"].startswith("File type .zip is not supported. Accepted: pdf, doc")


def test_combine_success_with_partial_failure():
    files = [
        {"file_name": "Company_Seal.png", "file_type": "png", "order": 3, "content_base64": b64(make_png())},
        {"file_name": "Signed_MSA.pdf", "file_type": "pdf", "order": 1, "content_base64": b64(make_pdf(3))},
        {"file_name": "Broken.pdf", "file_type": "pdf", "order": 2, "content_base64": b64(b"not a pdf")},
    ]
    r = client.post("/api/v1/documents/combine", json=combine_body(files), headers=AUTH_C)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert job_id.startswith("combine-")

    s = client.get(f"/api/v1/documents/combine/{job_id}/status", headers=AUTH_C).json()
    assert s["status"] == "completed"
    assert s["total_files"] == 3 and s["merged_files"] == 2
    assert s["total_pages"] == 5  # contents + 3 + 1
    assert s["contents_page_included"] is True
    assert s["contents"] == [
        {"file_name": "Signed_MSA.pdf", "start_page": 2},
        {"file_name": "Company_Seal.png", "start_page": 5},
    ]
    assert s["failed_files"][0]["file_name"] == "Broken.pdf"
    assert s["error_code"] is None and s["error_message"] is None

    d = client.get(f"/api/v1/documents/combine/{job_id}/download", headers=AUTH_C)
    assert d.status_code == 200 and d.headers["content-type"] == "application/pdf"
    with pymupdf.open(stream=d.content, filetype="pdf") as doc:
        assert doc.page_count == 5
        assert "Contents" in doc[0].get_text()


def test_combine_without_contents_page():
    files = [{"file_name": "a.pdf", "file_type": "pdf", "order": 1, "content_base64": b64(make_pdf(2))}]
    r = client.post("/api/v1/documents/combine", json=combine_body(files, include_contents_page=False),
                    headers=AUTH_C)
    s = client.get(f"/api/v1/documents/combine/{r.json()['job_id']}/status", headers=AUTH_C).json()
    assert s["total_pages"] == 2 and s["contents"][0]["start_page"] == 1
    assert s["contents_page_included"] is False


def test_combine_all_fail():
    files = [{"file_name": "bad.pdf", "file_type": "pdf", "order": 1, "content_base64": b64(b"junk")}]
    r = client.post("/api/v1/documents/combine", json=combine_body(files), headers=AUTH_C)
    s = client.get(f"/api/v1/documents/combine/{r.json()['job_id']}/status", headers=AUTH_C).json()
    assert s["status"] == "failed"
    assert s["error_code"] == "CONVERSION_FAILED"


def test_job_not_found():
    r = client.get("/api/v1/documents/combine/combine-doesnotexist/status", headers=AUTH_C)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_blob_not_accessible():
    files = [{"file_name": "Signed_MSA.pdf", "file_type": "pdf", "order": 1,
              "blob_url": "http://127.0.0.1:9/nothing.pdf"}]
    r = client.post("/api/v1/documents/combine", json=combine_body(files), headers=AUTH_C)
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "Unable to access Signed_MSA.pdf in temporary storage"


def test_payload_too_large(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_combined_size_mb", 0)
    files = [{"file_name": "a.pdf", "file_type": "pdf", "order": 1, "content_base64": b64(make_pdf())}]
    r = client.post("/api/v1/documents/combine", json=combine_body(files), headers=AUTH_C)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.skipif(find_libreoffice() is not None, reason="LibreOffice installed")
def test_office_without_libreoffice_503():
    files = [{"file_name": "x.docx", "file_type": "docx", "order": 1, "content_base64": b64(b"x")}]
    r = client.post("/api/v1/documents/combine", json=combine_body(files), headers=AUTH_C)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# ------------------------------------------------------------------ mask
SENSITIVE = (
    "Contact: john.doe@acme.com, phone +44 20 7946 0958. "
    "Account Number: 12345678, Sort Code: 20-00-00. "
    "IBAN GB29 NWBK 6016 1331 9268 19. Card 4111 1111 1111 1111. "
    "Total Contract Value: USD 1,250,000. Effective 2026-11-05, clause 12.3."
)


def mask(name, data, **form):
    fields = {"contract_id": "CMS-CONTRACT-12345", "version": "2.0", **form}
    return client.post("/api/v1/documents/mask", files={"file": (name, data)}, data=fields, headers=AUTH_M)


def assert_masked(text):
    for secret in ("john.doe@acme.com", "7946", "12345678", "20-00-00", "NWBK", "4111", "1,250,000"):
        assert secret not in text, secret
    for kept in ("Account Number:", "Sort Code:", "2026-11-05", "clause 12.3", "Contact:"):
        assert kept in text, kept


def test_mask_txt():
    r = mask("doc.txt", SENSITIVE.encode())
    assert r.status_code == 200, r.text
    assert_masked(r.text)
    assert r.headers["x-contract-id"] == "CMS-CONTRACT-12345"
    assert r.headers["x-version"] == "2.0"
    assert int(r.headers["x-masked-field-count"]) >= 7


def test_mask_docx_runs_tables_headers():
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "Confidential - billing@acme.com"
    p = doc.add_paragraph()
    p.add_run("Account ")
    p.add_run("Number: 1234").bold = True
    p.add_run("5678 end")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = SENSITIVE
    buf = io.BytesIO()
    doc.save(buf)

    r = mask("contract.docx", buf.getvalue(), output_filename="ACME_MSA_20.docx")
    assert r.status_code == 200, r.text
    assert "ACME_MSA_20.docx" in r.headers["content-disposition"]
    out = Document(io.BytesIO(r.content))
    assert out.paragraphs[0].text == "Account Number: ******** end"
    assert out.paragraphs[0].runs[1].bold  # formatting preserved
    assert_masked(out.tables[0].cell(0, 0).text)
    assert "billing@acme.com" not in out.sections[0].header.paragraphs[0].text


def test_mask_pdf():
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for sentence in SENSITIVE.split(". "):
        page.insert_text((50, y), sentence, fontsize=9)
        y += 14
    r = mask("contract.pdf", doc.tobytes())
    assert r.status_code == 200, r.text
    with pymupdf.open(stream=r.content, filetype="pdf") as out:
        assert_masked(out[0].get_text())


def test_mask_xlsx():
    wb = Workbook()
    wb.active["A1"] = "Email"
    wb.active["B1"] = "jane@acme.com"
    wb.active["A2"] = 42
    buf = io.BytesIO()
    wb.save(buf)
    r = mask("data.xlsx", buf.getvalue())
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws["B1"].value == "*************" and ws["A2"].value == 42


def test_mask_unsupported_and_mismatch():
    r = mask("x.zip", b"x")
    assert r.status_code == 400 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    r = mask("x.txt", b"x", output_filename="y.pdf")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_mask_missing_contract_id():
    r = client.post("/api/v1/documents/mask", files={"file": ("a.txt", b"x")}, data={"version": "1"},
                    headers=AUTH_M)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_mask_corrupt_pdf():
    r = mask("bad.pdf", b"not a pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "MASKING_FAILED"
