"""A.8 Mask Document Fields."""
import json
from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ..auth import require_mask
from ..config import Settings, get_settings
from ..errors import APIError, payload_too_large, unsupported_media_type, validation_error
from ..schemas import ErrorResponse
from ..services.masker import MASK_TYPES, MEDIA_TYPES, MaskingError, mask_document

router = APIRouter(prefix="/api/v1/documents", tags=["A.8 Mask document fields"])


@router.post(
    "/mask",
    response_class=Response,
    responses={
        200: {
            "description": "Masked document in the same format as the upload.",
            "content": {mt.split(";")[0]: {} for mt in MEDIA_TYPES.values()},
        },
        **{code: {"model": ErrorResponse} for code in (400, 401, 403, 413, 422, 500)},
    },
    summary="A.8.1 Submit file for masking",
)
async def mask(
    file: UploadFile = File(..., description="Document to mask (.docx, .pdf, .xlsx, .txt, .csv)"),
    contract_id: str = Form(..., min_length=1, examples=["CMS-CONTRACT-12345"]),
    version: str = Form(..., min_length=1, examples=["2.0"]),
    output_filename: str | None = Form(None, examples=["ACME_MSA_20.docx"]),
    settings: Settings = Depends(get_settings),
    _claims: dict = Depends(require_mask),
):
    original_name = PurePath(file.filename or "").name
    ext = PurePath(original_name).suffix.lower().lstrip(".")
    if not ext:
        raise validation_error("file must have a file extension")
    if ext not in MASK_TYPES:
        raise unsupported_media_type(ext, MASK_TYPES)

    out_name = PurePath(output_filename.strip()).name if output_filename and output_filename.strip() else original_name
    out_ext = PurePath(out_name).suffix.lower().lstrip(".")
    if not out_ext:
        out_name, out_ext = f"{out_name}.{ext}", ext
    if out_ext != ext:
        raise validation_error(
            f"output_filename must keep the original format (.{ext}); got .{out_ext}"
        )

    limit = settings.max_mask_file_size_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise APIError(413, "PAYLOAD_TOO_LARGE",
                       f"File size exceeds maximum allowed size of {settings.max_mask_file_size_mb} MB")
    if not data:
        raise validation_error("file is empty")

    try:
        masked, counts = await run_in_threadpool(mask_document, data, ext)
    except MaskingError as exc:
        raise APIError(422, "MASKING_FAILED", str(exc))
    finally:
        del data  # original is never persisted

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(out_name)}",
        "X-Contract-Id": contract_id,
        "X-Version": version,
        "X-Masked-Field-Count": str(sum(counts.values())),
        "X-Masked-Fields": json.dumps(dict(sorted(counts.items())), separators=(",", ":")),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Contract-Id, X-Version, "
                                         "X-Masked-Field-Count, X-Masked-Fields",
    }
    return Response(content=masked, media_type=MEDIA_TYPES[ext], headers=headers)
