import hashlib
import uuid
from unittest.mock import Mock

import pymupdf
import pytest

from rfp.ingestion.metadata import classify
from rfp.ingestion.ocr import ocr_page
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import artifact_bytes, parse_document, parse_isolated
from rfp.schemas.parsed import DocumentType, ParsedBlock, ParsedDocument, ParseStatus, SourceLocator

BID = uuid.UUID("b1d88da1-b5d8-40dd-8ef4-05dd5b8d91cf")
BODY = "Request for proposal: supply equipment with model AB-123 and quantity 20 units."


def make_pdf(texts=(BODY,), table=False):
    with pymupdf.open() as document:
        for value in texts:
            page = document.new_page()
            if value:
                page.insert_text((50, 50), value)
        if table:
            page = document[0]
            for x in (50, 220, 390):
                page.draw_line((x, 100), (x, 200))
            for y in (100, 150, 200):
                page.draw_line((50, y), (390, y))
            for x, y, value in [
                (60, 120, "Model"),
                (230, 120, "Quantity"),
                (60, 170, "AB-123"),
                (230, 170, "20"),
            ]:
                page.insert_text((x, y), value)
        return document.tobytes()


def fake_ocr(page, options, executable, locator, prefix):
    block = ParsedBlock(
        block_id=f"{prefix}-ocr-1", block_type="ocr_line", text=BODY, source_locator=locator
    )
    return BODY, [block], 83.5


def test_native_pdf_pages_and_tables(monkeypatch):
    ocr = Mock(side_effect=AssertionError("OCR must not run"))
    monkeypatch.setattr("rfp.ingestion.pdf.ocr_page", ocr)
    data = make_pdf((BODY, BODY + " Page two."), table=True)
    result = parse_document(data, "nested/specs.pdf", BID, ParserOptions())
    assert result.processing_status == ParseStatus.succeeded
    assert [page.page_number for page in result.parsed_pages] == [1, 2]
    assert result.parsed_pages[0].tables[0].rows == [["Model", "Quantity"], ["AB-123", "20"]]
    assert result.parsed_pages[0].tables[0].source_locator.page_number == 1
    assert result.parsed_pages[1].blocks[0].layout.bbox[0] > 0
    assert "AB-123" in result.parsed_pages[0].text
    ocr.assert_not_called()


@pytest.mark.parametrize("forced,text", [(False, ""), (True, BODY)])
def test_ocr_fallback_and_force(monkeypatch, forced, text):
    ocr = Mock(side_effect=fake_ocr)
    monkeypatch.setattr("rfp.ingestion.pdf.ocr_page", ocr)
    result = parse_document(make_pdf((text,)), "scan.pdf", BID, ParserOptions(ocr_force=forced))
    page = result.parsed_pages[0]
    assert result.processing_status == ParseStatus.succeeded
    assert page.ocr_used and page.ocr_language == "eng"
    assert page.ocr_average_confidence == 83.5
    assert "ocr_table_structure_not_recovered" in page.warnings
    ocr.assert_called_once()


def test_ocr_failure_partial_and_empty(monkeypatch):
    monkeypatch.setattr("rfp.ingestion.pdf.ocr_page", Mock(side_effect=RuntimeError("secret")))
    partial = parse_document(make_pdf((BODY, "")), "scan.pdf", BID, ParserOptions())
    assert partial.processing_status == ParseStatus.partially_succeeded
    assert partial.parsed_pages[1].ocr_used
    assert "secret" not in partial.model_dump_json()
    empty = parse_document(make_pdf(("",)), "empty.pdf", BID, ParserOptions())
    assert empty.processing_status == ParseStatus.failed


def test_corrupted_and_encrypted_pdf():
    assert parse_document(b"corrupt", "bad.pdf", BID, ParserOptions()).processing_status == "failed"
    with pymupdf.open(stream=make_pdf(), filetype="pdf") as document:
        data = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="private"
        )
    result = parse_document(data, "locked.pdf", BID, ParserOptions())
    assert result.warnings == ["password_protected_pdf", "document_has_no_readable_content"]


def test_page_failure_and_limit(monkeypatch):
    from rfp.ingestion.pdf import native_page

    def fail_second(page, locator):
        if locator.page_number == 2:
            raise ValueError("bad page")
        return native_page(page, locator)

    monkeypatch.setattr("rfp.ingestion.pdf.native_page", fail_second)
    monkeypatch.setattr("rfp.ingestion.pdf.ocr_page", Mock(side_effect=RuntimeError("ocr")))
    data = make_pdf((BODY, BODY))
    partial = parse_document(data, "rfp.pdf", BID, ParserOptions())
    assert partial.processing_status == "partially_succeeded"
    assert "native_text_extraction_failed" in partial.parsed_pages[1].warnings
    limited = parse_document(data, "rfp.pdf", BID, ParserOptions(max_pages=1))
    assert limited.page_count == 2 and len(limited.parsed_pages) == 1
    assert "pdf_page_limit" in limited.warnings


def test_html_provenance_and_safe_content(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("network")))
    data = b"""<html><title>Bid supply</title><body><nav>Contact procurement desk</nav>
    <h1 id="terms">Equipment</h1><p>Model AB-123 costs 20.</p>
    <ul><li>Item one</li></ul><a href="files/spec.pdf#part">Specifications</a>
    <table id="items"><tr><th>Model</th><th>Qty</th></tr>
    <tr><td>AB-123</td><td>20</td></tr></table>
    <script>evil()</script><style>secret css</style><p hidden>hidden secret</p></body>"""
    result = parse_document(data, "nested/bid.htm", BID, ParserOptions())
    page = result.parsed_pages[0]
    assert result.title == "Bid supply" and result.page_count == 0 and page.page_number is None
    assert "Contact procurement desk" in page.text
    assert "evil()" not in page.text and "secret" not in page.text
    assert page.tables[0].headers == ["Model", "Qty"]
    assert page.tables[0].source_locator.element_id == "items"
    assert page.tables[0].source_locator.table_index == 1
    assert (
        next(
            block for block in page.blocks if block.block_type == "heading"
        ).source_locator.element_id
        == "terms"
    )
    assert (
        next(block for block in page.blocks if block.block_type == "link").link_url
        == "files/spec.pdf#part"
    )


def test_malformed_html_missing_title_and_empty():
    result = parse_document(b"<h2>Title<p>Text <b>broken", "a.html", BID, ParserOptions())
    assert result.title is None and "broken" in result.parsed_pages[0].text
    assert (
        parse_document(b"<script>only()</script>", "a.html", BID, ParserOptions()).processing_status
        == "failed"
    )
    limited = parse_document(b"<p>x</p>" * 100, "a.html", BID, ParserOptions(html_max_nodes=10))
    assert limited.processing_status == "failed" and "html_node_limit" in limited.warnings


@pytest.mark.parametrize(
    "name,text,kind,number",
    [
        ("Addendum No. 2.pdf", "", "addendum", 2),
        ("a.pdf", "Addendum 1 and Addendum 2", "addendum", None),
        ("specs.pdf", "", "specs", None),
        ("a.pdf", "Affidavit", "affidavit", None),
        ("rfp.pdf", "", "rfp", None),
        ("a.pdf", "No metadata", "other", None),
    ],
)
def test_metadata_classification(name, text, kind, number):
    result = classify(name, text, "application/pdf")
    assert result["doc_type"] == DocumentType(kind)
    assert result["addendum_number"] == number
    assert result["document_date"] is None


def test_explicit_dates_and_ambiguity():
    result = classify("a.html", "Date issued: 2025-02-03 Deadline: 2025-03-04", "text/html")
    assert result["document_date"].isoformat() == "2025-02-03"
    assert classify("a.pdf", "03/04/2025", "application/pdf")["document_date"] is None
    assert (
        classify("a.pdf", "Published on: 2025-01-01\nDate issued: 2025-02-02", "application/pdf")[
            "document_date"
        ]
        is None
    )


def test_configurable_metadata_patterns():
    options = ParserOptions(metadata_type_patterns={"specs": "technical schedule"})
    result = parse_document(b"<h1>Technical schedule</h1>", "terms.html", BID, options)
    assert result.doc_type == "specs"
    assert options.fingerprint != ParserOptions().fingerprint
    assert (
        classify(
            "terms.html",
            "Technical schedule",
            "text/html",
            {"specs": "schedule", "rfp": "schedule"},
        )["doc_type"]
        == "other"
    )


def test_invalid_provenance_is_rejected():
    from pydantic import ValidationError

    result = parse_document(make_pdf(), "source.pdf", BID, ParserOptions()).model_dump()
    result["parsed_pages"][0]["blocks"][0]["source_locator"]["page_number"] = 2
    with pytest.raises(ValidationError, match="locators must match"):
        ParsedDocument.model_validate(result)


def test_deterministic_artifact_and_source_identity():
    data = "<h1>Unicode café</h1><p>Quantity 20 model AB-123</p>".encode()
    first = parse_document(data, "nested/a.html", BID, ParserOptions())
    second = parse_document(data, "nested/a.html", BID, ParserOptions())
    assert artifact_bytes(first) == artifact_bytes(second)
    assert first.source_sha256 == hashlib.sha256(data).hexdigest()
    assert (
        first.document_id != parse_document(data, "nested/b.html", BID, ParserOptions()).document_id
    )
    assert ParsedDocument.model_validate_json(artifact_bytes(first)) == first


def test_real_structured_ocr_wrapper_closes_images(monkeypatch):
    captured = []

    def fake(image, **kwargs):
        captured.append(image)
        assert image.width * image.height <= 100000
        assert kwargs["lang"] == "eng"
        return {
            "text": ["", "AB-123", "20"],
            "conf": [-1, 80, 90],
            "block_num": [0, 1, 1],
            "par_num": [0, 1, 1],
            "line_num": [0, 1, 1],
            "left": [0, 10, 40],
            "top": [0, 10, 10],
            "width": [0, 20, 10],
            "height": [0, 10, 10],
        }

    monkeypatch.setattr("rfp.ingestion.ocr.pytesseract.image_to_data", fake)
    with pymupdf.open(stream=make_pdf(), filetype="pdf") as document:
        text, blocks, confidence = ocr_page(
            document[0],
            ParserOptions(ocr_max_image_pixels=100000),
            "tesseract",
            SourceLocator(relative_path="a.pdf", page_number=1),
            "test",
        )
    assert text == "AB-123 20" and confidence == 85
    assert blocks[0].layout.coordinate_system == "pdf_points"
    with pytest.raises(ValueError):
        captured[0].getbbox()


def test_missing_tesseract_is_safe():
    result = parse_document(
        make_pdf(("", BODY)),
        "scan.pdf",
        BID,
        ParserOptions(),
        "nonexistent-rfp-tesseract-executable",
    )
    assert result.processing_status == "partially_succeeded"


def test_subprocess_parser_integration():
    pdf = parse_isolated(make_pdf((BODY, BODY)), "two.pdf", BID, ParserOptions())
    html = parse_isolated(b"<h1>Bid</h1><p>Model AB-123</p>", "a.html", BID, ParserOptions())
    assert pdf.processing_status == "succeeded" and pdf.page_count == 2
    assert html.processing_status == "succeeded"
