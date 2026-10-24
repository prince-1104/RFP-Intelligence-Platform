"""Phase 9 accuracy rules. Offline and synthetic; no bid's real answers appear here."""

import importlib.util
import json
import uuid
from pathlib import Path

from rfp.agents.extractors import CATALOG, extraction_request, parse_drafts, steps_for
from rfp.agents.llm import SEED, ModelRequest, ModelResponse, OpenAICompatibleModel
from rfp.agents.orchestrator import run_extraction
from rfp.agents.schemas import DraftField, EvidenceHit, ExtractionConfig, SourceRef
from rfp.agents.semantics import (
    bid_number_problems,
    identifier_aliases,
    join_split_emails,
)
from rfp.agents.validator import validate_field
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
LISTING = (
    "Issuing Agency County Water Board Solicitation Type RFP Reference Number 00001234567 "
    "Solicitation Number WB-55821 Title Field Tablets with Cases"
)
FORM = (
    "Purchase Order Request for Proposals (PORFP) Hardware Master Contract Section 1 PORFP "
    "Number: Fixed Price #Q77K1200099 Project Number: WB-55821 PORFP Type:"
)


def _hit(evidence_id, text, file="rfp.pdf", page=1):
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
            )
        ],
    )


def _validate(name, value, cited, others=()):
    hits = [_hit(f"e{index}", text) for index, text in enumerate([cited, *others], start=1)]
    draft = DraftField(name=name, value=value, evidence_ids=["e1"], notes="")
    return validate_field(draft, hits)


def test_bid_number_prefers_the_labelled_solicitation_number():
    assert _validate("Bid Number", "WB-55821", LISTING, [FORM]).status == "accepted"
    assert _validate("Bid Number", "WB-55821", FORM, [LISTING]).status == "accepted"
    porfp = _validate("Bid Number", "Q77K1200099", FORM, [LISTING])
    assert porfp.status == "rejected"
    assert "The evidence labels WB-55821 as the solicitation number" in porfp.reasons
    reference = _validate("Bid Number", "00001234567", LISTING)
    assert reference.status == "rejected"
    assert any("listing reference number" in reason for reason in reference.reasons)


def test_bid_number_without_any_label_is_judged_by_support_alone():
    assert bid_number_problems("AB-12345", ["Request For Proposal AB-12345 Devices"]) == []
    assert bid_number_problems("Q77K1200099", [FORM]) == []


def test_other_labelled_identifiers_become_aliases_and_listing_references_do_not():
    aliases = identifier_aliases("WB-55821", [LISTING, FORM])
    assert aliases == [(1, "PORFP Number: Fixed Price #Q77K1200099")]
    assert identifier_aliases("Q77K1200099", [FORM]) == [(0, "Project Number: WB-55821")]


def _response(text, page):
    citation = Citation(
        occurrence_id=uuid.uuid4(),
        chunk_generation_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="abc",
        bid_id=BID,
        document_id=uuid.uuid4(),
        original_file_name="porfp.pdf",
        relative_path="porfp.pdf",
        document_type=DocumentType.rfp,
        chunk_type="prose",
        page_start=page,
        page_end=page,
        location_known=True,
        normalization_version="1.0",
        chunking_version="1.0",
        source_segments=[],
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


class _Model:
    provider = "fake"
    model = "fake"

    def __init__(self, handler):
        self.handler = handler

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(self.handler(request), "fake", "fake", 5, 5, 1.0)


def _ids(prompt):
    current, found = None, {}
    for line in prompt.splitlines():
        if line.startswith("Field: "):
            current = line.removeprefix("Field: ")
        elif line.startswith("[") and current:
            found.setdefault(current, []).append(line[1 : line.index("]")])
    return found


def test_workflow_keeps_the_solicitation_number_and_cites_the_alias():
    def search(request):
        if "solicitation" in request.query.casefold():
            return _response(FORM, 1)
        return _response("x", 1).model_copy(update={"results": [], "result_count": 0})

    def handler(request):
        ids = _ids(request.user)
        fields = {name: {"value": None, "evidence_ids": [], "notes": "Not found"} for name in ids}
        if "Bid Number" in ids:
            fields["Bid Number"] = {"value": "WB-55821", "evidence_ids": ids["Bid Number"][:1]}
        return json.dumps({"fields": fields})

    output, _state, _events = run_extraction(
        "BidX", BID, search, _Model(handler), ExtractionConfig(max_retries=0, concurrency=1)
    )
    field = output.fields["Bid Number"]
    assert field.value == "WB-55821"
    assert "PORFP Number: Fixed Price #Q77K1200099 (porfp.pdf, page 1)" in field.notes


TERM = (
    "LENGTH OF CONTRACT The term of this proposal shall be for a three (3) year agreement with "
    "two (2) successive one (1) year extensions not to exceed five (5) years total."
)
TERM_TABLE = (
    "1.2 Terms Initial Term: 3 Renewal 1: 1 Renewal 2: 1 Any contract may be extended not to "
    "exceed a total of 5 years(s)."
)


def test_term_keeps_renewals_and_accepts_a_faithful_paraphrase():
    truncated = _validate("Term of Bid", "three (3) year agreement", TERM)
    assert truncated.status == "rejected"
    assert any("renewal or extension" in reason for reason in truncated.reasons)
    paraphrase = "3 years with 2 renewals of 1 year each, not to exceed a total of 5 years"
    assert _validate("Term of Bid", paraphrase, TERM_TABLE).status == "accepted"
    wrong = "4 years with 2 renewals of 1 year each, not to exceed a total of 5 years"
    assert _validate("Term of Bid", wrong, TERM_TABLE).status == "rejected"
    short = "3 years with 2 renewals of 1 year each"
    assert _validate("Term of Bid", short, TERM_TABLE).status == "accepted"
    no_renewal = "The term of this agreement is three (3) years from award."
    assert _validate("Term of Bid", "three (3) years", no_renewal).status == "accepted"


def test_identifiers_are_not_accepted_as_paraphrases():
    assert _validate("Bid Number", "WB 55821 solicitation", LISTING).status == "rejected"


def test_documentation_list_with_trailing_period_and_checkbox_instruction():
    text = (
        "8. The Contractor must provide a Mercury Affidavit. Contract Affidavit A. AUTHORITY "
        "The business named above is a (check applicable box): \uf06fdomestic \uf06fforeign"
    )
    listed = "Contract Affidavit and Mercury Affidavit."
    assert _validate("Any Additional Documentation Required", listed, text).status == "accepted"
    checkbox = "The business named above is a (check applicable box)"
    result = _validate("Any Additional Documentation Required", checkbox, text)
    assert result.status == "rejected"
    assert any("checkbox instruction" in reason for reason in result.reasons)


def test_mfg_accepts_a_reseller_qualification_without_a_named_manufacturer():
    text = (
        "Manufacturer or Authorized Reseller Qualifications For proposing vendors who are "
        "resellers, a copy of all factory-authorized repair and maintenance certifications "
        "must be submitted with each proposal."
    )
    value = (
        "For proposing vendors who are resellers, a copy of all factory-authorized repair and "
        "maintenance certifications must be submitted with each proposal."
    )
    assert _validate("MFG for Registration", value, text).status == "accepted"
    hits = {"MFG for Registration": [_hit("mfg-for-registration-1", text)]}
    request = extraction_request(
        "commercial", hits, ["MFG for Registration"], 0, {}, ExtractionConfig()
    )
    assert "authorized-reseller qualification" in request.user


def test_title_rejects_a_document_type_heading():
    heading = "Purchase Order Request for Proposals (PORFP) Hardware Master Contract"
    result = _validate("Title", heading, FORM)
    assert result.status == "rejected"
    assert any("document-type heading" in reason for reason in result.reasons)
    assert _validate("Title", "Field Tablets with Cases", LISTING).status == "accepted"
    named = "Request for Proposal for Field Tablets"
    assert _validate("Title", named, "Request for Proposal for Field Tablets").status == (
        "accepted"
    )


def test_product_must_carry_the_quantity_a_product_table_states():
    table = "Product Name Product Description Model # Qty 25 AB-77 Acme Slate 10 tablets"
    bare = _validate("Product", "tablets", table)
    assert bare.status == "rejected"
    assert any("omits the quantity" in reason for reason in bare.reasons)
    assert _validate("Product", "25 Acme Slate 10 tablets", table).status == "accepted"
    prose = "The district seeks tablets for field staff."
    assert _validate("Product", "tablets", prose).status == "accepted"


def test_summary_must_not_copy_a_passage_and_its_prompt_asks_for_own_sentences():
    passage = (
        "The County Water Board requests proposals for 25 field tablets with protective cases. "
        "Proposals are due 06/10/2024 through the online portal. Delivery within 45 days of "
        "award. The contractor must be an authorized reseller and provide a warranty."
    )
    copied = _validate("Bid Summary", passage, passage)
    assert copied.status == "rejected"
    assert any("copies one passage" in reason for reason in copied.reasons)
    summary = (
        "The County Water Board seeks 25 field tablets with protective cases from an authorized "
        "reseller. Proposals are due 06/10/2024 via the online portal, and delivery is required "
        "within 45 days of award."
    )
    assert _validate("Bid Summary", summary, passage).status == "accepted"
    hits = {"Bid Summary": [_hit("bid-summary-1", passage)]}
    request = extraction_request("summary", hits, ["Bid Summary"], 0, {}, ExtractionConfig())
    assert "own sentences" in request.user
    assert "Copy values verbatim" not in request.user


def test_split_email_domains_are_joined_only_when_unambiguous():
    assert join_split_emails("Fax: pdoe@agency.state.xy .us Mail") == (
        "Fax: pdoe@agency.state.xy.us Mail"
    )
    assert join_split_emails("Email: jroe@agency.state. xy.us Fax") == (
        "Email: jroe@agency.state.xy.us Fax"
    )
    sentence = "Write to pdoe@agency.gov. and then call."
    assert join_split_emails(sentence) == sentence
    text = "Agency POC Name: Pat Doe Agency POC Fax: pdoe@agency.state.xy .us"
    contact = _validate("contact_info", "Pat Doe, pdoe@agency.state.xy.us", text)
    assert contact.status == "accepted"
    other = _validate("contact_info", "Pat Doe, pdoe@agency.state.xy.uk", text)
    assert other.status == "rejected"


def _verifier():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_extraction.py"
    spec = importlib.util.spec_from_file_location("verify_extraction", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verifier_scores_a_split_email_as_verbatim():
    verifier = _verifier()
    text = "Agency POC Name: Pat Doe Agency POC Fax: pdoe@agency.state.xy .us"
    assert verifier.support("pdoe@agency.state.xy.us", text) == ("verbatim", 1.0)
    assert verifier.support("pdoe@agency.state.xy.uk", text)[0] != "verbatim"


def test_bid_number_must_be_shared_by_the_listing_and_the_solicitation_documents():
    listing = "00001234567 County Water Board RFP WB-55821 Field Tablets with Cases 21401"
    form = '["", "PORFP Number:", "", "#Q77K1200099\\nProject Number: WB-55821", null]'
    hits = [_hit("e1", form), _hit("e2", listing, file="listing.html", page=None)]
    hits[1] = hits[1].model_copy(
        update={
            "sources": [
                hits[1]
                .sources[0]
                .model_copy(
                    update={
                        "document_type": DocumentType.bid_page,
                        "page_end": None,
                        "html_element_id": "g_1",
                    }
                )
            ]
        }
    )

    def check(value, cited):
        draft = DraftField(name="Bid Number", value=value, evidence_ids=[cited], notes="")
        return validate_field(draft, hits)

    assert check("WB-55821", "e2").status == "accepted"
    form_only = check("Q77K1200099", "e1")
    assert form_only.status == "rejected"
    assert any("share WB-55821" in reason for reason in form_only.reasons)
    assert check("00001234567", "e2").status == "rejected"
    assert identifier_aliases("WB-55821", [form]) == [(0, "PORFP Number: #Q77K1200099")]


def test_payment_terms_reject_a_subcontractor_payment_clause():
    text = (
        "All Prime vendors must pay all submitted invoices, including retainage to "
        "subcontractors, suppliers, or entities within 10 days of receiving payment from the "
        "district. Payment will be made net 30 days after receipt of a correct invoice."
    )
    clause = (
        "All Prime vendors must pay all submitted invoices, including retainage to "
        "subcontractors, suppliers, or entities within 10 days of receiving payment from the "
        "district."
    )
    result = _validate("Payment Terms", clause, text)
    assert result.status == "rejected"
    assert any("subcontractors" in reason for reason in result.reasons)
    buyer = "Payment will be made net 30 days after receipt of a correct invoice."
    assert _validate("Payment Terms", buyer, text).status == "accepted"


def test_product_quantity_ignores_digits_inside_codes():
    table = "Need laptops to accommodate MD529 employees. Product Name Model # Qty 30 Acme Slate"
    assert _validate("Product", "laptops to accommodate MD529 employees", table).status == (
        "rejected"
    )
    assert _validate("Product", "30 Acme Slate", table).status == "accepted"


def test_retry_prompt_shows_the_rejected_value_and_title_retry_searches_the_listing():
    hits = {"Title": [_hit("title-1", FORM)]}
    reasons = {"Title": ["A document-type heading is not the solicitation title"]}
    heading = "Purchase Order Request for Proposals (PORFP) Hardware Master Contract"
    request = extraction_request(
        "identity", hits, ["Title"], 1, reasons, ExtractionConfig(), {"Title": heading}
    )
    assert f"Rejected value: {heading!r}" in request.user
    first = extraction_request("identity", hits, ["Title"], 0, {}, ExtractionConfig())
    assert "Rejected value" not in first.user
    steps = steps_for(CATALOG["Title"], 1, reasons["Title"])
    assert steps[0].doc_types == (DocumentType.bid_page,)
    assert DocumentType.bid_page not in steps_for(CATALOG["Title"], 0, [])[0].doc_types


def test_json_mode_and_seed_only_for_extraction_requests_on_openai():
    hits = {"Title": [_hit("title-1", LISTING)]}
    extraction = extraction_request("identity", hits, ["Title"], 0, {}, ExtractionConfig())
    qa_like = ModelRequest(purpose="qa:answer", system="Reply with JSON only", user="q")

    def model(provider):
        return OpenAICompatibleModel(provider, "m", "not-a-key", "https://example.invalid", 5, 10)

    body = model("openai").payload(extraction)
    assert body["response_format"] == {"type": "json_object"} and body["seed"] == SEED
    assert body["temperature"] == 0
    assert "response_format" not in model("openai").payload(qa_like)
    assert "seed" not in model("openai_compatible").payload(extraction)


def _product_run(replies):
    table = "Product Name Model # Qty 25 Acme Slate tablets"
    calls = iter(replies)

    def search(request):
        if "product name" in request.query.casefold():
            return _response(table, 1)
        return _response("x", 1).model_copy(update={"results": [], "result_count": 0})

    def handler(request):
        ids = _ids(request.user)
        fields = {name: {"value": None, "evidence_ids": [], "notes": "Not found"} for name in ids}
        if "Product" in ids:
            value = next(calls)
            if value is not None:
                fields["Product"] = {"value": value, "evidence_ids": ids["Product"][:1]}
        return json.dumps({"fields": fields})

    output, _state, _events = run_extraction(
        "BidX", BID, search, _Model(handler), ExtractionConfig(max_retries=2, concurrency=1)
    )
    return output.fields["Product"]


def test_incomplete_supported_value_is_kept_with_a_note_when_retries_do_not_improve():
    kept = _product_run(["tablets", "tablets", None])
    assert kept.value == "tablets"
    assert kept.notes.startswith("Possibly incomplete: Product omits the quantity")
    assert [source.page for source in kept.sources] == [1]
    better = _product_run(["tablets", "25 Acme Slate tablets", None])
    assert better.value == "25 Acme Slate tablets"
    assert "incomplete" not in better.notes


def test_a_wrong_kind_of_value_is_never_kept_as_a_fallback():
    def search(request):
        if "title" in request.query.casefold():
            return _response(FORM, 1)
        return _response("x", 1).model_copy(update={"results": [], "result_count": 0})

    heading = "Purchase Order Request for Proposals (PORFP) Hardware Master Contract"

    def handler(request):
        ids = _ids(request.user)
        fields = {name: {"value": None, "evidence_ids": [], "notes": "Not found"} for name in ids}
        if "Title" in ids:
            fields["Title"] = {"value": heading, "evidence_ids": ids["Title"][:1]}
        return json.dumps({"fields": fields})

    output, _state, _events = run_extraction(
        "BidX", BID, search, _Model(handler), ExtractionConfig(max_retries=1, concurrency=1)
    )
    assert output.fields["Title"].value is None
    assert "document-type heading" in output.fields["Title"].notes


def test_verbatim_value_with_a_wrong_citation_is_pointed_at_the_shown_passage():
    specs = "Acme Slate 10 tablet, 8 GB RAM, 128 GB storage"
    table = "Product Name Model # Qty 25 Acme Slate 10"

    def search(request):
        query = request.query.casefold()
        if "model number" in query:
            return _response(table, 3)
        if "technical specifications" in query:
            return _response(specs, 7)
        return _response("x", 1).model_copy(update={"results": [], "result_count": 0})

    def handler(request):
        ids = _ids(request.user)
        fields = {name: {"value": None, "evidence_ids": [], "notes": "Not found"} for name in ids}
        if "Model_no" in ids:
            wrong = ids["Product Specification"][0]
            fields["Model_no"] = {"value": "Acme Slate 10", "evidence_ids": ["model-no-9"]}
            fields["Part_no"] = {"value": "Acme", "evidence_ids": [wrong]}
        return json.dumps({"fields": fields})

    output, _state, events = run_extraction(
        "BidX", BID, search, _Model(handler), ExtractionConfig(max_retries=0, concurrency=1)
    )
    model = output.fields["Model_no"]
    assert model.value == "Acme Slate 10"
    assert [source.page for source in model.sources] == [3]
    assert any("citation repaired for Model_no" in (e.decision or "") for e in events)
    assert output.fields["Part_no"].value is None


def test_placeholder_parts_of_an_object_reply_are_dropped():
    reply = {
        "fields": {
            "Product Specification": {
                "value": {"cpu": "Core 5", "battery": "Not found in documents", "ram": "16 GB"},
                "evidence_ids": ["product-specification-1"],
            }
        }
    }
    drafts = parse_drafts(["Product Specification"], json.dumps(reply))
    assert drafts["Product Specification"].value == "Core 5, 16 GB"


def test_trace_records_the_rejected_value():
    def search(request):
        if "product name" in request.query.casefold():
            return _response("Product Name Model # Qty 25 Acme Slate tablets", 1)
        return _response("x", 1).model_copy(update={"results": [], "result_count": 0})

    def handler(request):
        ids = _ids(request.user)
        fields = {name: {"value": None, "evidence_ids": [], "notes": "Not found"} for name in ids}
        if "Product" in ids:
            fields["Product"] = {"value": "tablets", "evidence_ids": ids["Product"][:1]}
        return json.dumps({"fields": fields})

    _output, _state, events = run_extraction(
        "BidX", BID, search, _Model(handler), ExtractionConfig(max_retries=0, concurrency=1)
    )
    checks = [e for e in events if e.agent == "validator" and e.field == "Product"]
    assert checks and checks[0].decision == "rejected value: tablets"
