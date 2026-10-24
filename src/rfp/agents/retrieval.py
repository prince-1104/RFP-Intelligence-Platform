"""Search tool adapter. Every query is bid-scoped. Citations come only from search hits."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from uuid import UUID

from rfp.agents.schemas import EvidenceHit, ExtractionConfig, SourceRef
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import Citation, RetrievalMode, SearchFilters, SearchRequest, SearchResponse
from rfp.search.errors import SearchServiceError


def _html_id(citation: Citation) -> str | None:
    for locator in citation.html_locators:
        if locator.element_id:
            return locator.element_id
    return None


def _source(citation: Citation) -> SourceRef | None:
    pdf = citation.relative_path.lower().endswith(".pdf")
    page = citation.page_start if pdf else None
    html_id = None if pdf else _html_id(citation)
    if pdf and (page is None or page < 1):
        return None
    if not pdf and not html_id:
        return None
    if not pdf and citation.page_start is not None:
        return None
    return SourceRef(
        file=citation.original_file_name,
        relative_path=citation.relative_path,
        document_type=citation.document_type,
        addendum_number=citation.addendum_number,
        page=page,
        page_end=(citation.page_end or page) if pdf else None,
        html_element_id=html_id,
        bid_id=citation.bid_id,
    )


def hits_from_response(
    response: SearchResponse, bid_id: UUID, evidence_prefix: str
) -> list[EvidenceHit]:
    hits: list[EvidenceHit] = []
    seen: set[tuple[str, int | None, str | None, str]] = set()
    for result in response.results:
        sources = []
        for citation in (result.citation, *result.additional_citations):
            if citation.bid_id != bid_id:
                continue
            source = _source(citation)
            if source is not None:
                sources.append(source)
        if not sources:
            continue
        primary = sources[0]
        key = (primary.relative_path, primary.page, primary.html_element_id, result.text)
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            EvidenceHit(
                evidence_id=f"{evidence_prefix}{len(hits) + 1}",
                bid_id=bid_id,
                text=result.text,
                rank=result.rank,
                mode=response.mode.value,
                final_score=result.scores.final_score,
                sources=sources,
            )
        )
    return hits


def retrieve(
    search: Callable[[SearchRequest], SearchResponse],
    bid_id: UUID,
    query: str,
    *,
    mode: RetrievalMode,
    top_k: int,
    doc_types: list[DocumentType],
    addendum_numbers: list[int],
    evidence_prefix: str,
    lock: threading.Lock | None = None,
) -> tuple[list[EvidenceHit], SearchResponse]:
    request = SearchRequest(
        query=query,
        top_k=top_k,
        mode=mode,
        filters=SearchFilters(
            bid_ids=[bid_id],
            doc_types=doc_types,
            addendum_numbers=addendum_numbers,
        ),
    )
    if request.filters.bid_ids != [bid_id]:
        raise SearchServiceError(
            "Refusing a search that is not limited to the current bid", "bid_scope"
        )
    if lock is None:
        response = search(request)
    else:
        with lock:
            response = search(request)
    return hits_from_response(response, bid_id, evidence_prefix), response


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def top_k_for(hard: bool, config: ExtractionConfig) -> int:
    return config.hard_top_k if hard else config.top_k
