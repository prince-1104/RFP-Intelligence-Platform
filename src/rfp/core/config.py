from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from rfp.schemas.chunks import ChunkConfig
from rfp.schemas.search import SearchConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True, env_nested_delimiter="__"
    )
    chunking: ChunkConfig = Field(default_factory=ChunkConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)

    database_url: SecretStr
    redis_url: SecretStr
    qdrant_url: str
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = Field(default="bid_documents", min_length=1)
    r2_account_id: str = ""
    r2_endpoint_url: str = ""
    r2_access_key_id: SecretStr = SecretStr("")
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket_name: str = ""
    r2_region: str = "auto"
    tesseract_cmd: str = Field(default="tesseract", min_length=1)
    ingestion_max_file_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    ingestion_max_total_bytes: int = Field(default=500 * 1024 * 1024, ge=1024)
    ingestion_max_files: int = Field(default=500, ge=1, le=10000)
    parser_max_pages: int = Field(default=500, ge=1, le=5000)
    parser_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    html_max_nodes: int = Field(default=200000, ge=100)
    ocr_language: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*$")
    ocr_dpi: int = Field(default=200, ge=72, le=400)
    ocr_timeout_seconds: int = Field(default=30, ge=1, le=120)
    ocr_min_text_chars: int = Field(default=40, ge=0, le=10000)
    ocr_min_words: int = Field(default=5, ge=0, le=1000)
    ocr_max_image_pixels: int = Field(default=16000000, ge=10000, le=40000000)
    ocr_force: bool = False
    metadata_type_patterns: dict[str, str] = Field(default_factory=dict)
    ingestion_storage_attempts: int = Field(default=3, ge=1, le=5)
    llm_provider: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    dependency_timeout_seconds: float = Field(default=2, ge=0.1, le=30)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("metadata_type_patterns")
    @classmethod
    def validate_metadata_patterns(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        for name, pattern in value.items():
            if name not in {"bid_page", "rfp", "addendum", "specs", "affidavit", "other"}:
                raise ValueError("Unknown document type in metadata patterns")
            try:
                re.compile(pattern)
            except re.error:
                raise ValueError("Invalid metadata classification pattern") from None
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = url.drivername == "postgresql+psycopg" and url.host and url.database
        except Exception:
            valid = False
        if not valid:
            raise ValueError("DATABASE_URL must be a PostgreSQL psycopg URL with host and database")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis(cls, value: SecretStr) -> SecretStr:
        url = urlsplit(value.get_secret_value())
        if url.scheme not in {"redis", "rediss"} or not url.hostname:
            raise ValueError("REDIS_URL must use redis or rediss with a host")
        return value

    @field_validator("qdrant_url")
    @classmethod
    def validate_qdrant(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("QDRANT_URL must be an HTTP URL without credentials or query")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_qdrant_auth(self) -> "Settings":
        if self.qdrant_api_key.get_secret_value() and urlsplit(self.qdrant_url).scheme != "https":
            raise ValueError("QDRANT_URL must use HTTPS when QDRANT_API_KEY is configured")
        return self

    @model_validator(mode="after")
    def validate_r2(self) -> "Settings":
        import re

        if self.r2_account_id and not re.fullmatch(r"[a-fA-F0-9]{32}", self.r2_account_id):
            raise ValueError("R2_ACCOUNT_ID must contain 32 hexadecimal characters")
        if not self.r2_endpoint_url and self.r2_account_id:
            self.r2_endpoint_url = f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        if self.r2_endpoint_url:
            url = urlsplit(self.r2_endpoint_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or not url.hostname.endswith(".r2.cloudflarestorage.com")
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
                or url.port not in {None, 443}
            ):
                raise ValueError("R2_ENDPOINT_URL must be a Cloudflare R2 HTTPS endpoint")
        return self

    @property
    def r2_configured(self) -> bool:
        return all(
            [
                self.r2_endpoint_url,
                self.r2_bucket_name,
                self.r2_access_key_id.get_secret_value(),
                self.r2_secret_access_key.get_secret_value(),
            ]
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
