"""Phase D: completeness, document requirements and evidence-based confidence.

Offline and synthetic: search responses are constructed and model replies are fakes, so
these tests prove deterministic behaviour, not live-model accuracy.
"""

import json
import re
import uuid

from rfp.agents.confidence import Signals, band, score
from rfp.agents.extractors import (
    CATALOG,
    LIST_INSTRUCTION,
    compact,
    extraction_request,
    parse_drafts,
)
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.orchestrator import run_extraction
from rfp.agents.reconciliation import reconcile_field
from rfp.agents.schemas import (
    REQUIRED_FIELDS,
    BidExtraction,
    DraftField,
    EvidenceHit,
    ExtractionConfig,
    SourceRef,
)
from rfp.agents.semantics import (
    DUE_TIME_PROBLEM,
    DUE_ZONE_PROBLEM,
    WARRANTY_PROBLEM,
    document_problems,
    only_incomplete,
    semantic_problems,
)
from rfp.agents.validator import support_kind, validate_field
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import (
    Citation,
    EvidenceResult,
    IdentifierMatch,
    ProfileInfo,
    QueryDiagnostics,
    RetrievalMode,
    ScoreComponents,
    SearchFilters,
    SearchResponse,
)

BID = uuid.UUID("11111111-2222-5333-8444-555555555555")
OTHER = uuid.UUID("66666666-7777-5888-9999-000000000000")


def _hit(evidence_id, text, page=1, doc=DocumentType.rfp, file="rfp.pdf", addendum=None, bid=BID):
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
                document_type=doc,
                addendum_number=addendum,
                page=page,
                bid_id=bid,
            )
        ],
    )


def _check(name, value, *texts):
    hits = [_hit(f"e{index}", text, page=index + 1) for index, text in enumerate(texts)]
    draft = DraftField(name=name, value=value, evidence_ids=[hit.evidence_id for hit in hits])
    return validate_field(draft, hits)


def _result(text, page=1, doc=DocumentType.rfp, file="rfp.pdf", addendum=None, bid=BID):
    citation = Citation(
        occurrence_id=uuid.uuid4(),
        chunk_generation_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="abc",
        bid_id=bid,
        document_id=uuid.uuid4(),
        original_file_name=file,
        relative_path=file,
        document_type=doc,
        addendum_number=addendum,
        chunk_type="prose",
        page_start=page,
        page_end=page,
        location_known=True,
        normalization_version="1.0",
        chunking_version="1.0",
    )
    return EvidenceResult(
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


def _search(corpus):
    """A fake search: results whose keywords occur in the query, honouring doc-type filters."""

    def search(request):
        assert request.filters.bid_ids == [BID]
        query = request.query.casefold()
        types = set(request.filters.doc_types)
        results = [
            result
            for keywords, result in corpus
            if any(word in query for word in keywords)
            and (not types or result.citation.document_type in types)
        ]
        return SearchResponse(
            query=QueryDiagnostics(
                original=request.query,
                normalized=query,
                dense_query=query,
                sparse_query=query,
            ),
            mode=RetrievalMode.hybrid_rerank,
            top_k=12,
            filters=SearchFilters(bid_ids=[BID]),
            results=results,
            result_count=len(results),
            profile=ProfileInfo(
                embedding_profile="abc",
                collection="local",
                dense_model="fake",
                sparse_model="fake",
                fastembed_version="fake",
            ),
        )

    return search


def _ids_with(prompt: str, needle: str) -> list[str]:
    """Evidence ids whose passage (or validated-value line) contains the needle."""
    found: list[str] = []
    last = None
    for line in prompt.splitlines():
        match = re.match(r"\[([^\]]+)\]", line)
        if match:
            last = match.group(1)
            if needle.casefold() in line.casefold():
                found.append(last)
        elif last and needle.casefold() in line.casefold():
            found.append(last)
    return list(dict.fromkeys(found))


class FakeModel:
    provider = "fake"
    model = "fake"

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        fields = {}
        for name, answer in self.answers.items():
            if f"Field: {name}\n" not in request.user:
                continue
            value, needles = answer(request) if callable(answer) else answer
            ids = [item for needle in needles for item in _ids_with(request.user, needle)[:1]]
            fields[name] = {"value": value, "evidence_ids": ids, "notes": "From the evidence."}
        return ModelResponse(json.dumps({"fields": fields}), "fake", "fake", 10, 5, 1.0)


# 1, 2: due date keeps its stated time and zone and never invents one.


def test_due_date_keeps_date_time_and_zone_when_stated():
    text = "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST."
    assert _check("Due Date", "July 9, 2024 at 2:00 PM CST", text).status == "accepted"
    date_only = _check("Due Date", "July 9, 2024", text)
    assert date_only.reasons == [DUE_TIME_PROBLEM]
    no_zone = _check("Due Date", "July 9, 2024 at 2:00 PM", text)
    assert no_zone.reasons == [DUE_ZONE_PROBLEM]
    assert only_incomplete(date_only.reasons) and only_incomplete(no_zone.reasons)
    listing = "Closing Date 06/10/2024 02:00 PM EDT"
    assert _check("Due Date", "06/10/2024", listing).reasons == [DUE_TIME_PROBLEM]
    assert _check("Due Date", "06/10/2024 02:00 PM EDT", listing).status == "accepted"


def test_question_deadline_is_not_the_response_due_date():
    text = (
        "Questions Due (Closing) Date and Time: 06/01/2024 at 2:00 PM EDT. "
        "PROPOSAL DUE DATE and TIME: 06/10/2024"
    )
    questions = _check("Due Date", "06/01/2024 at 2:00 PM EDT", text)
    assert "Due Date is the deadline for vendor questions, not for responses" in questions.reasons
    assert not only_incomplete(questions.reasons)
    assert _check("Due Date", "06/10/2024", text).status == "accepted"
    sentence = "All questions must be sent by email. Proposals are due 06/10/2024"
    assert _check("Due Date", "06/10/2024", sentence).status == "accepted"


def test_missing_time_zone_stays_missing():
    text = "Proposals are due 06/10/2024 at 2:00 PM. Office hours are 9 to 5 EDT."
    assert _check("Due Date", "06/10/2024 at 2:00 PM", text).status == "accepted"
    invented = _check("Due Date", "06/10/2024 at 2:00 PM EDT", text)
    assert invented.status == "rejected"
    assert "Due Date states a time zone that is not printed with that date" in invented.reasons
    assert not only_incomplete(invented.reasons)
    assert _check("Due Date", "06/10/2024", "Proposal due date: 06/10/2024").status == "accepted"


# 3, 12: addendum deadlines reconcile; conflicting addenda stay unresolved and score low.


def _due_date_corpus(*addenda):
    rfp = _result("Solicitation Due 27-JUN-2024 14:00:00", page=2)
    title = _result("Request for Proposal: Student Computing Devices", page=1)
    corpus = [(("title",), title), (("due", "deadline", "solicitation"), rfp)]
    for number, text in addenda:
        result = _result(
            text, doc=DocumentType.addendum, file=f"addendum-{number}.pdf", addendum=number
        )
        corpus.append((("due", "deadline", "addendum", "revised"), result))
    return corpus


def test_original_and_addendum_deadlines_reconcile_with_full_time():
    corpus = _due_date_corpus(
        (2, "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST")
    )

    def due(request):
        if "Previous rejection" in request.user:
            return "July 9, 2024 at 2:00 PM CST", ["July 9, 2024"]
        return "27-JUN-2024 14:00:00", ["27-JUN-2024"]

    model = FakeModel({"Due Date": due, "Title": ("Student Computing Devices", ["Student"])})
    output, state, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    field = output.fields["Due Date"]
    assert field.value == "July 9, 2024 at 2:00 PM CST"
    assert field.sources[0].file == "addendum-2.pdf"
    assert output.addendum_changes[0].original_value.startswith("27-JUN-2024")
    assert not output.addendum_changes[0].conflict
    assert state.fields["Due Date"].retry_count == 1
    assert 0.5 <= field.confidence < output.fields["Title"].confidence


def test_conflicting_revisions_remain_unresolved_with_low_confidence():
    corpus = _due_date_corpus(
        (1, "The new due date for this RFP will be July 1, 2024 at 2:00 PM CST"),
        (2, "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST"),
    )
    model = FakeModel({"Due Date": ("July 9, 2024 at 2:00 PM CST", ["July 9, 2024"])})
    output, state, events = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    field = output.fields["Due Date"]
    assert field.value is None and state.fields["Due Date"].status == "unresolved"
    assert any(change.conflict for change in output.addendum_changes)
    assert field.confidence <= 0.15
    scored = [e for e in events if e.agent == "confidence" and e.field == "Due Date"]
    assert scored and "conflict=True" in scored[0].reasons and "(low)" in scored[0].decision


# 4, 5: device-specific warranties stay separate; the OEM requirement is kept.

SPECS = (
    "Student Chromebook Minimum Requirements\n• RAM: 8GB\n• Warranty: 1-year minimum\n"
    "Staff Windows Laptop Minimum Requirements\n• RAM: 16GB\n• Warranty: 3-year minimum"
)


def test_device_specific_warranties_are_not_collapsed():
    collapsed = _check("Product Specification", "RAM: 16GB; Warranty: 3-year minimum", SPECS)
    assert collapsed.reasons == [WARRANTY_PROBLEM] and only_incomplete(collapsed.reasons)
    separate = (
        "Student Chromebook: RAM: 8GB; Warranty: 1-year minimum. "
        "Staff Windows Laptop: RAM: 16GB; Warranty: 3-year minimum"
    )
    assert _check("Product Specification", separate, SPECS).status == "accepted"
    same = "Laptop A Warranty: 3-year minimum. Laptop B Warranty: 3-year minimum"
    assert not semantic_problems("Product Specification", "Warranty: 3-year minimum", [same])


def test_oem_warranty_requirement_from_an_addendum_is_not_omitted():
    oem = "The district will ask all warranties to be made by OEM."
    addendum = _hit(
        "a1",
        "8. Does the district prefer warranties underwritten by insurance?\nAnswer:\n" + oem,
        doc=DocumentType.addendum,
        file="addendum-1.pdf",
        addendum=1,
    )
    base = _hit("s1", SPECS, page=4)
    value = (
        "Student Chromebook: RAM: 8GB; Warranty: 1-year minimum. "
        "Staff Windows Laptop: RAM: 16GB; Warranty: 3-year minimum"
    )
    draft = DraftField(name="Product Specification", value=value, evidence_ids=["s1"])
    decision = reconcile_field("Product Specification", draft, [base, addendum], BID)
    assert decision.reject_reason is None
    assert any(oem[:-1] in (change.quote or "") for change in decision.all_changes)
    corpus = [
        (("specification", "requirements", "memory"), _result(SPECS, page=4)),
        (
            ("warranty", "warranties", "specification"),
            _result(addendum.text, doc=DocumentType.addendum, file="addendum-1.pdf", addendum=1),
        ),
    ]
    model = FakeModel({"Product Specification": (value, ["Staff Windows Laptop"])})
    output, _, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    assert "made by OEM" in output.fields["Product Specification"].notes


# 6: requirements spread over pages; compact tables and list budgets reach the model.


def test_requirements_spread_across_pages_are_extracted_with_every_page_cited():
    page3 = (
        "For resellers, factory-authorized repair certifications must be submitted with each "
        "proposal."
    )
    page33 = '["W9 Form", "File", "W9 Form completed, signed and attached to the submission"]'
    corpus = [
        (("affidavits", "forms", "certificates"), _result(page3, page=3)),
        (("affidavits", "forms", "attachments"), _result(page33, page=33)),
    ]
    value = (
        "factory-authorized repair certifications must be submitted with each proposal; "
        "W9 Form completed, signed and attached to the submission"
    )
    model = FakeModel(
        {"Any Additional Documentation Required": (value, ["factory-authorized", "W9 Form"])}
    )
    output, _, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    field = output.fields["Any Additional Documentation Required"]
    assert field.value == value
    assert {source.page for source in field.sources} == {3, 33}


def test_table_rows_are_compacted_and_list_fields_get_a_larger_excerpt():
    row = '["SI# CC7802", null, "", "Dell Latitude 5550 laptop", null, null, null, "30", null]'
    assert compact(row) == "SI# CC7802 | Dell Latitude 5550 laptop | 30"
    filler = "General terms and conditions apply to every purchase order. " * 20
    text = filler + "Item: Dell Latitude 5550, quantity 30 each."
    hits = {name: [_hit(f"{name[:3]}-1", text)] for name in ("Product", "Title")}
    request = extraction_request(
        "product", hits, ["Product"], 0, {}, ExtractionConfig(excerpt_chars=700)
    )
    assert "quantity 30" in request.user and LIST_INSTRUCTION in request.user
    title = extraction_request("identity", hits, ["Title"], 0, {}, ExtractionConfig())
    assert LIST_INSTRUCTION not in title.user


def test_structured_list_reply_is_flattened_into_a_checkable_string():
    reply = json.dumps(
        {
            "fields": {
                "Product": {
                    "value": [
                        {"item": "Dell Latitude 5550 laptop", "quantity": 30},
                        {"item": "Dell Thunderbolt 4 Dock WD22TB4", "quantity": 30},
                    ],
                    "evidence_ids": ["product-1"],
                    "notes": "Table.",
                }
            }
        }
    )
    draft = parse_drafts(["Product"], reply)["Product"]
    assert draft.value == "Dell Latitude 5550 laptop 30, Dell Thunderbolt 4 Dock WD22TB4 30"
    assert _check("Product", draft.value, TABLE).status == "accepted"


def test_value_assembled_from_several_shown_passages_gets_every_citation():
    page4 = "Tier 1 Chromebook Minimum Requirements: Memory: 8GB"
    page6 = "Staff Laptop Minimum Requirements: Memory: 16GB"
    corpus = [
        (("specification", "requirements", "memory"), _result(page4, page=4)),
        (("specification", "requirements", "memory"), _result(page6, page=6)),
    ]
    value = "Tier 1 Chromebook: Memory: 8GB; Staff Laptop: Memory: 16GB"
    model = FakeModel({"Product Specification": (value, ["Tier 1 Chromebook"])})
    output, _, events = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    field = output.fields["Product Specification"]
    assert field.value == value and {source.page for source in field.sources} == {4, 6}
    assert any("citation repaired" in (event.decision or "") for event in events)
    uncited = FakeModel({"Product Specification": (value, [])})
    output, _, _ = run_extraction("B", BID, _search(corpus), uncited, ExtractionConfig())
    assert output.fields["Product Specification"].value is None


def test_delivery_date_must_come_from_a_delivery_sentence():
    answer = (
        "4. When do you need these delivered by?\nAnswer:\nThe district anticipates requesting "
        "quotes from awarded vendors starting in September of 2024.\n"
        "6. Answer: Any awarded vendor will need to coordinate a schedule of deliveries with "
        "the technology department."
    )
    quotes = _check("Delivery Date", "starting in September of 2024", answer)
    assert quotes.status == "rejected"
    assert any("quotes, orders or the award" in reason for reason in quotes.reasons)
    paraphrase = _check("Delivery Date", "Requesting quotes starting in September 2024", answer)
    assert paraphrase.status == "rejected"
    assert any("quotes, orders or the award" in reason for reason in paraphrase.reasons)
    schedule = (
        "Any awarded vendor will need to coordinate a schedule of deliveries with the "
        "technology department."
    )
    assert _check("Delivery Date", schedule, answer).status == "accepted"
    assert _check(
        "Delivery Date", "within 45 days of Award", "9. Delivery within 45 days of Award."
    )


def test_summary_number_at_a_sentence_end_is_checked_without_its_period():
    text = "Solicitation JA-207652 seeks student computing devices for the district."
    value = "The district seeks student computing devices under solicitation JA-207652."
    assert _check("Bid Summary", value, text).status == "accepted"


# 7: mandatory documents versus questions, examples and conditions.


def test_required_documents_are_distinguished_from_questions_and_examples():
    text = (
        "Is a W-9 form required with the bid?\n\n"
        "Bidders must submit the Mercury Affidavit with the proposal.\n\n"
        "Optional exhibits such as the Contract Affidavit may be attached.\n\n"
        "If the vendor is not the manufacturer, a Letter of Authorization must be submitted.\n\n"
        "EXHIBIT B CONTRACT AFFIDAVIT I affirm the statements made as of the date of this "
        "Contract Affidavit remain true."
    )
    assert not document_problems("Mercury Affidavit", [text])
    assert document_problems("W-9 form", [text]) == [
        "'W-9 form' appears only in a question or example, not a requirement"
    ]
    assert document_problems("Contract Affidavit", [text]) == [
        "'Contract Affidavit' is not stated as a required submission in the cited text"
    ]
    assert document_problems("Letter of Authorization", [text]) == [
        "'Letter of Authorization' is required only under a condition the value omits"
    ]
    conditional = "Letter of Authorization if the vendor is not the manufacturer"
    assert not document_problems(conditional, [text])
    service = "Bidders must submit the Mercury Affidavit; a three-year warranty is required"
    assert document_problems(service, [text + "\n\nA three-year warranty is required."]) == [
        "'a three-year warranty is required' is not a document, form or certificate"
    ]
    mixed = _check(
        "Any Additional Documentation Required", "Mercury Affidavit, Contract Affidavit", text
    )
    assert mixed.status == "rejected" and not only_incomplete(mixed.reasons)


# 8, 9: quantities and identifiers stay distinct and exact.

TABLE = (
    '["SI# CC7802", "Dell Latitude 5550 laptop", "30"]\n'
    '["SI# CC7803", "Dell Thunderbolt 4 Dock WD22TB4", "30"]\n'
    '["Part", "210-BLYZ"]'
)


def test_distinct_quantities_and_units_stay_distinct():
    merged = _check("Product", "60 laptops and docks", TABLE)
    assert merged.status == "rejected"
    distinct = "30 Dell Latitude 5550 laptop; 30 Dell Thunderbolt 4 Dock WD22TB4"
    assert _check("Product", distinct, TABLE).status == "accepted"


def test_model_and_part_numbers_are_preserved_exactly():
    assert _check("Model_no", "Dell Thunderbolt 4 Dock WD22TB4", TABLE).status == "accepted"
    assert _check("Model_no", "WD22TB", TABLE).status == "rejected"
    assert _check("Model_no", "Dell Latitude 5500", TABLE).status == "rejected"
    assert _check("Part_no", "210-BLYZ", TABLE).status == "accepted"
    assert _check("Part_no", "210-BLY", TABLE).status == "rejected"


# 10, 11, 13, 14: absence, unsupported claims, evidence and bid isolation.


def test_correctly_missing_values_stay_not_found():
    corpus = [(("title",), _result("Request for Proposal: Student Computing Devices"))]
    model = FakeModel({"Title": ("Student Computing Devices", ["Student"])})
    output, state, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    bond = output.fields["Bid Bond Requirement"]
    assert bond.value is None and bond.sources == []
    assert state.fields["Bid Bond Requirement"].status == "not_found"
    assert bond.confidence == 0.5 and band(bond.confidence) == "medium"


def test_unsupported_claims_are_rejected_and_never_score_high():
    text = "Payment is due within thirty days of invoice receipt."
    assert _check("Payment Terms", "Net 45", text).status == "rejected"
    corpus = [(("payment",), _result(text))]
    model = FakeModel({"Payment Terms": ("Net 45 with a 2% early-payment discount", [])})
    output, state, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    assert output.fields["Payment Terms"].value is None
    assert state.fields["Payment Terms"].status == "unresolved"
    assert output.fields["Payment Terms"].confidence == 0.15


def test_every_populated_value_has_valid_supporting_evidence():
    corpus = _due_date_corpus(
        (2, "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST")
    )
    model = FakeModel(
        {
            "Due Date": ("July 9, 2024 at 2:00 PM CST", ["July 9, 2024"]),
            "Title": ("Student Computing Devices", ["Student"]),
        }
    )
    output, state, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    populated = [name for name, field in output.fields.items() if field.value is not None]
    assert populated
    for name in populated:
        slot = state.fields[name]
        cited = [hit.text for hit in slot.evidence if hit.evidence_id in slot.draft.evidence_ids]
        assert output.fields[name].sources and all(s.page for s in output.fields[name].sources)
        assert support_kind(name, output.fields[name].value, cited) != "none"


def test_cross_bid_evidence_cannot_satisfy_a_field():
    foreign = _hit("x1", "Solicitation number JA-207652", bid=OTHER)
    draft = DraftField(name="Bid Number", value="JA-207652", evidence_ids=["x1"])
    result = validate_field(draft, [foreign])
    assert result.status == "rejected" and "Cross-bid citation" in result.reasons
    corpus = [(("solicitation", "rfp number"), _result("Solicitation number JA-207652", bid=OTHER))]
    model = FakeModel({"Bid Number": ("JA-207652", ["JA-207652"])})
    output, _, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    assert output.fields["Bid Number"].value is None


# 15: confidence follows evidence quality.


def test_confidence_responds_to_evidence_quality():
    strong = score(Signals("accepted", "verbatim", agreeing_sources=2))
    verbatim = score(Signals("accepted", "verbatim", agreeing_sources=1))
    tokens = score(Signals("accepted", "tokens", agreeing_sources=1))
    paraphrase = score(Signals("accepted", "paraphrase", agreeing_sources=1))
    retried = score(Signals("accepted", "verbatim", agreeing_sources=1, retries=2))
    incomplete = score(Signals("accepted", "verbatim", agreeing_sources=2, incomplete=True))
    absent = score(Signals("not_found"))
    unresolved = score(Signals("unresolved"))
    conflict = score(Signals("unresolved", conflict=True))
    assert strong > verbatim > tokens > paraphrase
    assert verbatim > retried
    assert incomplete <= 0.5 < verbatim
    assert absent == 0.5 and score(Signals("not_found", retries=1)) == 0.4
    assert unresolved == 0.15 and conflict == 0.1
    assert max(strong, verbatim) <= 0.95 and band(strong) == "high"
    assert band(unresolved) == band(conflict) == band(incomplete) == "low"


# 16, 18: the 20-field contract and deterministic output for fixed replies.


def _summary_run():
    corpus = _due_date_corpus(
        (2, "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST")
    )
    corpus.append((("scope",), _result("The district seeks student computing devices.", page=3)))
    summary = (
        "The district seeks student computing devices. Proposals are due July 9, 2024 at "
        "2:00 PM CST."
    )
    model = FakeModel(
        {
            "Due Date": ("July 9, 2024 at 2:00 PM CST", ["July 9, 2024"]),
            "Title": ("Student Computing Devices", ["Student"]),
            "Bid Summary": (summary, ["district seeks", "Due Date: July 9"]),
        }
    )
    return run_extraction("B", BID, _search(corpus), model, ExtractionConfig(concurrency=1)), model


def test_summary_sees_validated_values_and_cites_them():
    (output, _, _), model = _summary_run()
    prompt = next(call.user for call in model.calls if "Field: Bid Summary" in call.user)
    assert "Validated field values" in prompt
    assert "Due Date: July 9, 2024 at 2:00 PM CST" in prompt
    summary = output.fields["Bid Summary"]
    assert summary.value and "July 9, 2024" in summary.value
    assert {source.file for source in summary.sources} >= {"addendum-2.pdf"}


def test_a_cited_validated_value_brings_every_passage_that_validated_it():
    page14 = "Form 1295 must be submitted with the response."
    page33 = "W9 Form completed, signed and attached to the submission."
    corpus = [
        (("affidavits", "forms", "certificates"), _result(page14, page=14)),
        (("affidavits", "forms", "attachments"), _result(page33, page=33)),
        (("scope",), _result("The district seeks student computing devices.", page=3)),
    ]
    documents = "Form 1295 must be submitted with the response; W9 Form completed, signed"
    summary = "The district seeks student computing devices and requires Form 1295 and a W9 Form."
    model = FakeModel(
        {
            "Any Additional Documentation Required": (documents, ["Form 1295", "W9 Form"]),
            "Bid Summary": (summary, ["district seeks", "Any Additional Documentation"]),
        }
    )
    output, _, _ = run_extraction("B", BID, _search(corpus), model, ExtractionConfig())
    pages = {source.page for source in output.fields["Bid Summary"].sources}
    assert output.fields["Bid Summary"].value == summary and {3, 14, 33} <= pages


def test_a_list_of_component_codes_gets_a_clear_identifier_reason():
    codes = _check("Part_no", "SI# CC7803; 210-BLYZ", TABLE)
    assert codes.reasons == [
        "Part_no lists 2 codes; give the one identifier of the requested product"
    ]


def test_twenty_field_json_contract_is_unchanged():
    (output, _, _), _ = _summary_run()
    dumped = json.loads(output.model_dump_json())
    assert list(dumped["fields"]) == list(REQUIRED_FIELDS)
    for field in dumped["fields"].values():
        assert set(field) == {"value", "sources", "confidence", "notes"}
        assert 0 <= field["confidence"] <= 1
    BidExtraction.model_validate(dumped)
    assert tuple(CATALOG) == REQUIRED_FIELDS


def test_extraction_is_deterministic_for_fixed_model_replies():
    (first, _, _), _ = _summary_run()
    (second, _, _), _ = _summary_run()
    assert first.model_dump() == second.model_dump()
