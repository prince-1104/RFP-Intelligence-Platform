"""Offline extraction workflow. Model calls are fakes; search responses are constructed."""

import json
import threading
import time
import uuid

import pytest

from rfp import cli
from rfp.agents.errors import ModelError
from rfp.agents.extractors import CATALOG, extraction_request, parse_drafts
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.orchestrator import _interleave, run_extraction
from rfp.agents.reconciliation import reconcile_due_date, reconcile_field
from rfp.agents.retrieval import hits_from_response, retrieve
from rfp.agents.schemas import (
    REQUIRED_FIELDS,
    BidExtraction,
    DraftField,
    EvidenceHit,
    ExtractionConfig,
    SourceRef,
)
from rfp.agents.state import ExtractionState
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
)
from rfp.search.errors import SearchServiceError

BID = uuid.UUID("5809c088-8514-58c3-a0e5-4d9e72adab0b")
OTHER = uuid.UUID("05876a57-4ff3-52ce-a5fe-9d605ebf94b3")


class FakeModel:
    provider = "fake"
    model = "fake"

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[ModelRequest] = []
        self.inflight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def complete(self, request: ModelRequest) -> ModelResponse:
        with self._lock:
            self.calls.append(request)
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        try:
            if request.purpose.startswith("extract"):
                time.sleep(0.02)
            text = self.handler(request)
        finally:
            with self._lock:
                self.inflight -= 1
        if isinstance(text, Exception):
            raise text
        return ModelResponse(text, self.provider, self.model, 11, 7, 1.0)


def _citation(bid, file, path, doc_type, text_page=1, html=None, addendum=None):
    return Citation(
        occurrence_id=uuid.uuid4(),
        chunk_generation_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="abc",
        bid_id=bid,
        document_id=uuid.uuid4(),
        original_file_name=file,
        relative_path=path,
        document_type=doc_type,
        addendum_number=addendum,
        chunk_type="prose",
        page_start=None if html else text_page,
        page_end=None if html else text_page,
        html_locators=[] if html is None else [LocatorRef(relative_path=path, element_id=html)],
        location_known=True,
        normalization_version="1.0",
        chunking_version="1.0",
    )


def _result(bid, file, path, doc_type, text, page=1, html=None, addendum=None, rank=1):
    return EvidenceResult(
        rank=rank,
        occurrence_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="abc",
        text=text,
        chunk_type="prose",
        citation=_citation(bid, file, path, doc_type, page, html, addendum),
        scores=ScoreComponents(final_score=0.5),
        identifier_match=IdentifierMatch(),
    )


def _response(results, mode=RetrievalMode.hybrid_rerank):
    return SearchResponse(
        query=QueryDiagnostics(original="q", normalized="q", dense_query="q", sparse_query="q"),
        mode=mode,
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


def _hit(evidence_id, file, path, doc_type, text, page=1, html=None, addendum=None, bid=BID):
    source = SourceRef(
        file=file,
        relative_path=path,
        document_type=doc_type,
        addendum_number=addendum,
        page=None if html else page,
        html_element_id=html,
        bid_id=bid,
    )
    return EvidenceHit(
        evidence_id=evidence_id,
        bid_id=bid,
        text=text,
        rank=1,
        mode="hybrid_rerank",
        final_score=0.4,
        sources=[source],
    )


def _evidence_id(prompt: str, needle: str) -> str:
    last = None
    for line in prompt.splitlines():
        if line.startswith("["):
            last = line[1 : line.index("]")]
        elif last and needle.casefold() in line.casefold():
            return last
    raise AssertionError(prompt)


def test_catalog_and_schema_cover_the_twenty_fields():
    assert tuple(CATALOG) == REQUIRED_FIELDS
    assert {spec.group for spec in CATALOG.values()} == {
        "identity",
        "logistics",
        "commercial",
        "product",
        "summary",
    }
    with pytest.raises(ValueError):
        BidExtraction.model_validate(
            {
                "bid_id": "Bid1",
                "fields": {},
                "addendum_changes": [],
                "validation": {"passed": 0, "failed": 0, "unresolved": 0, "not_found": 0},
            }
        )


def test_known_miss_queries_are_explicit():
    assert any("awarded" in step.query.casefold() for step in CATALOG["Delivery Date"].steps)
    assert any("buyer" in step.query.casefold() for step in CATALOG["contact_info"].steps)
    specs = CATALOG["Product Specification"].steps
    assert any(DocumentType.specs in step.doc_types for step in specs)
    assert any(step.hard for step in specs)
    assert CATALOG["Due Date"].steps[1].doc_types == (DocumentType.addendum,)
    listing = [s for s in CATALOG["Due Date"].steps if DocumentType.bid_page in s.doc_types]
    assert len(listing) == 1 and listing[0].hard


def test_state_records_retries_without_dropping_other_fields():
    state = ExtractionState.create("Bid1", BID)
    assert state.status == "running"
    state.fields["Title"].status = "accepted"
    state.fields["Due Date"].status = "rejected"
    state.fields["Due Date"].retry_count = 2
    assert state.pending() == ["Due Date"]
    assert state.fields["Title"].status == "accepted"


def test_retrieval_drops_other_bids_and_always_filters():
    seen = []

    def search(request):
        seen.append(request)
        return _response(
            [
                _result(
                    BID, "rfp.pdf", "rfp.pdf", DocumentType.rfp, "Bid number JA-207652", page=1
                ),
                _result(
                    OTHER,
                    "other.pdf",
                    "other.pdf",
                    DocumentType.rfp,
                    "Bid number JA-207652",
                    page=1,
                ),
            ]
        )

    hits, _ = retrieve(
        search,
        BID,
        "official solicitation or RFP number",
        mode=RetrievalMode.hybrid_rerank,
        top_k=12,
        doc_types=[],
        addendum_numbers=[],
        evidence_prefix="bid-number",
    )
    assert seen[0].filters.bid_ids == [BID]
    assert [hit.sources[0].file for hit in hits] == ["rfp.pdf"]
    foreign = hits_from_response(
        _response([_result(OTHER, "other.pdf", "other.pdf", DocumentType.rfp, "JA-207652")]),
        BID,
        "x",
    )
    assert foreign == []


def test_html_citation_has_no_invented_page_and_fabricated_ids_fail():
    hit = _hit(
        "e1",
        "page.html",
        "page.html",
        DocumentType.bid_page,
        "Buyer buyer@example.com",
        html="g_103",
    )
    draft = DraftField(
        name="contact_info",
        value="buyer@example.com",
        evidence_ids=["e1"],
        notes="Copied from the bid page.",
    )
    assert validate_field(draft, [hit]).passed
    assert hit.sources[0].output().page is None
    assert hit.sources[0].output().html_element_id == "g_103"
    fabricated = draft.model_copy(update={"evidence_ids": ["e999"]})
    rejected = validate_field(fabricated, [hit])
    assert not rejected.passed
    assert any("Fabricated" in reason for reason in rejected.reasons)


def test_missing_evidence_is_a_null_with_a_reason():
    draft = DraftField(name="Bid Bond Requirement", value=None, notes="Not found in documents")
    result = validate_field(draft, [])
    assert result.passed and result.status == "not_found"
    bare = validate_field(DraftField(name="Bid Bond Requirement", value=None, notes=""), [])
    assert bare.passed and bare.status == "not_found"


def test_cross_bid_source_is_rejected():
    hit = _hit("e1", "rfp.pdf", "rfp.pdf", DocumentType.rfp, "JA-207652", bid=BID)
    hit.sources[0].bid_id = OTHER
    result = validate_field(
        DraftField(name="Bid Number", value="JA-207652", evidence_ids=["e1"], notes="id"),
        [hit],
    )
    assert not result.passed
    assert any("Cross-bid" in reason for reason in result.reasons)


def test_original_due_date_is_the_labelled_deadline_not_the_first_date():
    base = _hit(
        "base",
        "rfp.pdf",
        "rfp.pdf",
        DocumentType.rfp,
        "First Advertisement Date/Issue Date 26-MAY-2024 08:00:00 Pre-Proposal Meeting "
        "10-JUN-2024 14:00:00 Solicitation Due 27-JUN-2024 14:00:00",
        page=2,
    )
    changed = _hit(
        "add",
        "addendum-2.pdf",
        "addendum-2.pdf",
        DocumentType.addendum,
        "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST",
        page=1,
        addendum=2,
    )
    draft = DraftField(name="Due Date", value="July 9, 2024 at 2:00 PM CST", evidence_ids=["add"])
    result = reconcile_due_date(draft, [base, changed])
    assert result.change is not None
    assert result.change.original_value == "27-JUN-2024 14:00:00"
    unlabelled = base.model_copy(update={"text": "Issue Date 26-MAY-2024 08:00:00"})
    result = reconcile_due_date(draft, [unlabelled, changed])
    assert result.change is not None and result.change.original_value is None


def test_month_year_delivery_date_is_a_date_when_quoted():
    hit = _hit(
        "d1",
        "addendum-1.pdf",
        "addendum-1.pdf",
        DocumentType.addendum,
        "Delivery requests are anticipated to begin in September of 2024.",
        addendum=1,
    )
    draft = DraftField(name="Delivery Date", value="September of 2024", evidence_ids=["d1"])
    assert validate_field(draft, [hit]).status == "accepted"
    vague = draft.model_copy(update={"value": "after award"})
    assert "Date field is not a date stated in the evidence" in validate_field(vague, [hit]).reasons


def test_reply_keys_are_matched_loosely_and_prompt_names_exact_keys():
    names = ["Term of Bid", "contact_info", "Title"]
    reply = json.dumps(
        {
            "fields": {
                "term_of_bid": {"value": "Initial Term: 3", "evidence_ids": ["t1"], "notes": ""},
                "Contact Info": {"value": None, "evidence_ids": [], "notes": "Not found"},
                "Issuing Organization": {"value": "Dallas ISD", "evidence_ids": ["x"]},
            }
        }
    )
    drafts = parse_drafts(names, reply)
    assert drafts["Term of Bid"].value == "Initial Term: 3"
    assert drafts["contact_info"].notes == "Not found"
    assert drafts["Title"].notes == "Model omitted this field"
    request = extraction_request("identity", {}, names, 0, {}, ExtractionConfig())
    assert json.dumps(names) in request.user


def test_every_query_step_reaches_the_first_evidence_slots():
    first = [
        _hit(f"a{i}", "rfp.pdf", "rfp.pdf", DocumentType.rfp, f"primary {i}") for i in range(9)
    ]
    second = [_hit("b0", "add.pdf", "add.pdf", DocumentType.addendum, "secondary", addendum=1)]
    merged = _interleave([first, second])
    assert [hit.evidence_id for hit in merged[:3]] == ["a0", "b0", "a1"]
    assert len(merged) == 10


def test_addendum_due_date_requires_text_and_ignores_number_alone():
    base = _hit(
        "base",
        "rfp.pdf",
        "rfp.pdf",
        DocumentType.rfp,
        "Solicitation Due 27-JUN-2024 14:00:00",
        page=2,
    )
    changed = _hit(
        "add",
        "addendum-2.pdf",
        "addendum-2.pdf",
        DocumentType.addendum,
        "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST",
        page=1,
        addendum=2,
    )
    stale = DraftField(
        name="Due Date", value="27-JUN-2024 14:00:00", evidence_ids=["base"], notes="original"
    )
    rejected = reconcile_due_date(stale, [base, changed])
    assert rejected.reject_reason
    assert rejected.change is not None
    assert rejected.change.revised_value.startswith("July 9, 2024")
    assert rejected.change.original_value.startswith("27-JUN-2024")
    current = stale.model_copy(
        update={"value": "July 9, 2024 at 2:00 PM CST", "evidence_ids": ["add"]}
    )
    accepted = reconcile_due_date(current, [base, changed])
    assert accepted.reject_reason is None
    assert accepted.change is not None and accepted.change.conflict is False
    quiet = _hit(
        "a2",
        "addendum-2.pdf",
        "addendum-2.pdf",
        DocumentType.addendum,
        "Payment remains Net 30 as previously stated.",
        addendum=2,
    )
    assert (
        reconcile_field(
            "Payment Terms",
            DraftField(name="Payment Terms", value="Net 30", evidence_ids=["a2"], notes="terms"),
            [quiet],
        ).change
        is None
    )


def test_conflicting_addenda_are_not_resolved_by_number():
    first = _hit(
        "a1",
        "addendum-1.pdf",
        "addendum-1.pdf",
        DocumentType.addendum,
        "The new due date for this RFP will be July 1, 2024 at 2:00 PM CST",
        addendum=1,
    )
    second = _hit(
        "a2",
        "addendum-2.pdf",
        "addendum-2.pdf",
        DocumentType.addendum,
        "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST",
        addendum=2,
    )
    result = reconcile_due_date(
        DraftField(
            name="Due Date", value="July 9, 2024 at 2:00 PM CST", evidence_ids=["a2"], notes="later"
        ),
        [first, second],
    )
    assert result.change is not None and result.change.conflict
    assert result.change.revised_value is None
    assert result.reject_reason == "conflicting addendum due dates"


def test_due_date_retry_uses_the_addendum_and_keeps_a_good_field():
    rfp = _result(
        BID, "rfp.pdf", "rfp.pdf", DocumentType.rfp, "Solicitation Due 27-JUN-2024 14:00:00", page=2
    )
    addendum = _result(
        BID,
        "addendum-2.pdf",
        "addendum-2.pdf",
        DocumentType.addendum,
        "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST",
        page=1,
        addendum=2,
    )
    title = _result(
        BID, "rfp.pdf", "rfp.pdf", DocumentType.rfp, "Student and Staff Computing Devices", page=1
    )

    def search(request):
        assert request.filters.bid_ids == [BID]
        query = request.query.casefold()
        if "title" in query:
            return _response([title])
        if "due" in query or "deadline" in query or "addendum" in query or "solicitation" in query:
            return _response([rfp, addendum])
        return _response([])

    def model(request: ModelRequest):
        fields = {}
        if "Field: Title" in request.user:
            fields["Title"] = {
                "value": "Student and Staff Computing Devices",
                "evidence_ids": [_evidence_id(request.user, "Student and Staff Computing Devices")],
                "notes": "Title page.",
            }
        if "Field: Due Date" in request.user:
            revised = "Previous rejection" in request.user
            needle = "July 9, 2024 at 2:00 PM CST" if revised else "27-JUN-2024 14:00:00"
            fields["Due Date"] = {
                "value": needle,
                "evidence_ids": [_evidence_id(request.user, needle)],
                "notes": "Extended by Addendum 2." if revised else "Original header.",
            }
        return json.dumps({"fields": fields})

    fake = FakeModel(model)
    output, state, events = run_extraction(
        "Bid1",
        BID,
        search,
        fake,
        ExtractionConfig(concurrency=2, max_retries=2),
    )
    assert output.fields["Due Date"].value == "July 9, 2024 at 2:00 PM CST"
    assert output.fields["Due Date"].sources[0].file == "addendum-2.pdf"
    assert output.fields["Due Date"].sources[0].page == 1
    assert output.fields["Title"].value == "Student and Staff Computing Devices"
    assert output.addendum_changes[0].original_value.startswith("27-JUN-2024")
    assert output.addendum_changes[0].conflict is False
    assert state.fields["Due Date"].retry_count == 1
    assert any(event.agent == "validator" and event.field == "Due Date" for event in events)
    assert output.validation.passed >= 2
    BidExtraction.model_validate(output.model_dump())


def test_retry_cap_and_partial_success_when_one_field_is_fabricated():
    evidence = _result(
        BID, "rfp.pdf", "rfp.pdf", DocumentType.rfp, "Official number JA-207652", page=1
    )

    def search(request):
        assert request.filters.bid_ids == [BID]
        if "solicitation" in request.query.casefold() or "number" in request.query.casefold():
            return _response([evidence])
        if "title" in request.query.casefold():
            return _response(
                [
                    _result(
                        BID,
                        "rfp.pdf",
                        "rfp.pdf",
                        DocumentType.rfp,
                        "Student and Staff Computing Devices",
                        page=1,
                    )
                ]
            )
        return _response([])

    def model(request: ModelRequest):
        if "Bid Number" in request.user and "Title" not in request.user.split("Field: ")[1][:20]:
            pass
        fields = {}
        if "Field: Bid Number" in request.user:
            fields["Bid Number"] = {
                "value": "JA-000000",
                "evidence_ids": ["missing"],
                "notes": "guess",
            }
        if "Field: Title" in request.user:
            evidence_id = next(
                line[1 : line.index("]")]
                for line in request.user.splitlines()
                if line.startswith("[") and "Title" in request.user
            )
            # The identity prompt contains both fields. Use the id above the title text.
            lines = request.user.splitlines()
            evidence_id = None
            for index, line in enumerate(lines):
                if line.startswith("[") and "Student and Staff" in lines[index + 1]:
                    evidence_id = line[1 : line.index("]")]
            fields["Title"] = {
                "value": "Student and Staff Computing Devices",
                "evidence_ids": [evidence_id],
                "notes": "Title page.",
            }
        return json.dumps({"fields": fields})

    fake = FakeModel(model)
    output, state, _events = run_extraction(
        "Bid1", BID, search, fake, ExtractionConfig(concurrency=1, max_retries=2)
    )
    assert output.fields["Bid Number"].value is None
    assert output.fields["Bid Number"].sources == []
    assert "Validation failed" in output.fields["Bid Number"].notes
    assert state.fields["Bid Number"].retry_count == 2
    assert state.fields["Bid Number"].status == "unresolved"
    assert output.fields["Title"].value == "Student and Staff Computing Devices"
    bid_calls = [call for call in fake.calls if "Field: Bid Number" in call.user]
    assert len(bid_calls) == 3
    assert output.validation.failed == output.validation.unresolved
    assert (
        output.validation.passed + output.validation.not_found + output.validation.unresolved == 20
    )


def test_parallel_groups_respect_the_concurrency_limit():
    def search(request):
        assert request.filters.bid_ids == [BID]
        query = request.query.casefold()
        samples = {
            "solicitation": ("Bid Number", "rfp.pdf", "JA-207652"),
            "bond": ("Bid Bond Requirement", "rfp.pdf", "Not required"),
            "model number": ("Model_no", "specs.pdf", "Latitude 5450"),
            "submission deadline": ("Due Date", "rfp.pdf", "July 9, 2024 at 2:00 PM CST"),
        }
        for needle, (_name, file, text) in samples.items():
            if needle in query:
                doc_type = DocumentType.specs if file.startswith("specs") else DocumentType.rfp
                return _response([_result(BID, file, file, doc_type, text, page=3)])
        return _response([])

    def model(request: ModelRequest):
        lines = request.user.splitlines()
        fields = {}
        current = None
        evidence_id = None
        for line in lines:
            if line.startswith("Field: "):
                current = line.removeprefix("Field: ")
                evidence_id = None
            elif current and line.startswith("["):
                evidence_id = line[1 : line.index("]")]
            elif current and evidence_id and not line.startswith("Field"):
                value = {
                    "Bid Number": "JA-207652",
                    "Bid Bond Requirement": "Not required",
                    "Model_no": "Latitude 5450",
                    "Due Date": "July 9, 2024 at 2:00 PM CST",
                }.get(current)
                if value and value.casefold() in line.casefold():
                    fields[current] = {
                        "value": value,
                        "evidence_ids": [evidence_id],
                        "notes": "Copied from the cited evidence.",
                    }
                    evidence_id = None
        return json.dumps({"fields": fields})

    fake = FakeModel(model)
    output, _state, _events = run_extraction(
        "Bid1", BID, search, fake, ExtractionConfig(concurrency=2, max_retries=0)
    )
    assert fake.peak <= 2
    assert fake.peak >= 2
    assert output.fields["Bid Number"].value == "JA-207652"
    assert output.fields["Model_no"].sources[0].page == 3


def test_model_errors_do_not_discard_successful_fields():
    def search(request):
        query = request.query.casefold()
        if "bond" in query:
            return _response(
                [
                    _result(
                        BID,
                        "rfp.pdf",
                        "rfp.pdf",
                        DocumentType.rfp,
                        "No bid bond is required",
                        page=4,
                    )
                ]
            )
        if "title" in query:
            return _response(
                [
                    _result(
                        BID,
                        "rfp.pdf",
                        "rfp.pdf",
                        DocumentType.rfp,
                        "Student and Staff Computing Devices",
                        page=1,
                    )
                ]
            )
        return _response([])

    def model(request: ModelRequest):
        if request.purpose.startswith("extract:commercial"):
            raise ModelError("llm_timeout", "The language model timed out")
        if "not json" in request.purpose:
            return "this is not json"
        lines = request.user.splitlines()
        evidence_id = None
        for index, line in enumerate(lines):
            if line.startswith("[") and "Student and Staff" in lines[index + 1]:
                evidence_id = line[1 : line.index("]")]
        return json.dumps(
            {
                "fields": {
                    "Title": {
                        "value": "Student and Staff Computing Devices",
                        "evidence_ids": [evidence_id],
                        "notes": "Title page.",
                    }
                }
            }
        )

    timeout = FakeModel(model)
    output, state, _events = run_extraction(
        "Bid1", BID, search, timeout, ExtractionConfig(concurrency=1, max_retries=2)
    )
    assert output.fields["Title"].value == "Student and Staff Computing Devices"
    assert state.fields["Bid Bond Requirement"].status == "unresolved"
    assert "llm_timeout" in output.fields["Bid Bond Requirement"].notes
    assert (
        len([call for call in timeout.calls if call.purpose.startswith("extract:commercial")]) == 3
    )

    def malformed(request: ModelRequest):
        if request.purpose.startswith("extract:commercial"):
            return "NOT JSON"
        return model(request)

    broken = FakeModel(malformed)
    output, _state, _events = run_extraction(
        "Bid1", BID, search, broken, ExtractionConfig(concurrency=1, max_retries=0)
    )
    assert output.fields["Bid Bond Requirement"].value is None
    assert "llm_malformed" in output.fields["Bid Bond Requirement"].notes

    def provider(request: ModelRequest):
        if request.purpose.startswith("extract:commercial"):
            raise ModelError("llm_provider_error", "The language model returned HTTP 503")
        return model(request)

    failed = FakeModel(provider)
    output, _state, _events = run_extraction(
        "Bid1", BID, search, failed, ExtractionConfig(concurrency=1, max_retries=0)
    )
    assert "llm_provider_error" in output.fields["Bid Bond Requirement"].notes
    assert output.fields["Title"].sources


def test_search_failure_is_field_level_and_workflow_fails_when_nothing_is_searched():
    def explode(request):
        raise SearchServiceError(
            "Vector store is unavailable or timed out", "vector_store_unavailable"
        )

    output, state, events = run_extraction(
        "Bid1",
        BID,
        explode,
        FakeModel(lambda request: "{}"),
        ExtractionConfig(concurrency=1, max_retries=0),
    )
    assert state.status == "failed"
    assert output.validation.unresolved == 20
    assert all(field.value is None for field in output.fields.values())
    assert any(event.error == "vector_store_unavailable" for event in events)


def test_critic_can_reject_but_a_critic_error_cannot_approve_a_bad_value():
    evidence = _result(
        BID, "rfp.pdf", "rfp.pdf", DocumentType.rfp, "Official number JA-207652", page=1
    )

    def search(request):
        if "number" in request.query.casefold() or "solicitation" in request.query.casefold():
            return _response([evidence])
        return _response([])

    def model(request: ModelRequest):
        if request.purpose.startswith("critic"):
            return json.dumps(
                {"supported": False, "reason": "the identifier is not the one stated"}
            )
        lines = request.user.splitlines()
        evidence_id = next(line[1 : line.index("]")] for line in lines if line.startswith("["))
        return json.dumps(
            {
                "fields": {
                    "Bid Number": {
                        "value": "JA-207652",
                        "evidence_ids": [evidence_id],
                        "notes": "header",
                    }
                }
            }
        )

    output, state, events = run_extraction(
        "Bid1",
        BID,
        search,
        FakeModel(model),
        ExtractionConfig(concurrency=1, max_retries=0, critic_enabled=True),
    )
    assert state.fields["Bid Number"].status == "unresolved"
    assert any(event.agent == "critic" for event in events)
    assert output.fields["Bid Number"].value is None


def test_cli_modes_exit_codes_and_do_not_invent_values(tmp_path, monkeypatch):
    code = cli.main(
        [
            "extract-bid",
            "--bid-id",
            "Bid1",
            "--output",
            str(tmp_path),
            "--qdrant-url",
            "https://example.cloud",
        ]
    )
    assert code == 1
    monkeypatch.setattr(
        "rfp.agents.llm.load_llm_config",
        lambda env_file=None: __import__("rfp.agents.llm", fromlist=["LLMConfig"]).LLMConfig(
            "", "", "", ""
        ),
    )
    code = cli.main(
        [
            "extract-bid",
            "--bid-id",
            "Bid1",
            "--output",
            str(tmp_path / "out"),
            "--qdrant-url",
            "http://127.0.0.1:6333",
            "--llm-mode",
            "live",
        ]
    )
    assert code == 1

    def empty_search(request):
        assert request.filters.bid_ids
        return _response([])

    monkeypatch.setattr(
        "rfp.search.wiring.search_service",
        lambda settings: type("Service", (), {"search": staticmethod(empty_search)})(),
    )
    out = tmp_path / "extraction"
    trace = tmp_path / "traces"
    code = cli.main(
        [
            "extract-bid",
            "--bid-id",
            "Bid1",
            "--output",
            str(out),
            "--trace-dir",
            str(trace),
            "--llm-mode",
            "test",
            "--qdrant-url",
            "http://127.0.0.1:6333",
        ]
    )
    assert code == 0
    document = BidExtraction.model_validate_json((out / "Bid1.json").read_text(encoding="utf-8"))
    assert list(document.fields) == list(REQUIRED_FIELDS)
    assert all(field.value is None for field in document.fields.values())
    assert document.validation.not_found == 20
    trace_body = json.loads((trace / "extraction_Bid1.json").read_text(encoding="utf-8"))
    assert trace_body["model"]["provider"] == "fake"
    assert trace_body["bid_uuid"] == str(BID)
    assert "api_key" not in json.dumps(trace_body).casefold()
    assert (
        cli.main(["extract-bid", "--bid-id", "../Bid1", "--output", str(out), "--llm-mode", "test"])
        == 2
    )
