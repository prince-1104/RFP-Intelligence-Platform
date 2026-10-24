"""Search tool adapter. Every query is bid-scoped. Citations come only from search hits."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable
from uuid import UUID

from rfp.agents.quotes import locate
from rfp.agents.schemas import EvidenceHit, ExtractionConfig, PageSpan, SourceRef
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import Citation, RetrievalMode, SearchFilters, SearchRequest, SearchResponse
from rfp.search.errors import SearchServiceError


def _html_id(citation: Citation) -> str | None:
    for locator in citation.html_locators:
        if locator.element_id:
            return locator.element_id
    return None


def page_spans(citation: Citation, text: str) -> list[PageSpan]:
    """Per-page ranges of a chunk that crosses pages.

    Chunk text is its source pieces joined by a blank line. The map is only
    trusted when the segment lengths add up to the text length exactly.
    """
    segments = citation.source_segments
    pages = {segment.locator.page_number for segment in segments}
    if len(pages) < 2 or None in pages:
        return []
    lengths = [segment.normalized_end - segment.normalized_start for segment in segments]
    if sum(lengths) + 2 * (len(segments) - 1) != len(text):
        return []
    spans: list[PageSpan] = []
    cursor = 0
    for segment, length in zip(segments, lengths, strict=True):
        page = segment.locator.page_number
        if page is not None and page >= 1:
            spans.append(PageSpan(page=page, start=cursor, end=cursor + length))
        cursor += length + 2
    return spans


def _anchors(value: str, text: str) -> list[tuple[int, int]]:
    span = locate(value, text)
    if span is not None:
        return [span]
    found: list[tuple[int, int]] = []
    for part in re.split(r"[,;|]\s*|\s+-\s+", value):
        span = locate(part, text) if len(part.strip()) >= 4 else None
        if span is not None:
            found.append(span)
            continue
        for word in re.findall(r"[^\s,;()]{4,}", part):
            span = locate(word, text)
            if span is not None:
                found.append(span)
    return found


def cited_pages(source: SourceRef, text: str, value: str | None) -> list[int]:
    """Pages of a PDF source that hold the value; the chunk's first page when unknown."""
    if source.page is None:
        return []
    if not source.spans or not value:
        return [source.page]
    pages = {
        span.page
        for start, end in _anchors(value, text)
        for span in source.spans
        if start < span.end and end > span.start
    }
    return sorted(pages) or [source.page]


def _source(citation: Citation, text: str = "") -> SourceRef | None:
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
        spans=page_spans(citation, text) if pdf else [],
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
            source = _source(citation, result.text)
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
