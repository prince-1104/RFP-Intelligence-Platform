import uuid

import pytest
from pydantic import ValidationError

from rfp.chunking.engine import chunk_bytes, chunk_document
from rfp.chunking.normalize import normalize
from rfp.chunking.tokens import EstimatedTokenCounter
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import parse_document
from rfp.schemas.chunks import ChunkConfig, ChunkingResult, sha256
from rfp.schemas.parsed import (
    DocumentType,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    SourceLocator,
)

BID = uuid.UUID("e052f49e-3e7e-4f08-96ad-4cf5c0376a7e")


def document(pages, path="specs.pdf", source_hash=None, status="succeeded"):
    digest = source_hash or sha256("\n".join(page.text for page in pages).encode())
    return ParsedDocument(
        document_id=uuid.uuid5(BID, path + digest),
        bid_id=BID,
        original_file_name=path,
        relative_path=path,
        source_sha256=digest,
        source_content_type="application/pdf",
        parser_name="synthetic",
        parser_version="1",
        parser_fingerprint="synthetic",
        page_count=len(pages),
        parsed_pages=pages,
        processing_status=status,
    )


def page(texts, number=1, path="specs.pdf", **kwargs):
    blocks = [
        ParsedBlock(
            block_id=f"p{number}-{i}",
            block_type="text",
            text=value,
            source_locator=SourceLocator(relative_path=path, page_number=number),
        )
        for i, value in enumerate(texts)
    ]
    return ParsedPage(page_number=number, text="\n\n".join(texts), blocks=blocks, **kwargs)


def small_config(**kwargs):
    return ChunkConfig(target_tokens=30, max_tokens=40, overlap_tokens=8, **kwargs)


def test_conservative_normalization():
    text = (
        "Cafe\u0301  PX-420  SKU:A-17\r\n\r\n$1,250.00  15%  03/04/2026  10:30 AM"
        "\nnon-\ncompliance\nshall not waive any rights."
    )
    normalized = normalize(text, ChunkConfig())
    for value in (
        "Café",
        "PX-420",
        "SKU:A-17",
        "$1,250.00",
        "15%",
        "03/04/2026",
        "10:30 AM",
        "non-",
        "shall not waive any rights.",
    ):
        assert value in normalized
    assert normalize(normalized, ChunkConfig()) == normalized
    assert normalize("speci\u00ad\nfication", ChunkConfig()) == "specification"
    assert "PX-\n420" == normalize("PX-\n420", ChunkConfig())


def test_determinism_source_immutability_and_multpage_provenance():
    parsed = document(
        [page(["Supply PX-420 pumps."], 1), page(["Quantity 20; retain legal wording."], 2)]
    )
    before = parsed.model_dump_json()
    result = chunk_document(parsed, ChunkConfig())
    assert chunk_bytes(result) == chunk_bytes(chunk_document(parsed, ChunkConfig()))
    assert parsed.model_dump_json() == before
    occurrence = result.occurrences[0]
    assert occurrence.page_start == 1 and occurrence.page_end == 2
    assert [segment.source_locator.page_number for segment in occurrence.source_segments] == [1, 2]
    assert ChunkingResult.model_validate_json(chunk_bytes(result)) == result


def test_html_sections_lists_and_no_unstructured_data_loss():
    parsed = parse_document(
        b"""<nav>Procurement help desk</nav><h1 id="a">1 General</h1>
    <p>Supply PX-420 equipment.</p><h2 id="b">1.1 Warranty</h2>
    <ul><li>Warranty shall remain valid.</li></ul><h1 id="c">2 Payment</h1>
    <p>Payment within 30 days.</p>""",
        "bid.html",
        BID,
        ParserOptions(),
    )
    result = chunk_document(parsed, small_config())
    assert all(
        occurrence.page_start is None and occurrence.page_end is None
        for occurrence in result.occurrences
    )
    assert any(
        occurrence.chunk_type == "list" and occurrence.section_path == ["1 General", "1.1 Warranty"]
        for occurrence in result.occurrences
    )
    assert "Procurement help desk" in "\n".join(c.text for c in result.contents)
    assert any(
        segment.source_locator.element_id == "c"
        for occ in result.occurrences
        for segment in occ.source_segments
    )
    assert not any(
        "Warranty" in occ.normalized_text and "Payment within" in occ.normalized_text
        for occ in result.occurrences
    )


def test_oversized_prose_overlap_and_budget():
    text = " ".join(f"word{i:03d}" for i in range(100))
    result = chunk_document(document([page([text])]), small_config())
    chunks = result.occurrences
    assert len(chunks) > 2
    assert all(c.estimated_token_count <= 40 for c in chunks)
    assert set(text.split()) <= set(" ".join(c.normalized_text for c in chunks).split())
    assert set(chunks[0].normalized_text.split()) & set(chunks[1].normalized_text.split())
    assert chunks[0].source_segments[0].normalized_start == 0
    assert chunks[1].source_segments[0].normalized_start > 0


def test_tables_repeat_headers_preserve_rows_and_remove_confident_text_copy():
    rows = [["Model", "Qty"], ["PX-420", "20"], ["SKU-B17", "10"], ["C-9", "1"]]
    table = ParsedTable(
        table_id="p1-t1",
        table_index=1,
        headers=rows[0],
        rows=rows,
        source_locator=SourceLocator(relative_path="specs.pdf", page_number=1, table_index=1),
        extraction_method="pdfplumber",
        warnings=["header_inferred"],
    )
    native = page(["Model Qty", "PX-420 20", "SKU-B17 10", "C-9 1"], tables=[table])
    result = chunk_document(document([native]), small_config(table_row_limit=2))
    groups = [chunk for chunk in result.occurrences if chunk.chunk_type == "table_rows"]
    assert len(groups) >= 2
    assert not any(chunk.chunk_type == "prose" for chunk in result.occurrences)
    assert all(chunk.normalized_text.startswith('Headers: ["Model", "Qty"]') for chunk in groups)
    assert [row for group in groups for row in group.table.rows] == rows
    assert groups[0].table.row_start == 1 and groups[-1].table.row_end == 4
    assert all(group.page_start == 1 and "header_inferred" in group.warnings for group in groups)


def test_missing_headers_and_oversized_row_not_invented_or_split():
    row = ["PX-420", "X" * 300, "20 units"]
    table = ParsedTable(
        table_id="t",
        table_index=1,
        rows=[row],
        headers=None,
        source_locator=SourceLocator(relative_path="specs.pdf", page_number=1),
        extraction_method="pdfplumber",
        warnings=["ambiguous_table_headers"],
    )
    result = chunk_document(document([page([], tables=[table])]), small_config())
    occurrence = result.occurrences[0]
    assert occurrence.table.rows == [row] and occurrence.table.headers is None
    assert "indivisible_row_or_identifier_exceeds_token_budget" in occurrence.warnings


def test_repetition_is_evidence_not_deleted():
    text = "The contractor shall not waive any rights."
    parsed = document([page([text]), page([text], 2)])
    result = chunk_document(parsed, small_config())
    assert sum(occ.normalized_text.count(text) for occ in result.occurrences) >= 2
    assert {
        segment.source_locator.page_number
        for occ in result.occurrences
        for segment in occ.source_segments
    } == {1, 2}


def test_shared_content_distinct_documents_and_addendum_authority():
    text = "The contractor shall retain all legal rights."
    a = document([page([text])])
    b = document(
        [page([text], path="addendum.pdf")], path="addendum.pdf", source_hash=a.source_sha256
    )
    b.doc_type = DocumentType.addendum
    b.addendum_number = 2
    first, second = chunk_document(a, small_config()), chunk_document(b, small_config())
    assert first.contents[0].content_id == second.contents[0].content_id
    assert first.occurrences[0].occurrence_id != second.occurrences[0].occurrence_id
    assert second.occurrences[0].addendum_number == 2
    assert second.occurrences[0].document_type == "addendum"


def test_ocr_and_partial_source_warnings():
    p = page(
        ["OCR evidence: PX-420 quantity 20."],
        ocr_used=True,
        ocr_language="eng",
        ocr_average_confidence=82,
        warnings=["ocr_table_structure_not_recovered"],
    )
    p.blocks[0].block_type = "ocr_line"
    result = chunk_document(document([p], status="partially_succeeded"), small_config())
    occurrence = result.occurrences[0]
    assert result.processing_status == "partially_succeeded" and occurrence.chunk_type == "ocr"
    assert occurrence.source_segments[0].ocr_average_confidence == 82
    assert "ocr_table_structure_not_recovered" in occurrence.warnings
    assert occurrence.table is None


def test_failure_empty_threshold_and_limits():
    parsed = document([page(["Readable."])])
    limited = chunk_document(parsed, small_config(min_content_chars=100))
    assert limited.processing_status == "failed" and not limited.occurrences
    assert chunk_document(
        parsed, small_config(min_content_chars=100, empty_behavior="skipped")
    ).skipped
    oversized = document([page(["one two three. " * 200])])
    result = chunk_document(oversized, small_config(max_chunks=1))
    assert result.processing_status == "failed" and not result.occurrences
    assert "chunk_count_limit" in result.warnings


def test_effective_configuration_validation_and_counter():
    with pytest.raises(ValidationError):
        ChunkConfig(target_tokens=50, max_tokens=40)
    with pytest.raises(ValidationError):
        ChunkConfig(overlap_tokens=600)
    config = small_config()
    assert config.fingerprint != config.model_copy(update={"table_row_limit": 3}).fingerprint

    class WrongCounter(EstimatedTokenCounter):
        identifier = "different"

    with pytest.raises(ValueError, match="identifier"):
        chunk_document(document([page(["hello"])]), config, counter=WrongCounter())


def test_exact_chunk_limit_and_output_bounds():
    parsed = document([page(["Single readable paragraph."]), page([], 2)])
    assert chunk_document(parsed, small_config(max_chunks=1)).processing_status == "succeeded"
    text = "Preserve every requirement. " * 100
    limited = chunk_document(document([page([text])]), small_config(max_output_chars=100))
    assert limited.processing_status == "failed" and not limited.occurrences
    assert "chunk_output_character_limit" in limited.warnings


def test_uncertain_duplicate_table_text_is_preserved():
    rows = [["SKU", "Qty"], ["PX-420", "20"]]
    table = ParsedTable(
        table_id="t",
        table_index=1,
        rows=rows,
        headers=rows[0],
        source_locator=SourceLocator(relative_path="specs.pdf", page_number=1),
        extraction_method="pdfplumber",
    )
    parsed = document([page(["SKU Qty PX-420 20", "SKU Qty PX-420 20"], tables=[table])])
    result = chunk_document(parsed, small_config())
    assert "table_prose_overlap_uncertain" in result.warnings
    assert any(c.chunk_type == "prose" for c in result.occurrences)


def test_nested_settings_are_validated(monkeypatch):
    from rfp.cli import local_settings

    monkeypatch.setenv("CHUNKING__TARGET_TOKENS", "100")
    monkeypatch.setenv("CHUNKING__MAX_TOKENS", "200")
    monkeypatch.setenv("CHUNKING__OVERLAP_TOKENS", "25")
    assert local_settings().chunking.target_tokens == 100
    monkeypatch.setenv("CHUNKING__OVERLAP_TOKENS", "200")
    with pytest.raises(ValidationError):
        local_settings()


def test_normalized_block_copy_and_identifier_residual_are_safe():
    parsed = document([page(["Cafe\u0301 speci\u00ad\nfication."])])
    result = chunk_document(parsed, small_config())
    assert len(result.occurrences) == 1
    assert result.occurrences[0].normalized_text == "Café specification."
    p = page(["123"])
    p.text = "PX-123\n123"
    result = chunk_document(document([p]), small_config())
    assert "PX-123" in "\n".join(content.text for content in result.contents)


def test_numbered_heading_cannot_inherit_unrelated_parent():
    parsed = parse_document(
        b"<h1>2 Payment</h1><p>Payment terms.</p><h2>1.1 Warranty</h2><p>Warranty evidence.</p>",
        "bid.html",
        BID,
        ParserOptions(),
    )
    result = chunk_document(parsed, small_config())
    warranty = next(
        occ for occ in result.occurrences if occ.normalized_text == "Warranty evidence."
    )
    assert warranty.section_path == ["1.1 Warranty"]
    assert "numbered_heading_parent_unavailable" in result.warnings
