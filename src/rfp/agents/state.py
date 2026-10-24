"""Mutable shared state for one extraction run. Serialized output is built from it later."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from rfp.agents.schemas import (
    REQUIRED_FIELDS,
    AddendumChange,
    DraftField,
    EvidenceHit,
    FieldStatus,
    FieldValidation,
    TokenUsage,
)


@dataclass
class FieldState:
    name: str
    evidence: list[EvidenceHit] = field(default_factory=list)
    draft: DraftField | None = None
    validation: FieldValidation | None = None
    retry_count: int = 0
    search_errors: list[str] = field(default_factory=list)
    status: FieldStatus = "draft"
    fallback: tuple[DraftField, list[EvidenceHit], list[str]] | None = None
    """A supported value rejected only as incomplete, kept in case no retry does better."""
    incomplete: list[str] = field(default_factory=list)


@dataclass
class ExtractionState:
    workflow_id: UUID
    bid_label: str
    bid_uuid: UUID
    documents: list[str] = field(default_factory=list)
    fields: dict[str, FieldState] = field(default_factory=dict)
    addendum_changes: list[AddendumChange] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    tokens: TokenUsage = field(default_factory=TokenUsage)
    status: str = "running"
    error: str | None = None
    search_ok: int = 0
    search_failed: int = 0
    provider: str = ""
    model_name: str = ""

    @classmethod
    def create(
        cls, bid_label: str, bid_uuid: UUID, workflow_id: UUID | None = None
    ) -> "ExtractionState":
        return cls(
            workflow_id=workflow_id or uuid4(),
            bid_label=bid_label,
            bid_uuid=bid_uuid,
            fields={name: FieldState(name) for name in REQUIRED_FIELDS},
        )

    def pending(self) -> list[str]:
        return [name for name in REQUIRED_FIELDS if self.fields[name].status == "rejected"]

    def remember_documents(self, hits: list[EvidenceHit]) -> None:
        seen = set(self.documents)
        for hit in hits:
            for source in hit.sources:
                if source.file not in seen:
                    self.documents.append(source.file)
                    seen.add(source.file)
