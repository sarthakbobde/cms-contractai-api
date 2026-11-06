"""Input fetching (blob URL / base64) and output storage (local or Azure Blob)."""
import base64
import binascii
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..config import get_settings
from ..errors import file_not_accessible, payload_too_large, service_unavailable, validation_error
from ..schemas import CombineFile


async def fetch_inputs(files: list[CombineFile]) -> list[bytes]:
    """Download / decode every input file, enforcing the combined size limit."""
    settings = get_settings()
    limit = settings.max_combined_size_mb * 1024 * 1024
    total = 0
    contents: list[bytes] = []
    async with httpx.AsyncClient(timeout=settings.blob_download_timeout_seconds, follow_redirects=True) as client:
        for f in files:
            if f.content_base64:
                try:
                    data = base64.b64decode(f.content_base64, validate=True)
                except (binascii.Error, ValueError):
                    raise validation_error(f"content_base64 for {f.file_name} is not valid base64")
            else:
                data = await _download(client, f.blob_url, f.file_name, limit - total)
            total += len(data)
            if total > limit:
                raise payload_too_large(settings.max_combined_size_mb)
            contents.append(data)
    return contents


async def _download(client: httpx.AsyncClient, url: str, file_name: str, remaining: int) -> bytes:
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code in (401, 403, 404, 409, 410):
                raise file_not_accessible(file_name)
            if resp.status_code >= 500:
                raise service_unavailable()
            if resp.status_code >= 400:
                raise file_not_accessible(file_name)
            declared = int(resp.headers.get("content-length") or 0)
            if declared > remaining:
                raise payload_too_large(get_settings().max_combined_size_mb)
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > remaining:
                    raise payload_too_large(get_settings().max_combined_size_mb)
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.InvalidURL:
        raise file_not_accessible(file_name)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise file_not_accessible(file_name)


def job_dir(job_id: str) -> Path:
    d = get_settings().work_dir / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_output(job_id: str, output_filename: str, pdf_bytes: bytes) -> str:
    """Persist the merged PDF and return its download URL."""
    settings = get_settings()
    local = job_dir(job_id) / "output.pdf"
    local.write_bytes(pdf_bytes)

    if settings.azure_storage_connection_string:
        return _upload_to_azure(job_id, output_filename, pdf_bytes)
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/documents/combine/{job_id}/download"


def _upload_to_azure(job_id: str, output_filename: str, pdf_bytes: bytes) -> str:
    # Imported lazily so azure-storage-blob is only needed when configured.
    from azure.storage.blob import (BlobSasPermissions, BlobServiceClient, ContentSettings,
                                    generate_blob_sas)

    settings = get_settings()
    service = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
    blob_name = f"{job_id}/{output_filename}"
    blob = service.get_blob_client(settings.azure_output_container, blob_name)
    blob.upload_blob(pdf_bytes, overwrite=True, content_settings=ContentSettings(content_type="application/pdf"))
    sas = generate_blob_sas(
        account_name=service.account_name,
        container_name=settings.azure_output_container,
        blob_name=blob_name,
        account_key=service.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=settings.azure_sas_expiry_hours),
    )
    return f"{blob.url}?{sas}"
