"""Repeated PDF header/footer removal before chunking (Phase C).

Synthetic PDFs are written with PyMuPDF and parsed by the production parser, so block coordinates
come from the same code path as real bids. The last tests read the local assessment artifacts when
they exist and skip otherwise.
"""

import uuid
from pathlib import Path

import pymupdf
import pytest
from search_fakes import chunked, generation, html_document, services

from rfp.agents.quotes import verbatim
from rfp.agents.retrieval import cited_pages, retrieve
from rfp.agents.revisions import reconcile
from rfp.chunking.engine import chunk_bytes
from rfp.chunking.furniture import (
    MIN_PAGES,
    detect,
    furniture_key,
    removable_blocks,
    strip,
)
from rfp.chunking.normalize import normalize
from rfp.cli import local_settings
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import parse_document
from rfp.schemas.chunks import ChunkConfig, ChunkingResult, sha256, stable_json
from rfp.schemas.parsed import DocumentType, ParsedDocument
from rfp.schemas.search import RetrievalMode

BID = uuid.UUID("5f0e4a3c-7d1b-4c8e-9a2f-3b6d8e1c0a47")
ESTIMATED = {"token_counter": "utf8_bytes_v1", "tokenizer_identifier": "utf8-bytes-div4-v1"}
CLEAN = ChunkConfig(**ESTIMATED, page_furniture="repeated_v1")
KEEP = ChunkConfig(**ESTIMATED)
HEADER_Y, FOOTER_Y = 36, 812
ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT = ROOT / "artifacts" / "assessment"


def pdf(pages: list[dict]) -> bytes:
    """Each page: optional ``header``/``footer`` text, ``body`` lines from y=150 every 40pt,
    and ``low`` lines placed just above the footer."""
    with pymupdf.open() as document:
        for spec in pages:
            page = document.new_page()
            if spec.get("header"):
                page.insert_text((60, HEADER_Y), spec["header"], fontsize=9)
            for number, line in enumerate(spec.get("body", [])):
                page.insert_text((60, 150 + 40 * number), line, fontsize=10)
            for number, line in enumerate(spec.get("low", [])):
                page.insert_text((60, 760 + 18 * number), line, fontsize=10)
            if spec.get("footer"):
                page.insert_text((60, FOOTER_Y), spec["footer"], fontsize=9)
        return document.tobytes()


def parsed(pages: list[dict], path: str = "rfp/solicitation.pdf") -> ParsedDocument:
    return parse_document(
        pdf(pages), path, BID, ParserOptions(ocr_min_text_chars=0, ocr_min_words=0)
    )


def texts(result: ChunkingResult) -> str:
    return "\n".join(o.normalized_text for o in result.occurrences)


def page_texts(result: ChunkingResult, number: int) -> str:
    return "\n".join(
        o.normalized_text
        for o in result.occurrences
        if (o.page_start or 0) <= number <= (o.page_end or 0)
    )


def assert_segments_on_pages(doc: ParsedDocument, result: ChunkingResult) -> int:
    """Every block segment of every chunk is in the chunk text and verbatim on its own page."""
    blocks = {b.block_id: b for p in doc.parsed_pages for b in p.blocks}
    pages = {p.page_number: p.text for p in doc.parsed_pages}
    checked = 0
    for occurrence in result.occurrences:
        for segment in occurrence.source_segments:
            if segment.block_id is None:
                continue
            source = normalize(blocks[segment.block_id].text, CLEAN)
            piece = source[segment.normalized_start : segment.normalized_end]
            assert piece in occurrence.normalized_text
            opening = " ".join(piece.split()[:12])
            assert opening in " ".join(pages[segment.source_locator.page_number].split())
            checked += 1
    return checked


def body(page: int) -> list[str]:
    return [f"Section {page} describes laptop configuration item {page}0 in detail."]


HEADER = "Harborview County Water Authority RFP 2026-031 Pump Replacement"


# 1. A repeated top-of-page header is removed (its first occurrence is kept).
def test_repeated_header_is_removed_after_its_first_occurrence():
    doc = parsed([{"header": HEADER, "body": body(n)} for n in range(1, 6)])
    result = chunked(doc, CLEAN)
    assert texts(result).count(HEADER) == 1
    assert HEADER in page_texts(result, 1)
    for number in range(1, 6):
        assert body(number)[0] in texts(result)
    (candidate,) = [c for c in detect(doc) if c.zone == "top"]
    assert candidate.decision == "removed" and candidate.removed_pages == [2, 3, 4, 5]


# 2. A repeated bottom-of-page footer is removed.
def test_repeated_footer_is_removed():
    footer = "Confidential - Procurement Office use only"
    doc = parsed([{"body": body(n), "footer": footer} for n in range(1, 5)])
    result = chunked(doc, CLEAN)
    assert texts(result).count(footer) == 1
    assert [c.zone for c in detect(doc) if c.decision == "removed"] == ["bottom"]


# 3. Page numbers that change per page are removed everywhere; numbers in the body stay.
def test_varying_page_numbers_are_removed_without_touching_body_numbers():
    doc = parsed([{"body": body(n), "footer": f"Page {n} of 4"} for n in range(1, 5)])
    result = chunked(doc, CLEAN)
    assert "Page 1 of 4" not in texts(result) and "of 4" not in texts(result)
    for number in range(1, 5):
        assert f"item {number}0" in texts(result)
    (candidate,) = detect(doc)
    assert candidate.key == "page # of #"
    assert candidate.reason == "page number repeated in the same band"


# 4. Whitespace, punctuation and digit values do not hide a repeat.
def test_minor_whitespace_and_punctuation_variants_are_one_candidate():
    variants = [
        "Harborview  County - RFP 2026-031",
        "Harborview County: RFP 2026/031.",
        "HARBORVIEW COUNTY RFP 2026 031",
        "Harborview County, RFP 2026-031",
    ]
    assert len({furniture_key(v) for v in variants}) == 1
    doc = parsed([{"header": v, "body": body(n)} for n, v in enumerate(variants, 1)])
    (candidate,) = detect(doc)
    assert candidate.decision == "removed" and candidate.removed_pages == [2, 3, 4]


# 5. A requirement repeated in the same band on every page is retained.
def test_repeated_substantive_requirement_is_retained():
    rule = "Vendors must sign and return this page with the proposal."
    doc = parsed([{"body": body(n), "footer": rule} for n in range(1, 6)])
    result = chunked(doc, CLEAN)
    assert texts(result).count(rule) == 5
    (candidate,) = detect(doc)
    assert candidate.decision == "retained" and candidate.reason == "contains requirement wording"


# 6. Header-like text on too few pages, or scattered, is not furniture.
def test_unique_or_scattered_header_like_text_is_retained():
    pages = [{"body": body(n)} for n in range(1, 11)]
    pages[0]["header"] = "Purchasing Department Notice"
    for n in (1, 5, 9):
        pages[n]["header"] = "Attachment Index"
    pages[3]["footer"] = pages[7]["footer"] = "Prepared by the Records Unit"
    doc = parsed(pages)
    result = chunked(doc, CLEAN)
    assert removable_blocks(detect(doc)) == set()
    assert texts(result).count("Attachment Index") == 3
    assert "Purchasing Department Notice" in texts(result)
    reasons = {c.key: c.reason for c in detect(doc)}
    assert reasons["attachment index"].startswith("scattered")
    assert reasons["prepared by the records unit"] == f"appears on fewer than {MIN_PAGES} pages"


# 7. Deadline and warranty statements in the page bands survive, repeated or not.
def test_deadline_and_warranty_near_the_edges_are_retained():
    deadline = "Proposals are due June 9, 2026 at 2:00 PM local time."
    warranty = "Three-year warranty"
    doc = parsed(
        [
            {"header": warranty, "body": body(n), "low": [deadline], "footer": f"Page {n}"}
            for n in range(1, 5)
        ]
    )
    result = chunked(doc, CLEAN)
    assert texts(result).count(deadline) == 4
    assert texts(result).count(warranty) == 4
    removed = [c.key for c in detect(doc) if c.decision == "removed"]
    assert removed == ["page #"]


# 8. A PDF without repeated furniture produces exactly the chunks it produced before.
def test_pdf_without_repeats_is_unchanged():
    doc = parsed([{"header": f"Part {chr(64 + n)} Overview", "body": body(n)} for n in range(1, 5)])
    cleaned, kept = chunked(doc, CLEAN), chunked(doc, KEEP)
    assert detect(doc) == [] or removable_blocks(detect(doc)) == set()
    assert [o.normalized_text for o in cleaned.occurrences] == [
        o.normalized_text for o in kept.occurrences
    ]
    assert [o.page_start for o in cleaned.occurrences] == [o.page_start for o in kept.occurrences]
    assert "repeated_page_furniture_removed" not in cleaned.warnings


# 9. Empty pages neither break detection nor shift page numbers.
def test_empty_pages_are_handled():
    pages = [{"header": HEADER, "body": body(n)} for n in range(1, 6)]
    pages.insert(2, {})
    doc = parsed(pages)
    result = chunked(doc, CLEAN)
    assert texts(result).count(HEADER) == 1
    assert body(5)[0] in page_texts(result, 6)


# 10. A page that holds only a repeated header does not fail chunking.
def test_page_with_only_furniture_does_not_fail():
    pages = [{"header": HEADER, "body": body(n)} for n in range(1, 5)]
    pages.append({"header": HEADER})
    result = chunked(parsed(pages), CLEAN)
    assert result.processing_status == "succeeded"
    assert all(o.page_start != 5 for o in result.occurrences)


# 11. Page numbers of every remaining chunk are the pages its text is on.
def test_page_numbers_stay_accurate_after_cleanup():
    doc = parsed(
        [{"header": HEADER, "body": body(n), "footer": f"Page {n} of 6"} for n in range(1, 7)]
    )
    result = chunked(doc, CLEAN)
    assert assert_segments_on_pages(doc, result) >= 6
    for number in range(1, 7):
        (owner,) = [o for o in result.occurrences if body(number)[0] in o.normalized_text]
        assert owner.page_start <= number <= owner.page_end


def _indexed(settings, results):
    indexer, search, _ = services(settings)
    report = indexer.index([generation(result) for result in results])
    assert report.succeeded
    return indexer, search.search


# 12. Citations from the cleaned index resolve to the original page text.
def test_citations_validate_against_original_pages(settings):
    doc = parsed(
        [{"header": HEADER, "body": body(n), "footer": f"Page {n} of 4"} for n in range(1, 4)]
        + [{"header": HEADER, "body": ["The awarded vendor must deliver within 45 days."]}]
    )
    _, search = _indexed(settings, [chunked(doc, CLEAN)])
    hits, _ = retrieve(
        search,
        BID,
        "deliver within 45 days",
        mode=RetrievalMode.hybrid,
        top_k=3,
        doc_types=[],
        addendum_numbers=[],
        evidence_prefix="E",
    )
    pages = {p.page_number: p.text for p in doc.parsed_pages}
    top = next(h for h in hits if "45 days" in h.text)
    quote = "The awarded vendor must deliver within 45 days."
    (page,) = cited_pages(top.sources[0], top.text, quote)
    assert page == 4 and verbatim(quote, pages[page]) is not None
    assert top.text.count(HEADER) <= 1


# 13. Addendum reconciliation over cleaned chunks finds the right quote and page.
def test_addendum_reconciliation_still_finds_source_and_quote(settings):
    header = "ADDENDUM No. 2 RFP 2026-031 Pump Replacement"
    answer = "Answer: The warranty period is changed to five years for all pumps."
    addendum = parse_document(
        pdf(
            [
                {
                    "header": header,
                    "body": ["1. Is a two-year warranty acceptable?"],
                    "footer": "Page 1 | 3",
                },
                {
                    "header": header,
                    "body": ["2. What warranty applies?", answer],
                    "footer": "Page 2 | 3",
                },
                {"header": header, "body": ["END OF ADDENDUM"], "footer": "Page 3 | 3"},
            ]
        ),
        "addenda/Addendum 2.pdf",
        BID,
        ParserOptions(ocr_min_text_chars=0, ocr_min_words=0),
    )
    assert addendum.doc_type == DocumentType.addendum and addendum.addendum_number == 2
    _, search = _indexed(settings, [chunked(addendum, CLEAN)])
    hits, _ = retrieve(
        search,
        BID,
        "warranty period",
        mode=RetrievalMode.hybrid,
        top_k=5,
        doc_types=[DocumentType.addendum],
        addendum_numbers=[],
        evidence_prefix="A",
    )
    history = reconcile("warranty", ["warranty"], hits, BID)
    (revision,) = [r for r in history.revisions if r.material]
    assert revision.change_type == "replace" and revision.addendum_number == 2
    (source,) = revision.sources
    assert source.page == 2
    pages = {p.page_number: p.text for p in addendum.parsed_pages}
    assert verbatim(source.quote, pages[2]) is not None
    assert header not in revision.statement and "Page 2" not in revision.statement


# 14. HTML documents are never touched by the PDF cleanup.
def test_html_ingestion_is_unchanged():
    banner = "<p>County Procurement Portal</p>"
    html = "".join(
        f'{banner}<h2 id="s{n}">Section {n}</h2><p>Requirement {n}: vendors list item {n}.</p>'
        for n in range(1, 6)
    )
    doc = html_document(BID, "notice.html", html)
    assert detect(doc) == []
    cleaned, kept = chunked(doc, CLEAN), chunked(doc, KEEP)
    assert [o.normalized_text for o in cleaned.occurrences] == [
        o.normalized_text for o in kept.occurrences
    ]


# 15. Indexing the same cleaned document twice adds no points.
def test_repeated_ingestion_creates_no_duplicate_points(settings):
    doc = parsed([{"header": HEADER, "body": body(n)} for n in range(1, 5)])
    first, second = chunked(doc, CLEAN), chunked(doc, CLEAN)
    assert first.generation_id == second.generation_id
    indexer, _ = _indexed(settings, [first])
    report = indexer.index([generation(second)])
    assert report.as_dict()["unchanged"] == 1
    manager = indexer.collection()
    assert indexer.client.count(manager.name, exact=True).count == len(first.occurrences)


# 16. Detection and chunking are deterministic; cleaning cleaned text removes nothing more.
def test_cleanup_is_deterministic_and_idempotent():
    doc = parsed(
        [{"header": HEADER, "body": body(n), "footer": f"Page {n} of 5"} for n in range(1, 6)]
    )
    assert [c.as_dict() for c in detect(doc)] == [c.as_dict() for c in detect(doc)]
    assert chunk_bytes(chunked(doc, CLEAN)) == chunk_bytes(chunked(doc, CLEAN))
    once = strip(doc, removable_blocks(detect(doc)))
    assert removable_blocks(detect(once)) == set()
    assert [o.normalized_text for o in chunked(once, CLEAN).occurrences] == [
        o.normalized_text for o in chunked(doc, CLEAN).occurrences
    ]


# 19. Configuration compatibility: old chunk artifacts keep their hashes, settings enable cleanup.
def test_configuration_hash_is_unchanged_when_cleanup_is_off():
    legacy = ChunkConfig(**ESTIMATED).model_dump(mode="json")
    legacy.pop("page_furniture")
    assert ChunkConfig.model_validate(legacy).fingerprint == sha256(stable_json(legacy))
    assert CLEAN.fingerprint != KEEP.fingerprint
    assert local_settings().chunking.page_furniture == "repeated_v1"


# 20. Headers that look alike but say different things are kept.
def test_similar_headers_with_different_content_are_kept():
    headers = [
        "Exhibit A - Pricing schedule for pumps",
        "Exhibit A - Delivery schedule for pumps",
        "Exhibit A - Insurance schedule for pumps",
        "Exhibit A - Training schedule for pumps",
    ]
    doc = parsed([{"header": h, "body": body(n)} for n, h in enumerate(headers, 1)])
    result = chunked(doc, CLEAN)
    for header in headers:
        assert header in texts(result)
    assert removable_blocks(detect(doc)) == set()


def _real_documents() -> list[ParsedDocument]:
    paths = sorted(ASSESSMENT.glob("v2/*/parsed/*.json"))
    documents = [
        ParsedDocument.model_validate_json(p.read_bytes())
        for p in paths
        if p.name != "summary.json"
    ]
    return [d for d in documents if d.source_content_type == "application/pdf"]


@pytest.mark.skipif(not ASSESSMENT.is_dir(), reason="local assessment artifacts not present")
def test_real_bid_pdfs_keep_every_chunk_verbatim_on_its_pages():
    documents = _real_documents()
    if not documents:
        pytest.skip("no parsed assessment PDFs")
    removed = 0
    for doc in documents:
        candidates = detect(doc)
        removed += len(removable_blocks(candidates))
        for candidate in candidates:
            if candidate.decision == "removed":
                assert len(candidate.pages) >= MIN_PAGES
                assert candidate.pages[0] not in candidate.removed_pages or (
                    candidate.reason == "page number repeated in the same band"
                )
        assert_segments_on_pages(doc, chunked(doc, CLEAN))
    assert removed > 0


@pytest.mark.skipif(not ASSESSMENT.is_dir(), reason="local assessment artifacts not present")
def test_real_bid_requirement_wording_is_never_removed():
    documents = _real_documents()
    if not documents:
        pytest.skip("no parsed assessment PDFs")
    for doc in documents:
        by_id = {block.block_id: block.text for page in doc.parsed_pages for block in page.blocks}
        for block_id in removable_blocks(detect(doc)):
            text = by_id[block_id].casefold()
            for cue in ("shall", "must", "warranty", "due", "deadline", "submit", "price"):
                assert f" {cue} " not in f" {text} "
