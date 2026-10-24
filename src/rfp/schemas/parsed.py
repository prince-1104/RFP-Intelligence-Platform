"""Versioned parser contracts: all text is untrusted source data."""

from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParseStatus(StrEnum):
    succeeded = "succeeded"
    partially_succeeded = "partially_succeeded"
    failed = "failed"


class DocumentType(StrEnum):
    bid_page = "bid_page"
    rfp = "rfp"
    addendum = "addendum"
    specs = "specs"
    affidavit = "affidavit"
    other = "other"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceLocator(Contract):
    relative_path: str
    page_number: int | None = Field(default=None, ge=1)
    element_id: str | None = None
    heading: str | None = None
    element_index: int | None = Field(default=None, ge=1)
    table_index: int | None = Field(default=None, ge=1)


class Layout(Contract):
    bbox: tuple[float, float, float, float]
    coordinate_system: str = "pdf_points"


class ParsedBlock(Contract):
    block_id: str
    block_type: Literal["heading", "paragraph", "list_item", "link", "ocr_line", "text"]
    text: str
    section_heading: str | None = None
    source_locator: SourceLocator
    layout: Layout | None = None
    link_url: str | None = None


class ParsedTable(Contract):
    table_id: str
    table_index: int = Field(ge=1)
    headers: list[str] | None = None
    rows: list[list[str | None]]
    source_locator: SourceLocator
    extraction_method: str
    warnings: list[str] = Field(default_factory=list)


class ParsedPage(Contract):
    page_number: int | None = Field(default=None, ge=1)
    text: str = ""
    blocks: list[ParsedBlock] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    ocr_used: bool = False
    ocr_language: str | None = None
    ocr_average_confidence: float | None = Field(default=None, ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)


class ParsedDocument(Contract):
    schema_version: str = "1.0"
    document_id: UUID
    bid_id: UUID
    original_file_name: str
    relative_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_content_type: str
    doc_type: DocumentType = DocumentType.other
    addendum_number: int | None = Field(default=None, ge=0)
    document_date: date | None = None
    title: str | None = None
    parser_name: str
    parser_version: str
    parser_fingerprint: str
    processing_status: ParseStatus = ParseStatus.failed
    page_count: int = Field(default=0, ge=0)
    parsed_pages: list[ParsedPage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_provenance(self) -> "ParsedDocument":
        if self.source_content_type == "application/pdf":
            numbers = [p.page_number for p in self.parsed_pages]
            if numbers != list(range(1, len(numbers) + 1)) or len(numbers) > self.page_count:
                raise ValueError("PDF pages must be contiguous and one-based")
        elif any(p.page_number is not None for p in self.parsed_pages):
            raise ValueError("HTML pages must have null page numbers")
        for page in self.parsed_pages:
            records: list[ParsedBlock | ParsedTable] = [*page.blocks, *page.tables]
            for record in records:
                locator = record.source_locator
                if (
                    locator.relative_path != self.relative_path
                    or locator.page_number != page.page_number
                ):
                    raise ValueError("Record locators must match their source document and page")
        if self.processing_status != ParseStatus.failed and not any(
            p.text.strip() or p.tables for p in self.parsed_pages
        ):
            raise ValueError("Successful artifacts require readable content")
        return self
