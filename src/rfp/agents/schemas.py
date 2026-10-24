"""Structured agent messages and the assignment's per-bid JSON contract."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from rfp.schemas.parsed import Contract, DocumentType
from rfp.schemas.search import RetrievalMode

REQUIRED_FIELDS: tuple[str, ...] = (
    "Bid Number",
    "Title",
    "Due Date",
    "Bid Submission Type",
    "Term of Bid",
    "Pre Bid Meeting",
    "Installation",
    "Bid Bond Requirement",
    "Delivery Date",
    "Payment Terms",
    "Any Additional Documentation Required",
    "MFG for Registration",
    "Contract or Cooperative to use",
    "Model_no",
    "Part_no",
    "Product",
    "contact_info",
    "company_name",
    "Bid Summary",
    "Product Specification",
)

FieldStatus = Literal["draft", "accepted", "rejected", "unresolved", "not_found"]
SupportLevel = Literal["exact", "contained", "unsupported", "null_ok", "conflict"]


class ExtractionConfig(Contract):
    top_k: int = Field(default=12, ge=1, le=50)
    hard_top_k: int = Field(default=20, ge=1, le=50)
    mode: RetrievalMode = RetrievalMode.hybrid_rerank
    max_retries: int = Field(default=2, ge=0, le=5)
    concurrency: int = Field(default=4, ge=1, le=8)
    critic_enabled: bool = False
    excerpt_chars: int = Field(default=700, ge=80, le=4000)


class OutputSource(Contract):
    file: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    html_element_id: str | None = None

    @model_validator(mode="after")
    def locator(self) -> "OutputSource":
        has_page = self.page is not None
        has_html = bool(self.html_element_id)
        if has_page == has_html:
            raise ValueError(
                "A source needs a PDF page or an HTML locator, not both and not neither"
            )
        return self


class FieldOutput(Contract):
    value: str | None
    sources: list[OutputSource]
    confidence: float = Field(ge=0, le=1)
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def cited(self) -> "FieldOutput":
        if self.value is not None and not self.sources:
            raise ValueError("A non-null value requires a source")
        if self.value is None and self.sources:
            raise ValueError("A null value cannot carry sources")
        return self


class AddendumChange(Contract):
    field: str
    original_value: str | None
    revised_value: str | None
    addendum_number: int | None = Field(default=None, ge=0)
    sources: list[OutputSource]
    explanation: str = Field(min_length=1)
    conflict: bool = False


class ValidationCounts(Contract):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    not_found: int = Field(ge=0)


class BidExtraction(Contract):
    bid_id: str = Field(min_length=1)
    fields: dict[str, FieldOutput]
    addendum_changes: list[AddendumChange]
    validation: ValidationCounts

    @model_validator(mode="after")
    def complete(self) -> "BidExtraction":
        if tuple(self.fields) != REQUIRED_FIELDS:
            raise ValueError("fields must contain the 20 required names in assignment order")
        counts = self.validation
        if counts.passed + counts.not_found + counts.unresolved != len(REQUIRED_FIELDS):
            raise ValueError("passed, not_found and unresolved must cover every field")
        if counts.failed != counts.unresolved:
            raise ValueError("failed counts fields left unresolved")
        return self


class PageSpan(Contract):
    """Character range [start, end) of the hit text that came from one PDF page."""

    page: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SourceRef(Contract):
    file: str
    relative_path: str
    document_type: DocumentType
    addendum_number: int | None = None
    page: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    html_element_id: str | None = None
    bid_id: UUID
    spans: list[PageSpan] = Field(default_factory=list)

    def output(self, page: int | None = None) -> OutputSource:
        if self.page is not None:
            return OutputSource(file=self.file, page=page or self.page)
        return OutputSource(file=self.file, html_element_id=self.html_element_id)


class EvidenceHit(Contract):
    evidence_id: str
    bid_id: UUID
    text: str
    rank: int = Field(ge=1)
    mode: str
    final_score: float
    sources: list[SourceRef] = Field(min_length=1)

    @property
    def document_type(self) -> DocumentType:
        return self.sources[0].document_type

    @property
    def addendum_number(self) -> int | None:
        return self.sources[0].addendum_number


class DraftField(Contract):
    name: str
    value: str | None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""
    status: FieldStatus = "draft"
    model_confidence: float | None = None


class FieldValidation(Contract):
    name: str
    passed: bool
    status: FieldStatus
    reasons: list[str] = Field(default_factory=list)
    support: SupportLevel = "unsupported"


class TokenUsage(Contract):
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


class ModelMetadata(Contract):
    provider: str
    model: str


class TraceEvent(Contract):
    sequence: int = Field(ge=0)
    agent: str
    task: str
    field: str | None = None
    started_at: str
    ended_at: str
    duration_ms: float = Field(ge=0)
    query: str | None = None
    query_hash: str | None = None
    mode: str | None = None
    top_k: int | None = None
    doc_types: list[str] = Field(default_factory=list)
    result_count: int | None = None
    evidence: list[dict[str, str | int | None]] = Field(default_factory=list)
    decision: str | None = None
    reasons: list[str] = Field(default_factory=list)
    retry: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider: str | None = None
    model: str | None = None
    error: str | None = None


class ExtractionTrace(Contract):
    workflow_id: str
    bid_id: str
    bid_uuid: UUID
    documents: list[str]
    events: list[TraceEvent]
    final_statuses: dict[str, FieldStatus]
    outcome: Literal["completed", "failed"]
    token_usage: TokenUsage
    model: ModelMetadata
    error: str | None = None
