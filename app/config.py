"""Application settings, loaded from environment variables or a .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ContractAI CMS Integration API"

    # --- Auth (JWT Bearer) ---------------------------------------------------
    # Either set JWT_JWKS_URL (e.g. Microsoft Entra ID) for RS256 tokens,
    # or JWT_SECRET for HS256 tokens (handy for local development).
    auth_enabled: bool = True
    jwt_secret: str | None = None
    jwt_jwks_url: str | None = None
    jwt_algorithms: list[str] = ["HS256", "RS256"]
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    # Role/scope required per operation. Checked against the "roles" and "scp"
    # claims. Leave empty to only require a valid token.
    combine_required_role: str | None = "Documents.Combine"
    mask_required_role: str | None = "Documents.Mask"

    # --- Limits ----------------------------------------------------------------
    max_combined_size_mb: int = 50
    max_mask_file_size_mb: int = 25
    blob_download_timeout_seconds: int = 60

    # --- Conversion ------------------------------------------------------------
    libreoffice_path: str | None = None  # auto-detected when not set
    libreoffice_timeout_seconds: int = 180

    # --- Output storage --------------------------------------------------------
    work_dir: Path = Path("./data")
    # Public base URL of this service; used to build download_url when Azure
    # Blob Storage is not configured.
    public_base_url: str = "http://localhost:8000"
    # Optional: upload merged PDFs to Azure Blob Storage and return a SAS URL.
    azure_storage_connection_string: str | None = None
    azure_output_container: str = "output"
    azure_sas_expiry_hours: int = 24

    # --- Jobs ------------------------------------------------------------------
    job_retention_hours: int = 24

    # --- Masking ---------------------------------------------------------------
    mask_char: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
