"""Table rows keep each item with its own quantity; requirement fields keep their sources (Phase F).

Synthetic PDFs with ruled tables are written with PyMuPDF and parsed by the production parser.
In them, as in real price schedules, the text block of a quantity cell starts above the block of
its description, so the page's text blocks read "50,000 Each / 1.01 Alpha ... / 5,000 Each /
1.02 Beta ...": read in order, each quantity seems to follow the previous row. The last tests
read the local assessment documents when they exist and skip otherwise.
"""

import uuid
from pathlib import Path

import pymupdf
import pytest
from search_fakes import chunked, generation, html_document, services

from rfp.agents.extractors import compact
from rfp.agents.quotes import verbatim
from rfp.agents.retrieval import cited_pages, retrieve
from rfp.agents.revisions import reconcile
from rfp.agents.schemas import REQUIRED_FIELDS, BidExtraction, DraftField
from rfp.agents.semantics import (
    UNNAMED_DEVICE_PROBLEM,
    document_problems,
    only_incomplete,
    question_only_problems,
    semantic_problems,
    specification_scope_problems,
)
from rfp.agents.validator import validate_field
from rfp.chunking.engine import _keep_lead_in, chunk_document
from rfp.cli import local_settings
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import parse_document
from rfp.schemas.chunks import ChunkConfig, ChunkingResult
from rfp.schemas.parsed import DocumentType, Layout, ParsedDocument, ParsedTable
from rfp.schemas.search import RetrievalMode

BID = uuid.UUID("8d3f6b2a-1c4e-4f7a-9b0d-2e5c7a9f1b36")
ESTIMATED = {"token_counter": "utf8_bytes_v1", "tokenizer_identifier": "utf8-bytes-div4-v1"}
ALIGNED = ChunkConfig(**ESTIMATED, page_furniture="repeated_v1", table_prose="layout_v1")
EXACT = ChunkConfig(**ESTIMATED, page_furniture="repeated_v1")
ROWS = [
    ["Line Item", "Target Quantity", "Unit"],
    ["1.01 Alpha Student Laptop", "50,000", "Each"],
    ["1.02 Beta Student Laptop", "5,000", "Each"],
    ["2 DESKTOPS - FOR EVALUATION", "", ""],
    ["2.01 Gamma Staff Desktop", "700", "Each"],
]
COLUMNS = [60, 300, 420, 500]
TOP, HEIGHT = 120, 44
NOTE = "Prices are firm for ninety days."
HEADER = "Northwind Unified School District RFP 77-120 Computing Devices"
ROOT = Path(__file__).resolve().parents[1]
BID1_PDF = (
    ROOT
    / "data"
    / "assessment"
    / "Bid1"
    / "JA-207652 Student and Staff Computing Devices FINAL.pdf"
)


def table_page(page, rows=ROWS, top=TOP):
    """Draw a ruled table whose quantity text starts above its two-line description."""
    bottom = top + HEIGHT * len(rows)
    for x in COLUMNS:
        page.draw_line((x, top), (x, bottom))
    for index in range(len(rows) + 1):
        page.draw_line((COLUMNS[0], top + HEIGHT * index), (COLUMNS[-1], top + HEIGHT * index))
    for index, (name, *rest) in enumerate(rows):
        y = top + HEIGHT * index
        words = name.split()
        page.insert_text((COLUMNS[0] + 4, y + 18), " ".join(words[:2]), fontsize=9)
        page.insert_text((COLUMNS[0] + 4, y + 30), " ".join(words[2:]), fontsize=9)
        for column, cell in enumerate(rest, 1):
            if cell:
                page.insert_text((COLUMNS[column] + 4, y + 12), cell, fontsize=9)
    page.insert_text((60, bottom + 40), NOTE, fontsize=10)


def pdf(pages: int = 1, header: str | None = None, rows=ROWS) -> bytes:
    with pymupdf.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page()
            if header:
                page.insert_text((60, 36), header, fontsize=9)
                page.insert_text((60, 812), f"Page {number} of {pages}", fontsize=9)
            if number == pages:
                table_page(page, rows)
            else:
                page.insert_text((60, 150), f"Section {number} describes delivery terms.")
        return document.tobytes()


def parsed(data: bytes, path: str = "rfp/pricing.pdf") -> ParsedDocument:
    return parse_document(data, path, BID, ParserOptions(ocr_min_text_chars=0, ocr_min_words=0))


def table_lines(result: ChunkingResult) -> list[str]:
    return [
        compact(line)
        for o in result.occurrences
        if o.chunk_type == "table_rows"
        for line in o.normalized_text.split("\n")
    ]


def prose(result: ChunkingResult) -> str:
    return "\n".join(o.normalized_text for o in result.occurrences if o.chunk_type != "table_rows")


def row_with(result: ChunkingResult, item: str) -> str:
    (line,) = [line for line in table_lines(result) if item in " ".join(line.split())]
    return " ".join(line.split())


def test_the_fixture_reproduces_the_misleading_block_order():
    page = parsed(pdf()).parsed_pages[0]
    texts = [" ".join(block.text.split()) for block in page.blocks]
    assert texts.index("50,000 Each") < texts.index("1.01 Alpha Student Laptop")
    assert texts.index("5,000 Each") == texts.index("1.01 Alpha Student Laptop") + 1
    (table,) = page.tables
    assert table.layout is not None and table.rows[2][1] == "5,000"


# 1. A description keeps its own quantity.
def test_description_keeps_its_quantity():
    result = chunked(parsed(pdf()), ALIGNED)
    alpha = row_with(result, "1.01 Alpha Student Laptop")
    assert alpha == "1.01 Alpha Student Laptop | 50,000 | Each"
    assert row_with(result, "2.01 Gamma Staff Desktop") == "2.01 Gamma Staff Desktop | 700 | Each"


def in_order(text: str, parts: list[str]) -> bool:
    flat, position = " ".join(text.split()), 0
    for part in parts:
        position = flat.find(part, position)
        if position < 0:
            return False
        position += len(part)
    return True


# 2. Flattening and chunking never present the block-order copy that shifts rows.
def test_chunking_does_not_shift_rows():
    doc = parsed(pdf())
    aligned, exact = chunked(doc, ALIGNED), chunked(doc, EXACT)
    rows = ["1.01 Alpha Student Laptop", "50,000 Each", "1.02 Beta Student Laptop", "5,000 Each"]
    rows += ["2.01 Gamma Staff Desktop", "700 Each"]
    assert in_order(prose(aligned), rows)
    assert not in_order(prose(exact), rows) and "table_prose_overlap_uncertain" in exact.warnings
    assert NOTE in prose(aligned)
    assert table_lines(aligned) == table_lines(exact)


# 3. Adjacent rows with different quantities stay apart.
def test_adjacent_rows_with_different_quantities():
    result = chunked(parsed(pdf()), ALIGNED)
    alpha = row_with(result, "1.01 Alpha Student Laptop")
    beta = row_with(result, "1.02 Beta Student Laptop")
    assert "50,000" in alpha and "5,000" not in alpha.replace("50,000", "")
    assert "| 5,000 |" in beta and "50,000" not in beta and "700" not in beta


# 4. A row without a quantity stays without one, and a null field stays "Not found".
def test_missing_quantity_stays_missing():
    result = chunked(parsed(pdf()), ALIGNED)
    assert row_with(result, "2 DESKTOPS - FOR EVALUATION") == "2 DESKTOPS - FOR EVALUATION"
    draft = DraftField(name="Product", value=None, notes="Not found in documents")
    assert validate_field(draft, []).status == "not_found"


SPECS = (
    '["Staff Devices\\nTier 1 Staff Laptop Minimum Requirements\\n• Processor: Intel i5\\n'
    "• Memory: Minimum 16GB RAM\\n• Battery Life: Minimum 10 Hours\\n• Warranty: 3-year minimum\\n"
    "Tier 1 Staff Desktop Minimum Requirements\\n• Processor: Intel i7\\n"
    '• Display: Minimum 23.8\\" diagonal screen\\n• Memory: Minimum 32GB RAM\\n'
    '• Warranty: 3-year minimum\\nMonitors\\nShared accessories are listed below.", null]'
)


# 5. A battery life is never borrowed from another device category.
def test_no_battery_life_is_borrowed_from_another_category():
    value = (
        "Tier 1 Staff Laptop: Intel i5; Minimum 16GB RAM; Minimum 10 hours battery life; "
        "Tier 1 Staff Desktop: Intel i7; Minimum 32GB RAM; Minimum 10 hours battery life"
    )
    problems = specification_scope_problems(value, [SPECS])
    assert problems == [
        "'Minimum 10 hours battery life' is not stated for Tier 1 Staff Desktop in the cited "
        "specifications; keep each attribute with the device it is listed under"
    ]
    assert semantic_problems("Product Specification", value, [SPECS])[-1] == problems[0]


# 6. Laptop and desktop specifications stay separate.
def test_laptop_and_desktop_specifications_stay_separate():
    correct = (
        "Tier 1 Staff Laptop: Intel i5; Minimum 16GB RAM; Minimum 10 hours battery life; "
        'Warranty: 3-year minimum; Tier 1 Staff Desktop: Intel i7; Minimum 23.8" diagonal '
        "screen; Minimum 32GB RAM; Warranty: 3-year minimum"
    )
    assert specification_scope_problems(correct, [SPECS]) == []
    swapped = "Tier 1 Staff Laptop: Minimum 32GB RAM; Tier 1 Staff Desktop: Minimum 16GB RAM"
    assert len(specification_scope_problems(swapped, [SPECS])) == 2


def test_minimums_of_several_devices_must_name_their_devices():
    flat = "Intel i5; Minimum 16GB RAM; Minimum 10 hours battery life; Intel i7; Minimum 32GB RAM"
    (problem,) = semantic_problems("Product Specification", flat, [SPECS])
    assert problem == UNNAMED_DEVICE_PROBLEM and only_incomplete([problem])
    one_device = "Intel i5; Minimum 16GB RAM; Minimum 10 hours battery life"
    assert semantic_problems("Product Specification", one_device, [SPECS]) == []


def test_a_device_section_that_may_continue_elsewhere_is_not_judged():
    open_section = '["Tier 3 Student Laptop Minimum Requirements\\n• Processor: Intel i5", null]'
    value = "Tier 3 Student Laptop: Intel i5; Minimum 10 hours battery life"
    assert specification_scope_problems(value, [open_section]) == []


ADDENDUM = (
    "33. Pricing: Do you require any Imaging on your devices? Every laptop has an OS installed.\n"
    "Answer:\nFor Windows devices, autopilot is required. For Chromebooks, enrollment in "
    "Google Management is required.\n"
    "34. Deployment: Is white glove delivery to each campus required?\nAnswer:\nYes.\n"
    "35. Do you require asset tagging and etching?\nAnswer:\nNo, only etching is required.\n"
    "36. Should devices be decaled and inventoried?\nAnswer:\nBoth are required."
)


# 7. A vendor question alone does not create a requirement.
def test_vendor_question_alone_does_not_create_a_requirement():
    value = "Device imaging is required."
    problems = question_only_problems("Installation", value, [ADDENDUM])
    assert problems and "'imaging'" in problems[0]
    asked = "12. Is device imaging required for deployment?\nAnswer:\nDevices ship with the OS."
    draft = DraftField(name="Installation", value=value, evidence_ids=["a"], notes="")
    result = validate_field(draft, [_hit("a", asked)])
    assert result.status == "rejected"
    assert any("vendor question" in reason for reason in result.reasons), result.reasons
    assert question_only_problems("Installation", "Asset tagging is required.", [ADDENDUM])


# 8. An authoritative addendum answer can establish a requirement.
def test_addendum_answer_can_establish_a_requirement():
    for value in (
        "Autopilot is required for Windows devices.",
        "White glove delivery to each campus.",
        "Etching is required.",
        "Decaled and inventoried.",
    ):
        assert question_only_problems("Installation", value, [ADDENDUM]) == [], value
    solicitation = "Deployment services include device imaging and setup."
    value = "Device imaging is required."
    assert question_only_problems("Installation", value, [ADDENDUM, solicitation]) == []
    assert question_only_problems("Title", "Imaging", [ADDENDUM]) == []


def _hit(evidence_id, text, page=1):
    from rfp.agents.schemas import EvidenceHit, SourceRef

    return EvidenceHit(
        evidence_id=evidence_id,
        bid_id=BID,
        text=text,
        rank=1,
        mode="hybrid_rerank",
        final_score=0.5,
        sources=[
            SourceRef(
                file="addendum.pdf",
                relative_path="addendum.pdf",
                document_type=DocumentType.addendum,
                page=page,
                page_end=page,
                bid_id=BID,
            )
        ],
    )


def _indexed(settings, results):
    indexer, search, _ = services(settings)
    report = indexer.index([generation(result) for result in results])
    assert report.succeeded
    return indexer, search.search


# 9. Citations point to the table's page and to the row range that holds the item.
def test_citations_point_to_the_correct_page_and_row(settings):
    doc = parsed(pdf(pages=3, header=HEADER))
    result = chunked(doc, ALIGNED)
    (table,) = [o for o in result.occurrences if o.chunk_type == "table_rows"]
    assert table.page_start == table.page_end == 3
    assert table.table is not None
    assert table.table.table_id == doc.parsed_pages[2].tables[0].table_id
    (beta,) = [
        number
        for number, row in enumerate(table.table.rows, table.table.row_start)
        if "Beta" in (row[0] or "")
    ]
    assert table.table.rows[beta - table.table.row_start][1:] == ["5,000", "Each"]
    assert doc.parsed_pages[2].tables[0].rows[beta - 1][1] == "5,000"
    _, search = _indexed(settings, [result])
    hits, _ = retrieve(
        search,
        BID,
        "Beta Student Laptop quantity",
        mode=RetrievalMode.hybrid,
        top_k=3,
        doc_types=[],
        addendum_numbers=[],
        evidence_prefix="E",
    )
    top = next(hit for hit in hits if "Beta" in hit.text)
    assert top.sources[0].page == 3
    (page,) = cited_pages(top.sources[0], top.text, "5,000")
    assert page == 3 and verbatim("5,000", doc.parsed_pages[2].text) is not None


# 10. Header and footer cleanup still works on pages with table extraction.
def test_header_footer_cleanup_works_with_tables():
    doc = parsed(pdf(pages=4, header=HEADER))
    result = chunked(doc, ALIGNED)
    text = "\n".join(o.normalized_text for o in result.occurrences)
    assert text.count(HEADER) == 1
    assert "Page 3 of 4" not in text
    assert row_with(result, "1.02 Beta Student Laptop") == "1.02 Beta Student Laptop | 5,000 | Each"
    assert NOTE in prose(result)


# 11. Addendum reconciliation still finds the answer, not the question, and its page.
def test_addendum_reconciliation_is_unchanged(settings):
    with pymupdf.open() as document:
        first = document.new_page()
        first.insert_text((60, 150), "1. Is a one-year warranty acceptable?")
        second = document.new_page()
        second.insert_text((60, 150), "2. What warranty applies?")
        second.insert_text((60, 190), "Answer: The warranty period is changed to five years.")
        data = document.tobytes()
    addendum = parsed(data, "addenda/Addendum 2.pdf")
    assert addendum.doc_type == DocumentType.addendum and addendum.addendum_number == 2
    _, search = _indexed(settings, [chunked(addendum, ALIGNED)])
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
    assert revision.sources[0].page == 2


# 12. Questions over a bid return only that bid's evidence; another bid's table stays apart.
def test_single_and_cross_bid_retrieval_keep_bids_apart(settings):
    other = uuid.UUID("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
    first = chunked(parsed(pdf()), ALIGNED)
    second_doc = parse_document(
        pdf(rows=[ROWS[0], ["1.01 Alpha Student Laptop", "12", "Each"]]),
        "rfp/pricing.pdf",
        other,
        ParserOptions(ocr_min_text_chars=0, ocr_min_words=0),
    )
    second = chunked(second_doc, ALIGNED)
    _, search = _indexed(settings, [first, second])
    for bid, quantity in ((BID, "50,000"), (other, "12")):
        hits, _ = retrieve(
            search,
            bid,
            "Alpha Student Laptop quantity",
            mode=RetrievalMode.hybrid,
            top_k=3,
            doc_types=[],
            addendum_numbers=[],
            evidence_prefix="E",
        )
        assert hits and all(hit.bid_id == bid for hit in hits)
        top = next(hit for hit in hits if "Alpha" in hit.text)
        assert quantity in compact(top.text)


# 13. The 20-field JSON contract is unchanged.
def test_twenty_field_contract_is_unchanged():
    assert REQUIRED_FIELDS == (
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
    assert set(BidExtraction.model_json_schema()["properties"]) == {
        "bid_id",
        "fields",
        "addendum_changes",
        "validation",
    }


# 14. Repeated ingestion is deterministic and indexes no duplicate points.
def test_repeated_ingestion_is_deterministic(settings):
    data = pdf(pages=2, header=HEADER)
    first, second = chunked(parsed(data), ALIGNED), chunked(parsed(data), ALIGNED)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    indexer, _ = _indexed(settings, [first])
    assert indexer.index([generation(second)]).as_dict()["unchanged"] == 1
    manager = indexer.collection()
    assert indexer.client.count(manager.name, exact=True).count == len(first.occurrences)


def test_settings_enable_alignment_and_old_configurations_keep_their_hash():
    assert local_settings().chunking.table_prose == "layout_v1"
    legacy = ChunkConfig(**ESTIMATED).model_dump(mode="json")
    explicit = ChunkConfig(**ESTIMATED, table_prose="exact")
    assert explicit.fingerprint == ChunkConfig.model_validate(legacy).fingerprint
    assert ALIGNED.fingerprint != EXACT.fingerprint


def test_a_table_whose_text_reads_in_cell_order_is_chunked_as_before():
    form = [
        ["Delivery terms", "Within 45 days of award, unless the order states otherwise."],
        ["Warranty terms", "Three years"],
    ]
    with pymupdf.open() as document:
        page = document.new_page()
        for x in (60, 200, 500):
            page.draw_line((x, TOP), (x, TOP + HEIGHT * len(form)))
        for index in range(len(form) + 1):
            page.draw_line((60, TOP + HEIGHT * index), (500, TOP + HEIGHT * index))
        for index, (label, value) in enumerate(form):
            page.insert_text((64, TOP + HEIGHT * index + 16), label, fontsize=9)
            page.insert_text((204, TOP + HEIGHT * index + 16), value, fontsize=9)
        doc = parsed(document.tobytes())
    assert doc.parsed_pages[0].tables[0].cell_boxes
    aligned, exact = chunk_document(doc, ALIGNED), chunk_document(doc, EXACT)
    assert [o.normalized_text for o in aligned.occurrences] == [
        o.normalized_text for o in exact.occurrences
    ]
    assert "Within 45 days of award" in " ".join(table_lines(aligned))


def test_text_inside_a_table_that_its_cells_lack_is_kept():
    doc = parsed(pdf())
    page = doc.parsed_pages[0]
    page.blocks[1] = page.blocks[1].model_copy(update={"text": "50,000 Each subject to approval"})
    result = chunk_document(doc, ALIGNED)
    assert "subject to approval" in prose(result)
    assert "table_prose_overlap_uncertain" in result.warnings


def test_single_column_boxes_and_html_tables_are_unchanged():
    doc = parsed(pdf())
    page = doc.parsed_pages[0]
    box = ParsedTable(
        table_id="box",
        table_index=2,
        rows=[[NOTE]],
        source_locator=page.tables[0].source_locator,
        extraction_method="pdfplumber",
        layout=Layout(bbox=(55.0, 350.0, 520.0, 400.0)),
    )
    page.tables[:] = [box]
    assert [o.normalized_text for o in chunk_document(doc, ALIGNED).occurrences] == [
        o.normalized_text for o in chunk_document(doc, EXACT).occurrences
    ]
    html = (
        "<table><tr><th>Item</th><th>Qty</th></tr><tr><td>Alpha laptop</td><td>40</td></tr></table>"
    )
    html_doc = html_document(BID, "notice.html", html)
    aligned, exact = chunked(html_doc, ALIGNED), chunked(html_doc, EXACT)
    assert [o.normalized_text for o in aligned.occurrences] == [
        o.normalized_text for o in exact.occurrences
    ]


LABELLED = [
    ["Line Item", "Target Quantity", "Unit"],
    ["1.01 Alpha Student Laptop", "50,000", "Each"],
    ["Staff Devices", "Quantity", "Unit"],
    ["2.01 Gamma Staff Desktop", "700", "Each"],
]


def table_chunks(result: ChunkingResult) -> list[list[str]]:
    return [
        [" ".join(line.split()) for line in o.normalized_text.split("\n")]
        for o in result.occurrences
        if o.chunk_type == "table_rows"
    ]


def test_a_label_row_stays_with_the_numeric_rows_it_introduces():
    doc = parsed(pdf(rows=LABELLED))
    limited = ALIGNED.model_copy(update={"table_row_limit": 3})
    chunks = table_chunks(chunk_document(doc, limited))
    (gamma,) = [chunk for chunk in chunks if any("Gamma" in line for line in chunk)]
    assert any("Staff Devices" in line and "Quantity" in line for line in gamma)
    assert all(any("Alpha" in line for line in chunk) for chunk in chunks if chunk != gamma)
    rows = [compact(line) for chunk in chunks for line in chunk]
    assert len(rows) == len(set(rows)) == len(LABELLED)


def test_lead_in_moves_only_short_label_rows_and_keeps_one_row():
    prose_row = ["AWARD STATEMENT\nThis proposal may be awarded to one or more vendors.", None]
    label = ["Product Name", "Qty"]
    numeric = ["1. Laptop", "30"]
    assert _keep_lead_in([numeric, label, numeric], 0, 2) == 1
    assert _keep_lead_in([numeric, prose_row, numeric], 0, 2) == 2
    assert _keep_lead_in([label, numeric], 0, 1) == 1
    assert _keep_lead_in([numeric, label, label], 0, 2) == 2
    assert _keep_lead_in([numeric, label], 0, 2) == 2


def test_items_of_a_multi_line_table_cell_are_judged_separately():
    row = (
        '["Security Requirements (if applicable):", "1. The vendor must submit a Mercury '
        "Affidavit with the quote.\\n2. If the vendor is a reseller, a Letter of Authorization "
        'from the manufacturer is required."]'
    )
    assert document_problems("Mercury Affidavit", [row]) == []
    (problem,) = document_problems("Letter of Authorization", [row])
    assert "condition" in problem


@pytest.mark.skipif(not BID1_PDF.exists(), reason="local assessment documents are not present")
def test_real_price_schedule_rows_keep_their_quantities():
    """In the real bid, each priced row's item and quantity share one table-chunk row, and
    the page's prose reads each quantity after its own line item, before the next one."""
    doc = parse_document(
        BID1_PDF.read_bytes(),
        BID1_PDF.name,
        BID,
        ParserOptions(ocr_min_text_chars=0, ocr_min_words=0),
    )
    result = chunk_document(doc, ALIGNED)
    lines = {" ".join(line.split()) for line in table_lines(result)}
    priced = 0
    for page in doc.parsed_pages:
        for table in page.tables:
            head = [" ".join((cell or "").split()) for cell in table.rows[0]] if table.rows else []
            if "Target Quantity" not in head:
                continue
            column = head.index("Target Quantity")
            items = []
            for row in table.rows[1:]:
                if row[0] and row[column]:
                    cells = [" ".join(cell.split()) for cell in row if cell]
                    assert " | ".join(cells) in lines, row
                    items.append((row[0].split()[0], row[column]))
                    priced += 1
            page_prose = " ".join(
                o.normalized_text
                for o in result.occurrences
                if o.chunk_type != "table_rows" and o.page_start == page.page_number
            )
            flat = " ".join(page_prose.split())
            for (code, quantity), (after, _) in zip(items, items[1:], strict=False):
                start = flat.index(code)
                between = flat[start : flat.index(after, start)]
                assert quantity in between.split(), (page.page_number, code)
    assert priced >= 20
