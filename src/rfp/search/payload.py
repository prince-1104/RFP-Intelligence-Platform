"""One Qdrant point per Phase 3 source occurrence, carrying everything needed for citations."""

import uuid
from typing import Any

from rfp.schemas.chunks import ChunkOccurrence
from rfp.schemas.search import Citation, LocatorRef, SegmentRef, TableProvenance
from rfp.search.identifiers import extract_identifiers, identifier_terms

POINT_SCHEMA_VERSION = "1"


def point_id(occurrence_id: uuid.UUID, generation_id: uuid.UUID) -> str:
    return str(uuid.uuid5(occurrence_id, f"point:{generation_id}"))


def _locator(segment_locator: Any) -> dict[str, Any]:
    return LocatorRef.model_validate(segment_locator.model_dump()).model_dump(mode="json")


def build_payload(
    occurrence: ChunkOccurrence,
    *,
    generation_status: str,
    profile_fingerprint: str,
    indexing_run_id: str,
    token_count: int,
    truncated: bool,
) -> dict[str, Any]:
    segments = [
        SegmentRef(
            locator=LocatorRef.model_validate(segment.source_locator.model_dump()),
            block_id=segment.block_id,
            normalized_start=segment.normalized_start,
            normalized_end=segment.normalized_end,
            ocr_used=segment.ocr_used,
        ).model_dump(mode="json")
        for segment in occurrence.source_segments
    ]
    html_locators = [
        _locator(segment.source_locator)
        for segment in occurrence.source_segments
        if segment.source_locator.page_number is None
    ]
    table = None
    if occurrence.table is not None:
        table = TableProvenance(
            table_id=occurrence.table.table_id,
            table_index=occurrence.table.table_index,
            row_start=occurrence.table.row_start,
            row_end=occurrence.table.row_end,
            headers=occurrence.table.headers,
            extraction_method=occurrence.table.extraction_method,
        ).model_dump(mode="json")
    warnings = list(dict.fromkeys(occurrence.warnings))
    if truncated:
        warnings.append("embedding_input_truncated")
    if generation_status != "completed":
        warnings.append("partial_chunk_generation")
    return {
        "point_schema": POINT_SCHEMA_VERSION,
        "occurrence_id": str(occurrence.occurrence_id),
        "chunk_generation_id": str(occurrence.generation_id),
        "content_id": str(occurrence.content_id),
        "content_hash": occurrence.content_hash,
        "bid_id": str(occurrence.bid_id),
        "document_id": str(occurrence.document_id),
        "original_file_name": occurrence.original_file_name,
        "relative_path": occurrence.relative_path,
        "source_sha256": occurrence.source_sha256,
        "source_parser_fingerprint": occurrence.source_parser_fingerprint,
        "doc_type": occurrence.document_type.value,
        "addendum_number": occurrence.addendum_number,
        "chunk_type": occurrence.chunk_type,
        "section_heading": occurrence.section_heading,
        "section_path": occurrence.section_path,
        "sequence": occurrence.sequence,
        "page_start": occurrence.page_start,
        "page_end": occurrence.page_end,
        "html_locators": html_locators,
        "source_segments": segments,
        "table": table,
        "normalization_version": occurrence.normalization_version,
        "chunking_version": occurrence.chunking_version,
        "chunking_configuration_hash": occurrence.configuration_hash,
        "generation_status": generation_status,
        "warnings": warnings,
        "text": occurrence.normalized_text,
        "identifiers": identifier_terms(extract_identifiers(occurrence.normalized_text)),
        "embedding_token_count": token_count,
        "embedding_truncated": truncated,
        "profile_fingerprint": profile_fingerprint,
        "indexing_run_id": indexing_run_id,
        "index_active": False,
    }


def citation_from_payload(payload: dict[str, Any]) -> Citation:
    html_locators = [LocatorRef.model_validate(item) for item in payload.get("html_locators", [])]
    location_known = payload.get("page_start") is not None or any(
        locator.element_id or locator.heading or locator.element_index or locator.table_index
        for locator in html_locators
    )
    return Citation(
        occurrence_id=payload["occurrence_id"],
        chunk_generation_id=payload["chunk_generation_id"],
        content_id=payload["content_id"],
        content_hash=payload["content_hash"],
        bid_id=payload["bid_id"],
        document_id=payload["document_id"],
        original_file_name=payload["original_file_name"],
        relative_path=payload["relative_path"],
        document_type=payload["doc_type"],
        addendum_number=payload.get("addendum_number"),
        chunk_type=payload["chunk_type"],
        section_heading=payload.get("section_heading"),
        section_path=payload.get("section_path") or [],
        page_start=payload.get("page_start"),
        page_end=payload.get("page_end"),
        html_locators=html_locators,
        location_known=bool(location_known),
        table=TableProvenance.model_validate(payload["table"]) if payload.get("table") else None,
        source_segments=[SegmentRef.model_validate(s) for s in payload.get("source_segments", [])],
        normalization_version=payload["normalization_version"],
        chunking_version=payload["chunking_version"],
        warnings=payload.get("warnings") or [],
    )
