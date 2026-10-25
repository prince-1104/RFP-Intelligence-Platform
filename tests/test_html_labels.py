"""Phase E: an HTML field's explicit label stays with its value through parsing, chunking
and Due Date validation. Synthetic pages only."""

import uuid

from search_fakes import chunked

from rfp.agents.schemas import DraftField, EvidenceHit, SourceRef
from rfp.agents.semantics import DUE_TIME_PROBLEM
from rfp.agents.validator import validate_field
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import parse_document
from rfp.schemas.parsed import DocumentType

BID = uuid.UUID("11111111-2222-5333-8444-555555555555")


def _field(field_id, label, value):
    return (
        f'<div class="dates field-view" id="{field_id}">'
        f'<span class="field-label">{label}</span>'
        f'<div class="field-body"><p>{value}</p></div></div>'
    )


LISTING = (
    "<h2>Dates</h2>"
    + _field("g_10", "Publication", "05/20/2031 11:15 AM EDT")
    + _field("g_11", "Closing Date", "06/10/2031 02:00 PM EDT")
    + _field("g_12", "Questions Due By", "06/01/2031 05:00 PM EDT")
    + "<h2>Office</h2><p>Office hours are 9:00 AM to 5:00 PM EST.</p>"
    + '<div id="g_13"><span class="field-label"> </span><div><p>Jane Buyer</p></div></div>'
)


def _blocks(html):
    parsed = parse_document(html.encode(), "listing.html", BID, ParserOptions())
    return parsed, [block for page in parsed.parsed_pages for block in page.blocks]


def _by_id(blocks):
    return {block.source_locator.element_id: block for block in blocks}


def _hit(text, element_id):
    return EvidenceHit(
        evidence_id="h1",
        bid_id=BID,
        text=text,
        rank=1,
        mode="hybrid_rerank",
        final_score=0.5,
        sources=[
            SourceRef(
                file="listing.html",
                relative_path="listing.html",
                document_type=DocumentType.bid_page,
                html_element_id=element_id,
                bid_id=BID,
            )
        ],
    )


def _due(value, text, element_id):
    draft = DraftField(name="Due Date", value=value, evidence_ids=["h1"])
    return validate_field(draft, [_hit(text, element_id)])


def test_deadline_with_explicit_label_keeps_date_time_and_zone_together():
    _, blocks = _blocks(LISTING)
    closing = _by_id(blocks)["g_11"]
    assert closing.text == "Closing Date: 06/10/2031 02:00 PM EDT"
    result = _due("06/10/2031 02:00 PM EDT", closing.text, "g_11")
    assert result.passed, result.reasons
    dropped = _due("06/10/2031", closing.text, "g_11")
    assert not dropped.passed and DUE_TIME_PROBLEM in dropped.reasons


def test_date_only_deadline_stays_date_only():
    _, blocks = _blocks(_field("g_20", "Closing Date", "06/10/2031"))
    closing = _by_id(blocks)["g_20"]
    assert closing.text == "Closing Date: 06/10/2031"
    assert _due("06/10/2031", closing.text, "g_20").passed
    invented = _due("06/10/2031 02:00 PM EDT", closing.text, "g_20")
    assert not invented.passed


def test_unrelated_times_are_not_labelled_or_attached():
    _, blocks = _blocks(LISTING)
    office = next(block for block in blocks if "Office hours" in block.text)
    assert office.text == "Office hours are 9:00 AM to 5:00 PM EST."
    assert _by_id(blocks)["g_13"].text == "Jane Buyer"
    text = _by_id(blocks)["g_11"].text.replace(" 02:00 PM EDT", "") + "\n" + office.text
    borrowed = _due("06/10/2031 9:00 AM EST", text, "g_11")
    assert not borrowed.passed


def test_multiple_deadlines_keep_their_own_labels():
    _, blocks = _blocks(LISTING)
    found = _by_id(blocks)
    assert found["g_10"].text.startswith("Publication: 05/20/2031")
    assert found["g_12"].text == "Questions Due By: 06/01/2031 05:00 PM EDT"
    questions = _due("06/01/2031 05:00 PM EDT", found["g_12"].text, "g_12")
    assert not questions.passed
    assert any("vendor questions" in reason for reason in questions.reasons)


def test_label_provenance_survives_chunking_and_citation_checks():
    parsed, blocks = _blocks(LISTING)
    closing = _by_id(blocks)["g_11"]
    assert closing.source_locator.element_id == "g_11"
    assert closing.source_locator.page_number is None
    chunks = chunked(parsed)
    text = "\n".join(content.text for content in chunks.contents)
    assert "Closing Date: 06/10/2031 02:00 PM EDT" in text
    located = [
        segment
        for occurrence in chunks.occurrences
        for segment in occurrence.source_segments
        if segment.source_locator.element_id == "g_11"
    ]
    assert located
    paged = DraftField(name="Due Date", value="06/10/2031 02:00 PM EDT", evidence_ids=["h1"])
    bad = _hit(closing.text, "g_11")
    bad.sources[0].page = 1
    assert "HTML citation invented a page number" in validate_field(paged, [bad]).reasons


def test_definition_lists_and_ambiguous_paragraphs():
    html = (
        "<dl><dt>Bid Opening</dt><dd><p>07/01/2031 10:00 AM CDT</p></dd>"
        "<dt>Award</dt><dd><p>08/01/2031</p></dd></dl>"
        '<div><span class="field-label">Notes</span><div><p>First note.</p>'
        "<p>Second note 3:00 PM.</p></div></div>"
    )
    _, blocks = _blocks(html)
    texts = [block.text for block in blocks]
    assert "Bid Opening: 07/01/2031 10:00 AM CDT" in texts
    assert "Award: 08/01/2031" in texts
    assert "Notes: First note." in texts
    assert "Second note 3:00 PM." in texts
