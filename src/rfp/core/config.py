from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    redis_url: SecretStr
    qdrant_url: str
    qdrant_collection: str = Field(default="bid_documents", min_length=1)
    r2_account_id: str = ""
    r2_endpoint_url: str = ""
    r2_access_key_id: SecretStr = SecretStr("")
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket_name: str = ""
    r2_region: str = "auto"
    tesseract_cmd: str = Field(default="tesseract", min_length=1)
    llm_provider: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    dependency_timeout_seconds: float = Field(default=2, ge=0.1, le=30)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

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
