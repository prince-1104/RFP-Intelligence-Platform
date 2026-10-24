"""Field-meaning validation, citation recovery and page locators. Offline and synthetic."""

import json
import uuid

from rfp.agents.extractors import (
    CATALOG,
    critic_request,
    extraction_request,
    parse_drafts,
    steps_for,
)
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.orchestrator import run_extraction
from rfp.agents.retrieval import cited_pages, hits_from_response
from rfp.agents.schemas import (
    REQUIRED_FIELDS,
    DraftField,
    EvidenceHit,
    ExtractionConfig,
    SourceRef,
)
from rfp.agents.semantics import grounded_notes, semantic_problems
from rfp.agents.validator import validate_field
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import (
    Citation,
    EvidenceResult,
    IdentifierMatch,
    LocatorRef,
    ProfileInfo,
    QueryDiagnostics,
    RetrievalMode,
    ScoreComponents,
    SearchFilters,
    SearchResponse,
    SegmentRef,
)
from rfp.search.identifiers import extract_identifiers
from rfp.search.query import normalize_query

BID = uuid.UUID("05876a57-4ff3-52ce-a5fe-9d605ebf94b3")

PRODUCT_TABLE = (
    "Product Name Product Description Model # Qty 30 06/10/2024 SI# AB1234 Due Date mm/dd/yyyy "
    "1. SI# AB1234 Acme Book 7000 *Laptops must be AI ready* 30 06/10/2024"
)
INSTRUCTIONS = (
    "8. The Master Contractor must be an authorized reseller for Acme. The state reserves the "
    "right to request a Letter of Authorization (LOA) from the Manufacturer or Distributor. "
    "9. Delivery within 45 days of Award."
)
AFFIDAVIT = (
    "The business named above is a (check applicable box): (1) Corporation - \uf06fdomestic or "
    "\uf06fforeign; (2) Limited Liability Company - \uf06fdomestic or \uf06fforeign"
)


def _hit(evidence_id, text, file="porfp.pdf", page=1, spans=None):
    return EvidenceHit(
        evidence_id=evidence_id,
        bid_id=BID,
        text=text,
        rank=1,
        mode="hybrid_rerank",
        final_score=0.5,
        sources=[
            SourceRef(
                file=file,
                relative_path=file,
                document_type=DocumentType.rfp,
                page=page,
                page_end=page,
                bid_id=BID,
                spans=spans or [],
            )
        ],
    )


def _check(name, value, text):
    draft = DraftField(name=name, value=value, evidence_ids=["e1"], notes="")
    return validate_field(draft, [_hit("e1", text)])


def test_delivery_date_rejects_a_due_date_column_in_a_product_table():
    result = _check("Delivery Date", "06/10/2024", PRODUCT_TABLE)
    assert result.status == "rejected"
    assert any("Delivery Date cue is missing" in reason for reason in result.reasons)
    assert any("label belongs to a different field" in reason for reason in result.reasons)


def test_relative_delivery_schedule_is_a_valid_delivery_date():
    result = _check("Delivery Date", "Delivery within 45 days of Award.", INSTRUCTIONS)
    assert result.status == "accepted", result.reasons


def test_delivery_date_is_kept_when_the_passage_labels_delivery():
    text = "When do you need these delivered by? Answer: starting in September of 2024."
    assert _check("Delivery Date", "September of 2024", text).status == "accepted"
    table = "Warranty requirements and deliverables 06/10/2024"
    assert _check("Delivery Date", "06/10/2024", table).status == "rejected"


def test_mfg_rejects_business_entity_checkbox_text():
    value = "The business named above is a (check applicable box): (1) Corporation"
    result = _check("MFG for Registration", value, AFFIDAVIT)
    assert result.status == "rejected"
    assert any("checkbox" in reason for reason in result.reasons)


def test_mfg_accepts_an_authorized_reseller_requirement():
    value = "The Master Contractor must be an authorized reseller for Acme."
    assert _check("MFG for Registration", value, INSTRUCTIONS).status == "accepted"


def test_submission_type_must_name_a_channel_not_a_procurement_method():
    text = (
        "This PORFP uses the Competitive Sealed Proposals process under COMAR 21.05.03. "
        "Responses will only be accepted through eMMA, the online procurement portal."
    )
    method = _check("Bid Submission Type", "Sealed Proposals process under COMAR 21.05.03", text)
    assert method.status == "rejected"
    channel = "Responses will only be accepted through eMMA, the online procurement portal."
    assert _check("Bid Submission Type", channel, text).status == "accepted"


def test_due_date_needs_a_deadline_label_near_the_date():
    issued = "PORFP Issue Date: 05/24/2024 Place of Performance: State Office"
    assert _check("Due Date", "05/24/2024", issued).status == "rejected"
    labelled = "PROPOSAL DUE DATE and TIME: 06/10/2024 at 2:00 PM"
    assert _check("Due Date", "06/10/2024", labelled).status == "accepted"


def test_contact_needs_an_email_or_phone_and_tolerates_a_split_pdf_domain():
    text = "Agency POC Name: Pat Doe Agency POC Fax: pdoe@agency.state.xy .us 555-260-7533"
    assert _check("contact_info", "Procurement Office", text).status == "rejected"
    value = "Pat Doe, 555-260-7533, pdoe@agency.state.xy.us"
    assert _check("contact_info", value, text).status == "accepted"
    assert _check("contact_info", value, "Pat Doe pdoe@agency.state.xy 555-260-7533").status == (
        "rejected"
    )


def test_company_name_and_identifiers_reject_values_of_another_kind():
    text = "Questions to pdoe@agency.gov or 555-260-7533 by 06/01/2024. Bid AB-12 issued."
    assert _check("company_name", "pdoe@agency.gov", text).status == "rejected"
    assert _check("Bid Number", "06/01/2024", text).status == "rejected"
    assert _check("Part_no", "555-260-7533", text).status == "rejected"
    assert _check("Bid Number", "AB-12", text).status == "accepted"


def test_product_specification_needs_a_technical_term():
    text = "Laptops for staff. Intel Core 5 processor, 16 GB RAM, 14 inch display."
    assert _check("Product Specification", "Laptops for staff", text).status == "rejected"
    value = "Intel Core 5 processor, 16 GB RAM, 14 inch display"
    assert _check("Product Specification", value, text).status == "accepted"


def test_semantic_rules_only_judge_supported_values():
    assert semantic_problems("Title", "Acme Laptops", ["Acme Laptops"]) == []
    result = _check("MFG for Registration", "authorized reseller for Zeta", INSTRUCTIONS)
    assert result.reasons == ["Cited evidence does not support the value"]


def test_abstractive_summary_needs_most_words_and_every_number_from_the_evidence():
    text = (
        "The State Treasurer's Office requests proposals for 30 laptops with docks. Proposals "
        "are due 06/10/2024 through the online portal. Delivery within 45 days of award."
    )
    summary = (
        "The Treasurer's Office requests proposals for 30 laptops and docks, due 06/10/2024 "
        "via the online portal, with delivery within 45 days of award."
    )
    assert _check("Bid Summary", summary, text).status == "accepted"
    wrong_number = summary.replace("30 laptops", "40 laptops")
    assert _check("Bid Summary", wrong_number, text).status == "rejected"
    unrelated = "Vendors must carry insurance, attend training and register as minority suppliers."
    assert _check("Bid Summary", unrelated, text).status == "rejected"


def test_catalog_queries_carry_no_identifier_that_would_empty_the_search():
    for spec in CATALOG.values():
        for attempt, reasons in ((0, []), (2, ["addendum conflict"])):
            for step in steps_for(spec, attempt, reasons):
                assert extract_identifiers(normalize_query(step.query)) == [], step.query


def test_numeric_evidence_ids_map_to_the_fields_own_ids():
    reply = {
        "fields": {
            "contact_info": {"value": "Pat Doe, 555-260-7533", "evidence_ids": [5, "7", True]},
            "Bid Number": {"value": "AB-12", "evidence_ids": ["bid-number-2", 3.0]},
        }
    }
    drafts = parse_drafts(["contact_info", "Bid Number"], json.dumps(reply))
    assert drafts["contact_info"].evidence_ids == ["contact-info-5", "contact-info-7"]
    assert drafts["Bid Number"].evidence_ids == ["bid-number-2"]


def test_string_id_lists_and_object_values_are_read_not_dropped():
    reply = {
        "fields": {
            "Due Date": {"value": "06/10/2024", "evidence_ids": "[due-date-1, due-date-3]"},
            "contact_info": {
                "value": {"name": "Pat Doe", "email": "pdoe@agency.gov", "phone": None},
                "evidence_ids": ["contact-info-2"],
            },
            "Title": {"value": "None", "evidence_ids": []},
        }
    }
    drafts = parse_drafts(["Due Date", "contact_info", "Title"], json.dumps(reply))
    assert drafts["Due Date"].evidence_ids == ["due-date-1", "due-date-3"]
    assert drafts["contact_info"].value == "Pat Doe, pdoe@agency.gov"
    assert drafts["Title"].value is None


def test_mfg_may_name_only_the_manufacturer_when_the_passage_states_the_requirement():
    assert _check("MFG for Registration", "Acme", INSTRUCTIONS).status == "accepted"
    assert _check("MFG for Registration", "Corporation", AFFIDAVIT).status == "rejected"
    text = (
        "Vendors must provide demonstrated competencies and references in delivering the "
        "magnitude of devices listed. Resellers must submit factory-authorized certifications."
    )
    sentence = "Vendors must provide demonstrated competencies and references in delivering the"
    assert _check("MFG for Registration", sentence, text).status == "rejected"


def test_prompt_shows_the_part_of_a_long_passage_that_matches_the_field():
    filler = "General terms and conditions apply to every award made by the district. " * 14
    answer = "Responses must be submitted online via the electronic system and supplier portal."
    hits = {"Bid Submission Type": [_hit("bid-submission-type-1", filler + answer + filler)]}
    request = extraction_request(
        "logistics", hits, ["Bid Submission Type"], 0, {}, ExtractionConfig()
    )
    assert answer in request.user
    assert len(filler + answer) > ExtractionConfig().excerpt_chars
    draft = DraftField(
        name="Bid Submission Type",
        value="submitted online via the electronic system",
        evidence_ids=["bid-submission-type-1"],
    )
    critic = critic_request(
        "Bid Submission Type", draft, hits["Bid Submission Type"], ExtractionConfig()
    )
    assert answer in critic.user
    short = {"Bid Submission Type": [_hit("bid-submission-type-1", answer)]}
    assert (
        answer
        in extraction_request(
            "logistics", short, ["Bid Submission Type"], 0, {}, ExtractionConfig()
        ).user
    )


def test_prompt_defines_every_field():
    hits = {name: [_hit("x-1", "text")] for name in REQUIRED_FIELDS}
    for group in ("identity", "logistics", "commercial", "product", "summary"):
        names = [name for name in REQUIRED_FIELDS if CATALOG[name].group == group]
        request = extraction_request(group, hits, names, 0, {}, ExtractionConfig())
        assert request.user.count("Definition: ") == len(names)
    assert "proposal due date is not a delivery date" in request_text("Delivery Date")


def test_retry_after_unsupported_paraphrase_asks_for_verbatim_wording():
    hits = {"Term of Bid": [_hit("term-of-bid-1", "Initial Term: 3 Renewal 1: 1 Renewal 2: 1")]}
    reasons = {"Term of Bid": ["Cited evidence does not support the value"]}
    retry = extraction_request("logistics", hits, ["Term of Bid"], 1, reasons, ExtractionConfig())
    assert "word for word" in retry.user
    first = extraction_request("logistics", hits, ["Term of Bid"], 0, {}, ExtractionConfig())
    assert "word for word" not in first.user


def request_text(name):
    hits = {name: [_hit("x-1", "text")]}
    group = CATALOG[name].group
    return extraction_request(group, hits, [name], 0, {}, ExtractionConfig()).user


def test_grounded_notes_drop_invented_chronology_and_absence_claims():
    cited = ["Questions Due 06/01/2024. PROPOSAL DUE DATE 06/10/2024"]
    assert grounded_notes("Original due date was 06/01/2024.", cited) == ""
    assert grounded_notes("Not explicitly stated. Due at 2 PM.", cited) == "Due at 2 PM."
    assert grounded_notes("Closes 07/01/2024.", cited) == ""
    change = "Addendum 2 states July 9, 2024; the base document stated 27-JUN-2024."
    assert grounded_notes("Original was June 27.", cited, change) == change


def _segment(page, start, end):
    return SegmentRef(
        locator=LocatorRef(relative_path="rfp.pdf", page_number=page),
        normalized_start=start,
        normalized_end=end,
    )


def _two_page_result(text, segments, pages=(1, 2)):
    citation = Citation(
        occurrence_id=uuid.uuid4(),
        chunk_generation_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="abc",
        bid_id=BID,
        document_id=uuid.uuid4(),
        original_file_name="rfp.pdf",
        relative_path="rfp.pdf",
        document_type=DocumentType.rfp,
        chunk_type="prose",
        page_start=pages[0],
        page_end=pages[1],
        location_known=True,
        normalization_version="1.0",
        chunking_version="1.0",
        source_segments=segments,
    )
    return SearchResponse(
        query=QueryDiagnostics(original="q", normalized="q", dense_query="q", sparse_query="q"),
        mode=RetrievalMode.hybrid_rerank,
        top_k=12,
        filters=SearchFilters(bid_ids=[BID]),
        results=[
            EvidenceResult(
                rank=1,
                occurrence_id=uuid.uuid4(),
                content_id=uuid.uuid4(),
                content_hash="abc",
                text=text,
                chunk_type="prose",
                citation=citation,
                scores=ScoreComponents(final_score=0.5),
                identifier_match=IdentifierMatch(),
            )
        ],
        result_count=1,
        profile=ProfileInfo(
            embedding_profile="abc",
            collection="local",
            dense_model="fake",
            sparse_model="fake",
            fastembed_version="fake",
        ),
    )


PAGE_ONE = "Request For Proposal JA-1 Student Devices"
PAGE_TWO = "Buyer DOE, PAT Email PDOE@district.org Pre-Proposal Meeting 10-JUN-2024 14:00:00"


def test_two_page_chunk_cites_the_page_that_holds_the_value():
    text = PAGE_ONE + "\n\n" + PAGE_TWO
    segments = [_segment(1, 40, 40 + len(PAGE_ONE)), _segment(2, 0, len(PAGE_TWO))]
    hit = hits_from_response(_two_page_result(text, segments), BID, "pre-bid-meeting-")[0]
    source = hit.sources[0]
    assert (source.page, source.page_end) == (1, 2)
    assert [span.page for span in source.spans] == [1, 2]
    assert text[source.spans[1].start : source.spans[1].end] == PAGE_TWO
    assert cited_pages(source, text, "10-JUN-2024 14:00:00") == [2]
    assert cited_pages(source, text, "JA-1") == [1]
    assert cited_pages(source, text, "JA-1 Student Devices; PDOE@district.org") == [1, 2]
    assert cited_pages(source, text, "not in the passage") == [1]


def test_unverifiable_segment_map_keeps_the_chunk_start_page():
    text = PAGE_ONE + "\n\n" + PAGE_TWO
    segments = [_segment(1, 0, len(PAGE_ONE) - 3), _segment(2, 0, len(PAGE_TWO))]
    hit = hits_from_response(_two_page_result(text, segments), BID, "x-")[0]
    assert hit.sources[0].spans == []
    assert cited_pages(hit.sources[0], text, "10-JUN-2024 14:00:00") == [1]


class _Model:
    provider = "fake"
    model = "fake"

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(self.handler(request), "fake", "fake", 5, 5, 1.0)


def _ids(prompt):
    current, found = None, {}
    for line in prompt.splitlines():
        if line.startswith("Field: "):
            current = line.removeprefix("Field: ")
        elif line.startswith("[") and current:
            found.setdefault(current, []).append(line[1 : line.index("]")])
    return found


def test_cross_field_citation_and_two_page_locator_in_the_workflow():
    specs_text = "Acme Book 7000 Intel Core 5 processor, 16 GB RAM"
    two_page = PAGE_ONE + "\n\n" + PAGE_TWO
    segments = [_segment(1, 0, len(PAGE_ONE)), _segment(2, 0, len(PAGE_TWO))]

    def search(request):
        query = request.query.casefold()
        if "technical specifications" in query:
            return _two_page_result(specs_text, [_segment(3, 0, len(specs_text))], (3, 3))
        if "model number" in query or "part number" in query:
            return _two_page_result("Model # Qty see the product table", [], (1, 1))
        if "pre-bid" in query:
            return _two_page_result(two_page, segments)
        return _two_page_result("x", [], (1, 1)).model_copy(
            update={"results": [], "result_count": 0}
        )

    def handler(request):
        ids = _ids(request.user)
        fields = {}
        if "Model_no" in ids:
            spec_id = ids["Product Specification"][0]
            fields["Model_no"] = {"value": "Acme Book 7000", "evidence_ids": [spec_id]}
            fields["Part_no"] = {"value": "AB1234", "evidence_ids": ["product-specification-99"]}
        if "Pre Bid Meeting" in ids:
            fields["Pre Bid Meeting"] = {
                "value": "10-JUN-2024 14:00:00",
                "evidence_ids": ids["Pre Bid Meeting"][:1],
                "notes": "Originally scheduled for 03-JUN-2024.",
            }
        for name in ids:
            fields.setdefault(name, {"value": None, "evidence_ids": [], "notes": "Not found"})
        return json.dumps({"fields": fields})

    output, _state, events = run_extraction(
        "Bid2", BID, search, _Model(handler), ExtractionConfig(max_retries=0, concurrency=1)
    )
    model = output.fields["Model_no"]
    assert model.value == "Acme Book 7000"
    assert [(source.file, source.page) for source in model.sources] == [("rfp.pdf", 3)]
    assert output.fields["Part_no"].value is None
    part_reasons = [
        reason for event in events if event.field == "Part_no" for reason in event.reasons
    ]
    assert "Fabricated citation product-specification-99" in part_reasons
    meeting = output.fields["Pre Bid Meeting"]
    assert [source.page for source in meeting.sources] == [2]
    assert meeting.notes == "Supported by the cited passage."


def test_model_number_that_repeats_the_solicitation_number_is_retried_then_unresolved():
    price_sheet = "RFP JA-1 Price Sheet Item Product Model # Qty"

    def search(request):
        query = request.query.casefold()
        if "solicitation" in query or "model number" in query:
            return _two_page_result(price_sheet, [], (5, 5))
        return _two_page_result("x", [], (1, 1)).model_copy(
            update={"results": [], "result_count": 0}
        )

    def handler(request):
        ids = _ids(request.user)
        fields = {name: {"value": None, "evidence_ids": [], "notes": "Not found"} for name in ids}
        for name in ("Bid Number", "Model_no"):
            if name in ids:
                fields[name] = {"value": "JA-1", "evidence_ids": ids[name][:1]}
        return json.dumps({"fields": fields})

    output, _state, events = run_extraction(
        "Bid1", BID, search, _Model(handler), ExtractionConfig(max_retries=1, concurrency=1)
    )
    assert output.fields["Bid Number"].value == "JA-1"
    assert output.fields["Model_no"].value is None
    assert "repeats the solicitation number" in output.fields["Model_no"].notes
    assert any(event.field == "Model_no" and event.retry == 1 for event in events)
