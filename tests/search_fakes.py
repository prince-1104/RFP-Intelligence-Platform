"""Offline deterministic stand-ins for FastEmbed models plus synthetic Phase 3 chunk inputs."""

import re
import uuid
import zlib
from collections import Counter

import numpy as np
from qdrant_client import QdrantClient

from rfp.chunking.engine import chunk_document
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import parse_document
from rfp.schemas.chunks import ChunkConfig, ChunkingResult, sha256
from rfp.schemas.parsed import (
    DocumentType,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    SourceLocator,
)
from rfp.search.errors import InferenceFailed
from rfp.search.indexing import GenerationInput, IndexingService
from rfp.search.models import ModelRuntime, SparseVector
from rfp.search.service import SearchService

DIM = 64
BID_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
BID_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def bucket(token: str, size: int) -> int:
    return zlib.crc32(token.encode()) % size


class FakeDense:
    model_id = "fake/dense"
    dim = DIM
    max_tokens = 64

    def __init__(self, version: str = "1"):
        self.version = version
        self.documents_embedded = 0

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(DIM, dtype=np.float32)
        vector[0] = 0.01
        for token in tokens(text):
            vector[bucket(token, DIM)] += 1.0
        return vector / np.linalg.norm(vector)

    def embed_documents(self, texts):
        self.documents_embedded += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    def token_counts(self, texts):
        return [len(tokens(text)) for text in texts]

    def identity(self):
        return {
            "model": self.model_id,
            "dim": DIM,
            "version": self.version,
            "fastembed_version": "fake",
        }


class FakeSparse:
    model_id = "fake/bm25"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.documents_embedded = 0

    def embed_documents(self, texts):
        self.documents_embedded += len(texts)
        result = []
        for text in texts:
            counts = Counter(zlib.crc32(t.encode()) for t in tokens(text))
            pairs = sorted(counts.items())
            result.append(
                SparseVector(tuple(i for i, _ in pairs), tuple(float(v) for _, v in pairs))
            )
        return result

    def embed_query(self, text):
        if self.fail:
            raise InferenceFailed(code="sparse_query_failed")
        indices = sorted({zlib.crc32(t.encode()) for t in tokens(text)})
        return SparseVector(tuple(indices), tuple(1.0 for _ in indices))

    def identity(self):
        return {"model": self.model_id, "k": 1.2}


class FakeReranker:
    model_id = "fake/reranker"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def rerank(self, query, documents):
        self.calls += 1
        if self.fail:
            raise RuntimeError("reranker crashed")
        wanted = set(tokens(query))
        return [
            float(len(wanted & set(tokens(doc)))) - 0.001 * i for i, doc in enumerate(documents)
        ]

    def identity(self):
        return {"model": self.model_id}


def runtime(config, dense=None, sparse=None, reranker=None) -> ModelRuntime:
    dense = dense or FakeDense()
    sparse = sparse or FakeSparse()
    reranker = reranker or FakeReranker()
    return ModelRuntime(
        config,
        dense_factory=lambda: dense,
        sparse_factory=lambda: sparse,
        reranker_factory=lambda: reranker,
    )


class MemoryCache:
    kind = "memory"

    def __init__(self):
        self.entries = {}
        self.stores = 0

    def load(self, profile, hashes):
        return {
            h: self.entries[(profile.fingerprint, h)]
            for h in hashes
            if (profile.fingerprint, h) in self.entries
        }

    def store(self, profile, entries):
        if entries:
            self.stores += 1
        for entry in entries:
            self.entries[(profile.fingerprint, entry.content_hash)] = entry


def services(settings, client=None, cache=None, **models):
    client = client or QdrantClient(":memory:")
    model_runtime = runtime(settings.search, **models)
    indexer = IndexingService(
        model_runtime, client, cache or MemoryCache(), settings, sleep=lambda _: None
    )
    return indexer, SearchService(model_runtime, client, settings), client


def pdf_document(
    bid_id: uuid.UUID,
    path: str,
    pages: list[list[str]],
    doc_type: DocumentType = DocumentType.rfp,
    addendum_number: int | None = None,
) -> ParsedDocument:
    digest = sha256(("\n".join("|".join(p) for p in pages) + path).encode())
    parsed_pages = [
        ParsedPage(
            page_number=number,
            text="\n\n".join(t.removeprefix("# ") for t in texts),
            blocks=[
                ParsedBlock(
                    block_id=f"p{number}-{i}",
                    block_type="heading" if value.startswith("# ") else "paragraph",
                    text=value.removeprefix("# "),
                    section_heading=value.removeprefix("# ") if value.startswith("# ") else None,
                    source_locator=SourceLocator(relative_path=path, page_number=number),
                )
                for i, value in enumerate(texts)
            ],
        )
        for number, texts in enumerate(pages, start=1)
    ]
    return ParsedDocument(
        document_id=uuid.uuid5(bid_id, path + digest),
        bid_id=bid_id,
        original_file_name=path.rsplit("/", 1)[-1],
        relative_path=path,
        source_sha256=digest,
        source_content_type="application/pdf",
        doc_type=doc_type,
        addendum_number=addendum_number,
        parser_name="synthetic",
        parser_version="1",
        parser_fingerprint="synthetic",
        processing_status="succeeded",
        page_count=len(pages),
        parsed_pages=parsed_pages,
    )


def html_document(bid_id: uuid.UUID, path: str, html: str) -> ParsedDocument:
    return parse_document(html.encode(), path, bid_id, ParserOptions())


def chunked(parsed: ParsedDocument, config: ChunkConfig | None = None) -> ChunkingResult:
    digest = sha256(parsed.model_dump_json().encode())
    return chunk_document(parsed, config or ChunkConfig(), digest)


def generation(result: ChunkingResult) -> GenerationInput:
    return GenerationInput(
        generation_id=result.generation_id,
        bid_id=result.bid_id,
        document_id=result.document_id,
        relative_path=result.relative_path,
        status="completed",
        occurrences=result.occurrences,
    )


def sample_bid(bid_id: uuid.UUID = BID_A) -> list[ChunkingResult]:
    rfp = pdf_document(
        bid_id,
        "rfp/solicitation.pdf",
        [
            ["# 1 General", "Solicitation RFP-2026-014 requests twenty pumps for the water plant."],
            ["# 2 Schedule", "The submission deadline is March 3, 2026 at 2:00 PM local time."],
        ],
    )
    terms = pdf_document(
        bid_id, "rfp/terms.pdf", [["All proposals must remain valid for ninety days."]]
    )
    spec = pdf_document(
        bid_id,
        "specs/pumps.pdf",
        [["Pump model PX-420 with stainless impeller and SKU AB-77/X."]],
        doc_type=DocumentType.specs,
    )
    addendum = pdf_document(
        bid_id,
        "addenda/addendum-1.pdf",
        [["All proposals must remain valid for ninety days."]],
        doc_type=DocumentType.addendum,
        addendum_number=1,
    )
    notice = html_document(
        bid_id,
        "notice.html",
        '<h1 id="notice">Procurement notice</h1>'
        "<p>Pre-bid meeting held virtually for all prospective vendors.</p>",
    )
    return [chunked(doc) for doc in (rfp, terms, spec, addendum, notice)]
