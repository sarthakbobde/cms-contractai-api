"""Request / response models."""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

# Format -> extensions handled by the combine service (spec A.7 + accepted list in A.7.3)
PDF_TYPES = {"pdf"}
OFFICE_TYPES = {"doc", "docx", "xls", "xlsx", "ppt", "pptx"}
IMAGE_TYPES = {"png", "jpg", "jpeg"}
COMBINE_ACCEPTED = ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg"]


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


# ---------------------------------------------------------------- A.7.1 request
class CombineFile(BaseModel):
    file_name: str = Field(..., min_length=1, examples=["Signed_MSA.pdf"])
    file_type: str = Field(..., min_length=1, examples=["pdf"])
    order: int = Field(..., ge=1, examples=[1])
    # Where to get the file content. Provide exactly one of these.
    blob_url: str | None = Field(
        None, description="URL (e.g. Azure Blob with SAS token) the service downloads the file from."
    )
    content_base64: str | None = Field(None, description="File content, base64-encoded (alternative to blob_url).")

    @field_validator("file_type")
    @classmethod
    def _normalise_type(cls, v: str) -> str:
        return v.strip().lower().lstrip(".")

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.blob_url) == bool(self.content_base64):
            raise ValueError("provide exactly one of blob_url or content_base64")
        return self


class CombineRequest(BaseModel):
    contract_id: str = Field(..., min_length=1, examples=["CMS-CONTRACT-12345"])
    output_filename: str = Field(
        ..., min_length=1, examples=["ACME_MSA_2026_Supporting_Documents.pdf"]
    )
    include_contents_page: bool = True
    files: list[CombineFile]

    @field_validator("files")
    @classmethod
    def _non_empty(cls, v: list[CombineFile]) -> list[CombineFile]:
        if not v:
            raise ValueError("files must contain at least one item")
        orders = [f.order for f in v]
        if len(set(orders)) != len(orders):
            raise ValueError("files[].order values must be unique")
        return v

    @field_validator("output_filename")
    @classmethod
    def _pdf_name(cls, v: str) -> str:
        v = v.strip().replace("/", "_").replace("\\", "_")
        return v if v.lower().endswith(".pdf") else f"{v}.pdf"


class CombineSubmitResponse(BaseModel):
    job_id: str
    contract_id: str
    status: JobStatus
    status_url: str
    created_at: datetime


# ---------------------------------------------------------------- A.7.2 response
class ContentsEntry(BaseModel):
    file_name: str
    start_page: int


class FailedFile(BaseModel):
    file_name: str
    reason: str


class CombineStatusResponse(BaseModel):
    """Fields present depend on status (see spec A.7.2)."""

    job_id: str
    contract_id: str | None = None
    status: JobStatus
    output_filename: str | None = None
    download_url: str | None = None
    total_files: int | None = None
    converted_files: int | None = None
    merged_files: int | None = None
    total_pages: int | None = None
    contents_page_included: bool | None = None
    contents: list[ContentsEntry] | None = None
    failed_files: list[FailedFile] | None = None
    message: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    error_code: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
