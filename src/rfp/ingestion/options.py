import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from rfp.core.config import Settings

PARSER_VERSION = "2.1.0"


class ParserOptions(BaseModel):
    """Non-secret, persisted parser configuration shared by CLI and workers."""

    model_config = ConfigDict(extra="forbid")
    max_file_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    max_pages: int = Field(default=500, ge=1, le=5000)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    html_max_nodes: int = Field(default=200000, ge=1)
    ocr_language: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*$")
    ocr_dpi: int = Field(default=200, ge=72, le=400)
    ocr_timeout_seconds: int = Field(default=30, ge=1, le=120)
    ocr_min_text_chars: int = Field(default=40, ge=0, le=10000)
    ocr_min_words: int = Field(default=5, ge=0, le=1000)
    ocr_max_image_pixels: int = Field(default=16000000, ge=10000, le=40000000)
    ocr_force: bool = False
    metadata_type_patterns: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ParserOptions":
        return cls(
            max_file_bytes=settings.ingestion_max_file_bytes,
            max_pages=settings.parser_max_pages,
            timeout_seconds=settings.parser_timeout_seconds,
            html_max_nodes=settings.html_max_nodes,
            metadata_type_patterns=settings.metadata_type_patterns,
            **{
                name: getattr(settings, name)
                for name in cls.model_fields
                if name.startswith("ocr_")
            },
        )

    @property
    def fingerprint(self) -> str:
        payload = {"version": PARSER_VERSION, **self.model_dump()}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
