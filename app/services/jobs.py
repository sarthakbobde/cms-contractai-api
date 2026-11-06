"""In-memory job store for combine jobs.

Good for a single-instance deployment. For multiple workers/instances,
replace JobStore with a shared store (Redis, Azure Table, SQL...).
"""
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..schemas import CombineStatusResponse, ContentsEntry, FailedFile, JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class CombineJob:
    contract_id: str
    output_filename: str
    include_contents_page: bool
    total_files: int
    job_id: str = field(default_factory=lambda: f"combine-{uuid.uuid4().hex[:12]}")
    status: JobStatus = JobStatus.queued
    message: str = "Job queued."
    converted_files: int = 0
    merged_files: int = 0
    total_pages: int = 0
    contents: list[ContentsEntry] = field(default_factory=list)
    failed_files: list[FailedFile] = field(default_factory=list)
    download_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    completed_at: datetime | None = None

    def to_response(self) -> CombineStatusResponse:
        if self.status in (JobStatus.queued, JobStatus.running):
            return CombineStatusResponse(
                job_id=self.job_id,
                contract_id=self.contract_id,
                status=self.status,
                total_files=self.total_files,
                converted_files=self.converted_files,
                message=self.message,
            )
        return CombineStatusResponse(
            job_id=self.job_id,
            contract_id=self.contract_id,
            status=self.status,
            output_filename=self.output_filename,
            download_url=self.download_url,
            total_files=self.total_files,
            merged_files=self.merged_files,
            total_pages=self.total_pages,
            contents_page_included=self.include_contents_page and self.status == JobStatus.completed,
            contents=self.contents,
            failed_files=self.failed_files,
            created_at=self.created_at,
            completed_at=self.completed_at,
            error_message=self.error_message,
            error_code=self.error_code,
        )


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, CombineJob] = {}
        self._lock = threading.Lock()

    def add(self, job: CombineJob) -> None:
        with self._lock:
            self._purge_expired()
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> CombineJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _purge_expired(self) -> None:
        cutoff = utcnow() - timedelta(hours=get_settings().job_retention_hours)
        expired = [jid for jid, j in self._jobs.items() if j.created_at < cutoff]
        for jid in expired:
            del self._jobs[jid]
            shutil.rmtree(get_settings().work_dir / "jobs" / jid, ignore_errors=True)


job_store = JobStore()
