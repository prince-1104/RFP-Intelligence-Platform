"""Phase 4 search contracts: retrieved evidence and citations, never generated answers."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from rfp.schemas.parsed import Contract, DocumentType

ChunkType = Literal["prose", "heading", "list", "table_rows", "ocr"]

DEFAULT_SYNONYMS: dict[str, list[str]] = {
    "deadline": ["submission deadline", "due date", "closing date"],
    "bid number": ["solicitation number", "rfp number"],
    "manufacturer": ["mfg", "vendor registration"],
    "part number": ["sku"],
    "pre-bid meeting": ["pre-proposal conference", "pre-bid conference"],
}


class SearchConfig(Contract):
    dense_model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=3)
    dense_dim: int = Field(default=384, ge=8, le=8192)
    dense_max_tokens: int = Field(default=512, ge=16, le=32768)
    # BGE v1.5 documents this instruction for short query -> passage retrieval (queries only).
    query_instruction: str = Field(
        default="Represent this sentence for searching relevant passages: ", max_length=300
    )
    sparse_model: str = Field(default="Qdrant/bm25", min_length=3)
    bm25_k: float = Field(default=1.2, gt=0, le=10)
    bm25_b: float = Field(default=0.75, ge=0, le=1)
    bm25_avg_len: float = Field(default=256.0, gt=0, le=100000)
    bm25_language: str = Field(default="english", pattern=r"^[a-z]+$")
    reranker_model: str = Field(default="Xenova/ms-marco-MiniLM-L-6-v2", min_length=3)
    model_cache_dir: str = Field(default=".cache/fastembed", min_length=1)
    allow_model_download: bool = False
    onnx_threads: int | None = Field(default=None, ge=1, le=64)
    embed_batch_size: int = Field(default=32, ge=1, le=1024)
    # Measured on 4-core CPU ONNX (VERIFICATION.md): each extra pair per batch adds ~60 MB of
    # arena memory and no throughput, because every pair pads to the longest in its batch.
    rerank_batch_size: int = Field(default=1, ge=1, le=1024)
    rerank_window_overlap_tokens: int = Field(default=50, ge=0, le=256)
    max_concurrent_inference: int = Field(default=2, ge=1, le=32)
    inference_timeout_seconds: float = Field(default=60, ge=1, le=600)
    default_top_k: int = Field(default=5, ge=1, le=100)
    max_top_k: int = Field(default=50, ge=1, le=200)
    candidate_depth: int = Field(default=40, ge=1, le=500)
    rerank_depth: int = Field(default=20, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    dense_weight: float = Field(default=1.0, ge=0, le=10)
    sparse_weight: float = Field(default=1.0, ge=0, le=10)
    identifier_weight: float = Field(default=1.0, ge=0, le=10)
    identifier_policy: Literal["require_evidence", "annotate"] = "require_evidence"
    identifier_rerank_bonus: float = Field(default=1.0, ge=0, le=5)
    synonyms: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_SYNONYMS))
    max_expansions: int = Field(default=12, ge=0, le=100)
    allow_degraded: bool = False
    collapse_duplicates: bool = True
    max_query_chars: int = Field(default=1000, ge=10, le=10000)
    qdrant_timeout_seconds: int = Field(default=10, ge=1, le=300)
    upsert_batch_size: int = Field(default=128, ge=1, le=2000)
    upsert_attempts: int = Field(default=3, ge=1, le=10)
    delete_superseded_points: bool = True
    index_batch_seconds: int = Field(default=600, ge=10, le=3600)
    local_cache_dir: str = Field(default=".cache/embeddings", min_length=1)
    max_cache_artifact_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)

    @field_validator("synonyms")
    @classmethod
    def validate_synonyms(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        cleaned: dict[str, list[str]] = {}
        for term, expansions in value.items():
            key = " ".join(term.lower().split())
            if not key or len(key) > 100 or len(expansions) > 20:
                raise ValueError("Synonym terms must be 1-100 characters with at most 20 entries")
            cleaned[key] = [" ".join(item.split()) for item in expansions if item.strip()]
        return cleaned

    @model_validator(mode="after")
    def depths(self) -> "SearchConfig":
        if not self.default_top_k <= self.max_top_k:
            raise ValueError("Require default_top_k <= max_top_k")
        if self.candidate_depth < self.default_top_k:
            raise ValueError("candidate_depth must be at least default_top_k")
        return self


class RetrievalMode(StrEnum):
    dense = "dense"
    sparse = "sparse"
    hybrid = "hybrid"
    hybrid_rerank = "hybrid_rerank"


class SearchFilters(Contract):
    bid_ids: list[UUID] = Field(default_factory=list, max_length=50)
    document_ids: list[UUID] = Field(default_factory=list, max_length=200)
    doc_types: list[DocumentType] = Field(default_factory=list, max_length=10)
    addendum_numbers: list[int] = Field(default_factory=list, max_length=100)
    chunk_types: list[ChunkType] = Field(default_factory=list, max_length=10)
    relative_paths: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("addendum_numbers")
    @classmethod
    def nonnegative(cls, value: list[int]) -> list[int]:
        if any(number < 0 for number in value):
            raise ValueError("Addendum numbers must be non-negative")
        return value


class SearchRequest(Contract):
    query: str = Field(min_length=1, max_length=10000)
    top_k: int | None = Field(default=None, ge=1)
    mode: RetrievalMode = RetrievalMode.hybrid_rerank
    filters: SearchFilters = Field(default_factory=SearchFilters)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must contain non-whitespace characters")
        return value


class LocatorRef(Contract):
    relative_path: str
    page_number: int | None = None
    element_id: str | None = None
    heading: str | None = None
    element_index: int | None = None
    table_index: int | None = None


class SegmentRef(Contract):
    locator: LocatorRef
    block_id: str | None = None
    normalized_start: int
    normalized_end: int
    ocr_used: bool = False


class TableProvenance(Contract):
    table_id: str
    table_index: int
    row_start: int
    row_end: int
    headers: list[str] | None = None
    extraction_method: str


class Citation(Contract):
    occurrence_id: UUID
    chunk_generation_id: UUID
    content_id: UUID
    content_hash: str
    bid_id: UUID
    document_id: UUID
    original_file_name: str
    relative_path: str
    document_type: DocumentType
    addendum_number: int | None = None
    chunk_type: ChunkType
    section_heading: str | None = None
    section_path: list[str] = Field(default_factory=list)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    html_locators: list[LocatorRef] = Field(default_factory=list)
    location_known: bool
    table: TableProvenance | None = None
    source_segments: list[SegmentRef] = Field(default_factory=list)
    normalization_version: str
    chunking_version: str
    warnings: list[str] = Field(default_factory=list)


class ScoreComponents(Contract):
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    sparse_rank: int | None = None
    identifier_rank: int | None = None
    rrf_score: float | None = None
    reranker_score: float | None = None
    identifier_bonus: float | None = None
    final_score: float


class IdentifierMatch(Contract):
    query_identifiers: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)


class EvidenceResult(Contract):
    rank: int = Field(ge=1)
    occurrence_id: UUID
    content_id: UUID
    content_hash: str
    text: str
    chunk_type: ChunkType
    citation: Citation
    additional_citations: list[Citation] = Field(default_factory=list)
    scores: ScoreComponents
    identifier_match: IdentifierMatch


class QueryExpansion(Contract):
    term: str
    added: list[str]


class QueryDiagnostics(Contract):
    original: str
    normalized: str
    dense_query: str
    sparse_query: str
    expansions: list[QueryExpansion] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)


class ProfileInfo(Contract):
    embedding_profile: str
    collection: str
    dense_model: str
    sparse_model: str
    reranker_model: str | None = None
    fastembed_version: str


class SearchResponse(Contract):
    query: QueryDiagnostics
    mode: RetrievalMode
    top_k: int
    filters: SearchFilters
    results: list[EvidenceResult]
    result_count: int
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)
    profile: ProfileInfo
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)


class IndexRequest(Contract):
    bid_id: UUID
