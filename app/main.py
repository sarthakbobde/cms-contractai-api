"""ContractAI <-> CMS integration microservice.

Run:  uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""
import logging

from fastapi import FastAPI

from .config import get_settings
from .errors import register_exception_handlers
from .routers import combine, mask
from .services.converter import find_libreoffice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()
settings.work_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "API integration between CMS and ContractAI.\n\n"
        "* **A.7** Combine supporting documents into a single PDF (async job)\n"
        "* **A.8** Mask sensitive fields in a document"
    ),
)
register_exception_handlers(app)
app.include_router(combine.router)
app.include_router(mask.router)


@app.get("/health", tags=["Health"], summary="Liveness / dependency check")
async def health():
    return {"status": "ok", "libreoffice_available": find_libreoffice() is not None}
