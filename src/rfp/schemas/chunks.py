"""Phase 3 contracts separate reusable text from citation-bearing source occurrences."""

import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from rfp.schemas.parsed import Contract, DocumentType, Layout, ParseStatus, SourceLocator


def stable_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ChunkConfig(Contract):
    normalization_version: Literal["1.0"] = "1.0"
    # 2.0: exact embedding-model token budgets and cell-level splitting of oversized table rows.
    chunking_version: Literal["1.0", "2.0"] = "2.0"
    # Budgets count the full embedding input, including the model's special tokens.
    target_tokens: int = Field(default=320, ge=8, le=16000)
    max_tokens: int = Field(default=400, ge=8, le=32000)
    overlap_tokens: int = Field(default=50, ge=0)
    table_row_limit: int = Field(default=20, ge=1, le=1000)
    token_counter: Literal["utf8_bytes_v1", "model_tokenizer_v1"] = "model_tokenizer_v1"
    # The hash pins tokenizer files; a different tokenizer must change the configuration.
    tokenizer_identifier: str = Field(
        default="hf:BAAI/bge-small-en-v1.5@6ba3d85b0f27cb7a", min_length=3, max_length=300
    )
    min_content_chars: int = Field(default=1, ge=1, le=1000)
    empty_behavior: Literal["failed", "skipped"] = "failed"
    join_wrapped_lines: bool = True
    max_artifact_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=256 * 1024 * 1024)
    max_input_chars: int = Field(default=5000000, ge=100, le=20000000)
    max_output_chars: int = Field(default=20000000, ge=100, le=100000000)
    max_chunks: int = Field(default=10000, ge=1, le=100000)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    batch_seconds: int = Field(default=240, ge=1, le=900)

    @model_validator(mode="after")
    def budgets(self) -> "ChunkConfig":
        if not self.overlap_tokens < self.target_tokens <= self.max_tokens:
            raise ValueError("Require overlap_tokens < target_tokens <= max_tokens")
        estimated = self.token_counter == "utf8_bytes_v1"
        if estimated != (self.tokenizer_identifier == "utf8-bytes-div4-v1"):
            raise ValueError("tokenizer_identifier does not match token_counter")
        if not estimated and not re.fullmatch(r"hf:[^@\s]+@[a-f0-9]{16}", self.tokenizer_identifier):
            raise ValueError("Model tokenizer identifier must be hf:<model>@<16 hex>")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(stable_json(self.model_dump(mode="json")))


class CanonicalChunk(Contract):
    content_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str


class SourceSegment(Contract):
    source_locator: SourceLocator
    block_id: str | None = None
    normalized_start: int = Field(default=0, ge=0)
    normalized_end: int = Field(ge=0)
    layout: Layout | None = None
    ocr_used: bool = False
    ocr_language: str | None = None
    ocr_average_confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class TableRows(Contract):
    table_id: str
    table_index: int = Field(ge=1)
    row_start: int = Field(ge=1)
    row_end: int = Field(ge=1)
    headers: list[str] | None = None
    rows: list[list[str | None]]
    extraction_method: str
    warnings: list[str] = Field(default_factory=list)
    # Set only when one row exceeds the token budget: the chunk text holds these columns
    # (one-based, inclusive) and, for a single oversized cell, part ``cell_part`` of
    # ``cell_parts``. ``rows`` always preserves the complete source row.
    column_start: int | None = Field(default=None, ge=1)
    column_end: int | None = Field(default=None, ge=1)
    cell_part: int | None = Field(default=None, ge=1)
    cell_parts: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def row_range(self) -> "TableRows":
        if self.row_end - self.row_start + 1 != len(self.rows):
            raise ValueError("Table row range must match preserved rows")
        if (self.column_start is None) != (self.column_end is None) or (
            self.column_start is not None
            and (self.column_end < self.column_start or self.row_start != self.row_end)
        ):
            raise ValueError("Column range requires a single row and start <= end")
        if (self.cell_part is None) != (self.cell_parts is None) or (
            self.cell_part is not None
            and (self.cell_part > self.cell_parts or self.column_start != self.column_end)
        ):
            raise ValueError("Cell parts require a single column and part <= parts")
        return self


class ChunkOccurrence(Contract):
    occurrence_id: UUID
    generation_id: UUID
    content_id: UUID
    content_hash: str
    bid_id: UUID
    document_id: UUID
    original_file_name: str
    relative_path: str
    source_sha256: str
    source_parser_fingerprint: str
    source_parsed_sha256: str
    document_type: DocumentType
    addendum_number: int | None = None
    chunk_type: Literal["prose", "heading", "list", "table_rows", "ocr"]
    normalized_text: str
    section_heading: str | None = None
    section_path: list[str] = Field(default_factory=list)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    source_segments: list[SourceSegment]
    table: TableRows | None = None
    sequence: int = Field(ge=1)
    normalization_version: str
    chunking_version: str
    configuration_hash: str
    estimated_token_count: int = Field(ge=0)
    tokenizer_identifier: str
    warnings: list[str] = Field(default_factory=list)


class ChunkingResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    generation_id: UUID
    bid_id: UUID
    document_id: UUID
    relative_path: str
    source_sha256: str
    parsed_artifact_sha256: str
    normalized_content_hash: str
    configuration: ChunkConfig
    configuration_hash: str
    processing_status: ParseStatus
    skipped: bool = False
    contents: list[CanonicalChunk] = Field(default_factory=list)
    occurrences: list[ChunkOccurrence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def integrity(self) -> "ChunkingResult":
        contents = {content.content_id: content for content in self.contents}
        if (
            len(contents) != len(self.contents)
            or self.configuration_hash != self.configuration.fingerprint
        ):
            raise ValueError("Invalid canonical contents or configuration fingerprint")
        for content in self.contents:
            if sha256(content.text.encode("utf-8")) != content.content_hash:
                raise ValueError("Canonical content hash mismatch")
        for index, occurrence in enumerate(self.occurrences, 1):
            canonical = contents.get(occurrence.content_id)
            if (
                canonical is None
                or occurrence.normalized_text != canonical.text
                or occurrence.content_hash != canonical.content_hash
                or occurrence.sequence != index
                or occurrence.generation_id != self.generation_id
                or occurrence.document_id != self.document_id
                or occurrence.bid_id != self.bid_id
                or occurrence.relative_path != self.relative_path
                or occurrence.source_sha256 != self.source_sha256
                or occurrence.source_parsed_sha256 != self.parsed_artifact_sha256
                or occurrence.configuration_hash != self.configuration_hash
                or not occurrence.source_segments
            ):
                raise ValueError("Invalid source occurrence")
            if any(
                segment.source_locator.relative_path != self.relative_path
                or segment.normalized_end < segment.normalized_start
                for segment in occurrence.source_segments
            ):
                raise ValueError("Invalid source segment")
            pages = [
                segment.source_locator.page_number
                for segment in occurrence.source_segments
                if segment.source_locator.page_number is not None
            ]
            if occurrence.page_start != (min(pages) if pages else None) or occurrence.page_end != (
                max(pages) if pages else None
            ):
                raise ValueError("Page range must cover every source segment")
        if (
            self.processing_status != ParseStatus.failed
            and not self.occurrences
            and not self.skipped
        ):
            raise ValueError("Nonempty successful generation required")
        return self
