# ContractAI ↔ CMS Integration API (FastAPI)

Implements the *API specification document for CMS*:

| Section | Endpoint | Purpose |
|---|---|---|
| A.7.1 | `POST /api/v1/documents/combine` | Submit files to convert + merge into one PDF (async job, returns `202` + `job_id`) |
| A.7.2 | `GET /api/v1/documents/combine/{job_id}/status` | Poll until `completed` / `failed` |
| — | `GET /api/v1/documents/combine/{job_id}/download` | Merged PDF (the `download_url` when Azure Blob isn't configured) |
| A.8.1 | `POST /api/v1/documents/mask` | Upload a document (multipart); returns it masked, same format |
| — | `GET /health` | Liveness + LibreOffice availability |

Interactive docs: `http://localhost:8000/docs`

## Run

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
copy .env.example .env            # then edit
uvicorn app.main:app --reload
```

Word/Excel/PowerPoint conversion needs **LibreOffice** (`soffice`) on the server. It's auto-detected
on PATH or in `C:\Program Files\LibreOffice`, or set `LIBREOFFICE_PATH`. The `Dockerfile` installs it.

```bash
docker build -t contractai-cms-api .
docker run -p 8000:8000 --env-file .env contractai-cms-api
```

Tests: `pytest -q`

## Auth

Every endpoint needs `Authorization: Bearer <JWT>`.

* **Entra ID / Azure AD** (what Power Automate's HTTP-with-Entra-ID connector sends): set
  `JWT_JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`.
* **Shared secret (HS256)**: set `JWT_SECRET`.

Roles/scopes are checked against the `roles` and `scp` claims: `Documents.Combine` for A.7,
`Documents.Mask` for A.8 (configurable; blank = any valid token). Missing/invalid token → `401
UNAUTHORIZED`; valid token without the role → `403 FORBIDDEN`.

## A.7 Combine — request

The spec's JSON sample lists file names but not where the bytes come from. Each file therefore
takes **one** of:

* `blob_url` — URL the service downloads (e.g. Azure Blob temp storage with a SAS token). Unreachable / expired → `404 FILE_NOT_ACCESSIBLE`.
* `content_base64` — the file content inline (simplest from Power Automate: `base64(body('Get_file_content'))`).

```json
{
  "contract_id": "CMS-CONTRACT-12345",
  "output_filename": "ACME_MSA_2026_Supporting_Documents.pdf",
  "include_contents_page": true,
  "files": [
    { "file_name": "Signed_MSA.pdf", "file_type": "pdf", "order": 1,
      "blob_url": "https://<account>.blob.core.windows.net/temp/Signed_MSA.pdf?<sas>" },
    { "file_name": "Company_Seal.png", "file_type": "png", "order": 2,
      "content_base64": "iVBORw0KGgo..." }
  ]
}
```

Response `202`:
```json
{ "job_id": "combine-3f8a1c2d9e41", "contract_id": "CMS-CONTRACT-12345", "status": "queued",
  "status_url": "http://localhost:8000/api/v1/documents/combine/combine-3f8a1c2d9e41/status",
  "created_at": "2026-11-05T09:20:00Z" }
```

Behaviour:
* Files are merged in `order`. Accepted types: pdf, doc, docx, xls, xlsx, ppt, pptx, png, jpg, jpeg.
* The contents page is page 1 (so the first file starts on page 2, as in the spec example). It
  has clickable links, and the PDF also gets bookmarks per file.
* A file that can't be converted (corrupt / password-protected) is skipped and listed in
  `failed_files`; if none convert, the job is `failed` with `error_code: "CONVERSION_FAILED"`.
* Combined input over `MAX_COMBINED_SIZE_MB` (default 50) → `413 PAYLOAD_TOO_LARGE`.
* Office files submitted while LibreOffice isn't installed → `503 SERVICE_UNAVAILABLE`.
* If `AZURE_STORAGE_CONNECTION_STRING` is set, the PDF is uploaded and `download_url` is a SAS
  URL (`pip install azure-storage-blob`); otherwise it points at the `/download` endpoint.

Status responses match A.7.2 (in-progress shape while `queued`/`running`, full shape when
`completed`/`failed`).

## A.8 Mask — request

`multipart/form-data` with `file`, `contract_id`, `version`, and optional `output_filename`
(must keep the same extension). Supported: **.docx, .pdf, .xlsx, .txt, .csv**.

```bash
curl -X POST http://localhost:8000/api/v1/documents/mask \
  -H "Authorization: Bearer $TOKEN" \
  -F file=@ACME_MSA.docx -F contract_id=CMS-CONTRACT-12345 -F version=2.0 \
  -F output_filename=ACME_MSA_20.docx -o ACME_MSA_20.docx
```

Returns the masked file with headers `X-Contract-Id`, `X-Version`, `X-Masked-Field-Count`,
`X-Masked-Fields` (e.g. `{"email":2,"iban":1}`). Nothing is written to disk.

**Masked fields** — defined in [app/services/mask_rules.py](app/services/mask_rules.py); edit
that list to change them. Defaults: email, bank account number, IBAN, SWIFT/BIC, sort code,
IFSC, routing number, VAT/GST/tax ID, PAN, SSN, credit card (Luhn-checked), Aadhaar, phone
number, contract value. Labelled fields mask only the value (`Account Number: ********`).

* DOCX: body, tables, headers/footers, footnotes, comments; values split across runs are handled and formatting is kept.
* PDF: true redaction (text removed, black box drawn), metadata cleared.
* XLSX: string cells (formulas left alone).

## Assumptions to confirm with the spec owners

1. **Where combine input files come from** — added `blob_url` / `content_base64` per file.
2. **A.8 "defined set of sensitive fields"** isn't listed in the spec — the defaults above are a starting point.
3. **A.8 response shape / error codes** aren't in the spec — binary file + `X-` headers;
   reused the A.7.3 error format, plus `422 MASKING_FAILED` for corrupt files.
4. **Job store is in memory** — fine for one instance; use Redis/SQL for multiple workers.
   Jobs and outputs are purged after `JOB_RETENTION_HOURS` (24).
