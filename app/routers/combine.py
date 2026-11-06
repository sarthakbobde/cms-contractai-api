"""A.7 Combine Supporting Documents into a Single PDF."""
from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import FileResponse

from ..auth import require_combine
from ..config import Settings, get_settings
from ..errors import APIError, job_not_found, service_unavailable, unsupported_media_type
from ..schemas import (COMBINE_ACCEPTED, OFFICE_TYPES, CombineRequest, CombineStatusResponse,
                       CombineSubmitResponse, ErrorResponse, JobStatus)
from ..services.combiner import run_combine_job
from ..services.converter import find_libreoffice
from ..services.jobs import CombineJob, job_store
from ..services.storage import fetch_inputs, job_dir

router = APIRouter(prefix="/api/v1/documents/combine", tags=["A.7 Combine documents"])

ERRORS = {code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 413, 500, 503)}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CombineSubmitResponse,
    responses=ERRORS,
    summary="A.7.1 Submit files for combining",
)
async def submit_combine(
    body: CombineRequest,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _claims: dict = Depends(require_combine),
):
    for f in body.files:
        if f.file_type not in COMBINE_ACCEPTED:
            raise unsupported_media_type(f.file_type, COMBINE_ACCEPTED)

    if any(f.file_type in OFFICE_TYPES for f in body.files) and not find_libreoffice():
        raise service_unavailable()

    contents = await fetch_inputs(body.files)

    job = CombineJob(
        contract_id=body.contract_id,
        output_filename=body.output_filename,
        include_contents_page=body.include_contents_page,
        total_files=len(body.files),
    )
    job_store.add(job)
    background.add_task(run_combine_job, job, body.files, contents)

    base = settings.public_base_url.rstrip("/")
    return CombineSubmitResponse(
        job_id=job.job_id,
        contract_id=job.contract_id,
        status=job.status,
        status_url=f"{base}/api/v1/documents/combine/{job.job_id}/status",
        created_at=job.created_at,
    )


@router.get(
    "/{job_id}/status",
    response_model=CombineStatusResponse,
    response_model_exclude_unset=True,
    responses=ERRORS,
    summary="A.7.2 Get combine status and result",
)
async def combine_status(job_id: str, _claims: dict = Depends(require_combine)):
    job = job_store.get(job_id)
    if not job:
        raise job_not_found(job_id)
    return job.to_response()


@router.get(
    "/{job_id}/download",
    response_class=FileResponse,
    responses={**ERRORS, 200: {"content": {"application/pdf": {}}}},
    summary="Download the merged PDF (used when Azure Blob Storage is not configured)",
)
async def combine_download(job_id: str, _claims: dict = Depends(require_combine)):
    job = job_store.get(job_id)
    if not job:
        raise job_not_found(job_id)
    if job.status != JobStatus.completed:
        raise APIError(409, "JOB_NOT_COMPLETED", f"Job {job_id} is {job.status.value}")
    path = job_dir(job_id) / "output.pdf"
    if not path.exists():
        raise APIError(404, "FILE_NOT_ACCESSIBLE", f"Output for job {job_id} is no longer available")
    return FileResponse(path, media_type="application/pdf", filename=job.output_filename)
