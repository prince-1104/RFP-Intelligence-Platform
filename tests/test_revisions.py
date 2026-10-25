"""Requirement-level addendum reconciliation (`revisions.py`) in extraction, Q&A and comparison.

Passages are synthetic; Bid1-style text mirrors the structure of a vendor Q&A addendum. Models are
scripted fakes, so these tests are not evidence of live answer quality.
"""

import json
import uuid
from datetime import date

import pytest
from test_compare_qa import BID2_WARRANTY, BidSearch, comparison
from test_qa_grounding import (
    ABSTAIN,
    BID1,
    BID2,
    BIDNET,
    CLOSING,
    FINAL,
    PORFP,
    Model,
    Search,
    _result,
    claims,
)

from rfp.agents.compare import compare, resolve_bids
from rfp.agents.errors import ModelError
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.orchestrator import _change_note, run_extraction
from rfp.agents.qa import ModelReply, ask, check_reply
from rfp.agents.reconciliation import reconcile_due_date, reconcile_field
from rfp.agents.revisions import classify, detect, question_terms, reconcile, to_changes
from rfp.agents.schemas import (
    AddendumChange,
    BidExtraction,
    DraftField,
    EvidenceHit,
    ExtractionConfig,
    OutputSource,
    SourceRef,
)
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import (
    ProfileInfo,
    QueryDiagnostics,
    SearchFilters,
    SearchResponse,
)
from rfp.search.errors import SearchServiceError

ADD1 = ("Addendum 1.pdf", DocumentType.addendum)
ADD2 = ("Addendum 2.pdf", DocumentType.addendum)
RFP_FILE = "rfp.pdf"

DELIVERY_BASE = "9. Delivery within 45 days after award."
DELIVERY_60 = "The delivery requirement is amended to 60 days after award."
DELIVERY_90 = "The delivery requirement is amended to 90 days after award."
DELIVERY_90_ORDERED = (
    "This addendum supersedes Addendum 1. The delivery requirement is amended to 90 days after "
    "award."
)
PREBID_BASE = "Bidders must attend the mandatory pre-bid meeting on June 10."
PREBID_REMOVED = "The mandatory pre-bid meeting requirement is deleted."
FORM_ADDED = "A notarized Form 1295 is added to the required submittals."
PAYMENT_BASE = "Payment terms are Net 30 after receipt of a correct invoice."
UNCHANGED = "Payment remains Net 30 as previously stated. All other terms remain unchanged."
DUE_ONLY = "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST."
INSTALL_BASE = "All deliveries must include white glove installation and asset tag reporting."

# Bid1-style: the solicitation lists device tiers; a Q&A addendum restates the warranty minimums.
WARRANTY_TABLET = "Student Tablet\nWarranty: 1-year minimum"
WARRANTY_WINDOWS = "Staff Windows Laptop\nWarranty: 3-year minimum"
WARRANTY_QA = (
    "ADDENDUM No. 1\n"
    "7. Reference Page 34/40: Additional Warranty. How would the district like us to explain "
    "this point?\n"
    "Answer:\n"
    "As a minimum requirement, the district is asking for a one-year warranty for\n"
    "student Chromebooks and three years for student and staff Windows laptops.\n"
    "8. Does the district prefer warranties underwritten by insurance?\n"
    "Answer:\n"
    "The district will ask all warranties to be made by OEM.\n"
    "9. Are you ok with a higher USB version on the display?\n"
    "Answer:\n"
    "A higher specification is permissible.\n"
)
WARRANTY_QA_REPEAT = (
    "29. What is the warranty for student laptops?\n"
    "Answer:\n"
    "As a minimum requirement, the district is asking for a one-year warranty for student\n"
    "Chromebooks and three years for student and staff Windows laptops.\n"
)
MINIMUM = (
    "As a minimum requirement, the district is asking for a one-year warranty for student "
    "Chromebooks and three years for student and staff Windows laptops."
)
OEM = "The district will ask all warranties to be made by OEM."
WARRANTY_TERMS = ("warranty", "warranties", "specification", "usb")


def hit(evidence_id, doc, text, page=1, addendum=None, bid=BID1):
    name, doc_type = doc
    return EvidenceHit(
        evidence_id=evidence_id,
        bid_id=bid,
        text=text,
        rank=1,
        mode="hybrid_rerank",
        final_score=0.5,
        sources=[
            SourceRef(
                file=name,
                relative_path=name,
                document_type=doc_type,
                addendum_number=addendum,
                page=page,
                page_end=page,
                bid_id=bid,
            )
        ],
    )


RFP = (RFP_FILE, DocumentType.rfp)


def draft(name, value, *ids):
    return DraftField(name=name, value=value, evidence_ids=list(ids), notes="")


# 1. No addendum


def test_no_addendum_leaves_the_original_requirement_unchanged():
    hits = [hit("e1", RFP, DELIVERY_BASE)]
    history = reconcile("Delivery Date", ["delivery"], hits, BID1)
    assert history.status == "unchanged" and history.revisions == []
    assert [s.quote for s in history.active_baseline] == [DELIVERY_BASE]
    decision = reconcile_field("Delivery Date", draft("Delivery Date", DELIVERY_BASE, "e1"), hits)
    assert decision.all_changes == () and decision.reject_reason is None


# 2. One addendum replaces a value


def test_one_addendum_replacement_rejects_the_stale_value_and_accepts_the_revision():
    hits = [hit("e1", RFP, DELIVERY_BASE, page=2), hit("a1", ADD1, DELIVERY_60, addendum=1)]
    stale = reconcile_field("Delivery Date", draft("Delivery Date", DELIVERY_BASE, "e1"), hits)
    assert stale.reject_reason and stale.reject_reason.startswith("Addendum 1 replaces")
    current = reconcile_field("Delivery Date", draft("Delivery Date", DELIVERY_60, "a1"), hits)
    assert current.reject_reason is None
    (change,) = current.all_changes
    assert (change.change_type, change.status, change.conflict) == ("replace", "applied", False)
    assert change.original_value == DELIVERY_BASE and change.revised_value == DELIVERY_60
    assert change.addendum_number == 1 and change.addendum_date is None
    assert [(s.file, s.page) for s in change.sources] == [("Addendum 1.pdf", 1), (RFP_FILE, 2)]
    assert current.history and current.history.active_baseline == []


# 3. Two ordered addenda: the latest wins


def test_two_ordered_addenda_apply_the_one_that_supersedes():
    hits = [
        hit("e1", RFP, DELIVERY_BASE),
        hit("a1", ADD1, DELIVERY_60, addendum=1),
        hit("a2", ADD2, DELIVERY_90_ORDERED, addendum=2),
    ]
    history = reconcile("Delivery Date", ["delivery"], hits, BID1)
    by_number = {r.addendum_number: r for r in history.revisions}
    assert by_number[2].status == "applied" and by_number[2].orderable
    assert by_number[1].status == "superseded" and "addendum 2" in by_number[1].reason.casefold()
    assert history.status == "revised"
    decision = reconcile_field("Delivery Date", draft("Delivery Date", "90 days", "a2"), hits)
    assert decision.reject_reason is None
    assert [c.status for c in decision.all_changes] == ["superseded", "applied"]


def test_issue_dates_from_metadata_order_competing_addenda():
    hits = [
        hit("a1", ADD1, DELIVERY_60, addendum=1),
        hit("a2", ADD2, DELIVERY_90, addendum=2),
    ]
    dates = {"Addendum 1.pdf": date(2024, 6, 1), "Addendum 2.pdf": date(2024, 5, 1)}
    history = reconcile("Delivery Date", ["delivery"], hits, BID1, dates)
    by_number = {r.addendum_number: r for r in history.revisions}
    # The lower-numbered addendum was issued later, so it wins; numbers are not chronology.
    assert by_number[1].status == "applied" and by_number[2].status == "superseded"
    assert by_number[1].addendum_date == date(2024, 6, 1)


# 4. Missing chronology keeps the conflict


def test_unordered_competing_changes_stay_unresolved():
    hits = [
        hit("e1", RFP, DELIVERY_BASE),
        hit("a1", ADD1, DELIVERY_60, addendum=1),
        hit("a2", ADD2, DELIVERY_90, addendum=2),
    ]
    history = reconcile("Delivery Date", ["delivery"], hits, BID1)
    assert history.status == "conflict"
    assert {r.status for r in history.revisions} == {"conflict"}
    assert all(not r.orderable for r in history.revisions)
    decision = reconcile_field("Delivery Date", draft("Delivery Date", DELIVERY_90, "a2"), hits)
    assert decision.reject_reason and "conflicting addendum changes" in decision.reject_reason
    assert all(c.conflict and c.revised_value is None for c in decision.all_changes)
    assert {c.addendum_number for c in decision.all_changes} == {1, 2}


# 5. A later document that does not touch a requirement does not erase it


def test_a_later_addendum_on_another_subject_keeps_the_requirement():
    hits = [hit("e1", RFP, INSTALL_BASE), hit("a2", ADD2, DUE_ONLY, addendum=2)]
    history = reconcile("Installation", ["installation", "white glove", "asset tag"], hits, BID1)
    assert history.status == "unchanged" and history.revisions == []
    assert [s.quote for s in history.active_baseline] == [INSTALL_BASE]


# 6. A removed requirement is not active


def test_removed_requirement_is_not_presented_as_active():
    hits = [hit("e1", RFP, PREBID_BASE), hit("a1", ADD1, PREBID_REMOVED, addendum=1)]
    history = reconcile("Pre Bid Meeting", ["pre-bid"], hits, BID1)
    (revision,) = history.revisions
    assert (revision.change_type, revision.status) == ("remove", "applied")
    assert history.status == "revised" and history.active_baseline == []
    (change,) = to_changes("Pre Bid Meeting", history)
    assert change.change_type == "remove" and change.revised_value is None
    assert change.quote == PREBID_REMOVED
    assert _change_note(change) == f'Addendum 1 removes: "{PREBID_REMOVED}".'
    stale = reconcile_field("Pre Bid Meeting", draft("Pre Bid Meeting", PREBID_BASE, "e1"), hits)
    assert stale.reject_reason and "removes this requirement" in stale.reject_reason


# 7. An added requirement is kept alongside unrelated ones


def test_added_requirement_is_recorded_without_touching_unrelated_requirements():
    hits = [
        hit("e1", RFP, PAYMENT_BASE),
        hit("e2", RFP, "Bidders must submit a signed W-9 form."),
        hit("a1", ADD1, FORM_ADDED, addendum=1),
    ]
    docs = reconcile_field(
        "Any Additional Documentation Required",
        draft("Any Additional Documentation Required", "Bidders must submit a signed W-9", "e2"),
        hits,
    )
    (change,) = docs.all_changes
    assert change.change_type == "add" and change.status == "applied"
    assert docs.reject_reason is None
    assert docs.history and [s.evidence_id for s in docs.history.active_baseline] == ["e2"]
    payment = reconcile_field("Payment Terms", draft("Payment Terms", PAYMENT_BASE, "e1"), hits)
    assert payment.all_changes == () and payment.reject_reason is None


# 8. A clarification is not a replacement


def test_clarification_is_distinguished_from_replacement_and_removal():
    assert classify(MINIMUM, qa=True) == "clarify"
    assert classify("A local office is not required.", qa=True) == "clarify"
    assert classify("A local office is not required.", qa=False) == "remove"
    assert classify("The local office requirement is deleted.", qa=True) == "remove"
    assert classify(DELIVERY_60, qa=True) == "replace"
    assert classify(FORM_ADDED, qa=False) == "add"
    assert classify("Proposals are reviewed by staff.", qa=False) == "unknown"
    hits = [hit("e1", RFP, WARRANTY_WINDOWS), hit("a1", ADD1, WARRANTY_QA, addendum=1)]
    decision = reconcile_field(
        "Product Specification",
        draft("Product Specification", "Warranty: 3-year minimum", "e1"),
        hits,
    )
    assert decision.reject_reason is None
    assert {c.change_type for c in decision.all_changes} == {"clarify"}
    assert all(c.original_value is None for c in decision.all_changes)


# 9. Conflicts stay visible with citations (single-bid Q&A)


def test_qa_conflicting_addenda_are_reported_with_both_citations():
    search = Search(
        [_result(BID1, FINAL, DELIVERY_BASE, page=4)],
        addenda=[
            _result(BID1, ADD1, DELIVERY_60, addendum=1),
            _result(BID1, ADD2, DELIVERY_90, page=2, addendum=2),
        ],
    )
    one_sided = claims("answered", (DELIVERY_90, [("90 days", DELIVERY_90[:-1])]))
    both = claims(
        "conflict",
        (DELIVERY_60, [("60 days", DELIVERY_60[:-1])]),
        (DELIVERY_90, [("90 days", DELIVERY_90[:-1])]),
    )
    model = Model(one_sided, both)
    response, _ = ask("Bid1", BID1, "What is the delivery requirement?", search, model)
    assert "Addendum not addressed" in model.calls[1].user
    assert "report status conflict" in model.calls[1].user
    assert response.status == "conflict"
    assert {(c.document, c.addendum_number) for c in response.citations} == {
        ("Addendum 1.pdf", 1),
        ("Addendum 2.pdf", 2),
    }


# 10. Invalid addendum claims are rejected


def test_invalid_addendum_claims_and_sources_are_rejected():
    rfp_amend = hit("e1", RFP, "Section 4 is amended to require 90 days delivery.")
    other_bid = hit("x1", ADD1, DELIVERY_60, addendum=1, bid=BID2)
    mention = hit("a1", ADD1, "Delivery questions were discussed at the meeting.", addendum=1)
    history = reconcile("Delivery Date", ["delivery"], [rfp_amend, other_bid, mention], BID1)
    assert [r.change_type for r in history.revisions] == ["unknown"]
    assert history.revisions[0].status == "not_applied" and not history.revisions[0].material
    assert history.status == "unchanged"

    base = hit("E1", RFP, DELIVERY_BASE)
    reply = ModelReply.model_validate(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "Addendum 2 states delivery within 45 days after award.",
                    "citations": [
                        {"evidence_id": "E1", "quote": "Delivery within 45 days after award"}
                    ],
                }
            ],
        }
    )
    checked = check_reply("What is the delivery requirement?", reply, [base])
    assert checked.claims == []
    assert any("names addendum 2 but cites no passage from it" in r for r in checked.reasons)


# 11. Due-date regression


def test_due_date_reconciliation_is_unchanged_and_tagged():
    base = hit("e1", RFP, "Solicitation Due 27-JUN-2024 14:00:00", page=2)
    add2 = hit("a2", ADD2, DUE_ONLY, addendum=2)
    decision = reconcile_due_date(
        draft("Due Date", "July 9, 2024 at 2:00 PM CST", "a2"), [base, add2]
    )
    assert decision.reject_reason is None
    change = decision.change
    assert change is not None and change.revised_value == "July 9, 2024 at 2:00 PM CST"
    assert change.original_value == "27-JUN-2024 14:00:00"
    assert (change.change_type, change.status) == ("replace", "applied")
    stale = reconcile_field(
        "Due Date", draft("Due Date", "27-JUN-2024 14:00:00", "e1"), [base, add2]
    )
    assert stale.reject_reason == "addendum states a different due date than the draft"
    other = hit(
        "a3", ("Addendum 3.pdf", DocumentType.addendum), DUE_ONLY.replace("9", "16"), addendum=3
    )
    conflict = reconcile_due_date(draft("Due Date", "July 9", "a2"), [base, add2, other])
    assert conflict.reject_reason == "conflicting addendum due dates"
    assert conflict.change and conflict.change.status == "conflict" and conflict.change.conflict
    unchanged = hit("a4", ADD1, UNCHANGED, addendum=1)
    payment = reconcile_field("Payment Terms", draft("Payment Terms", "Net 30", "e1"), [unchanged])
    assert payment.change is None and payment.reject_reason is None
    # Old records without the new keys still load.
    legacy = {
        "field": "Due Date",
        "original_value": None,
        "revised_value": "x",
        "addendum_number": 2,
        "sources": [{"file": "a.pdf", "page": 1}],
        "explanation": "e",
    }
    assert AddendumChange.model_validate(legacy).change_type == "replace"


# 12. Bid1-style warranty reconciliation without hardcoding


def test_bid1_style_warranty_clarification_reconciles_to_device_specific_terms():
    hits = [
        hit("e1", RFP, WARRANTY_TABLET, page=5),
        hit("e2", RFP, WARRANTY_WINDOWS, page=6),
        hit("a1", ADD1, WARRANTY_QA, page=1, addendum=1),
        hit("a2", ADD1, WARRANTY_QA_REPEAT, page=3, addendum=1),
    ]
    history = reconcile("Product Specification", WARRANTY_TERMS, hits, BID1)
    material = {r.statement: r for r in history.material}
    assert set(material) == {MINIMUM, OEM}
    minimum = material[MINIMUM]
    assert minimum.change_type == "clarify" and minimum.status == "applied"
    assert {(s.file, s.page) for s in minimum.sources} == {
        ("Addendum 1.pdf", 1),
        ("Addendum 1.pdf", 3),
    }
    assert "Chromebooks" in minimum.statement and "Windows laptops" in minimum.statement
    assert history.status == "amended"
    # The original device-tier terms stay active: a clarification retires nothing.
    assert {s.evidence_id for s in history.active_baseline} == {"e1", "e2"}
    permissive = [r for r in history.revisions if "permissible" in r.statement]
    assert permissive and not permissive[0].material


# 13. Single-bid Q&A includes the addendum change


def _warranty_search():
    return Search(
        [
            _result(BID1, FINAL, WARRANTY_TABLET, page=5),
            _result(BID1, FINAL, WARRANTY_WINDOWS, page=6),
        ],
        addenda=[_result(BID1, ADD1, WARRANTY_QA, page=1, addendum=1)],
    )


ORIGINAL_CLAIM = ("Warranty: 3-year minimum.", [("3-year minimum", "Warranty: 3-year minimum")])
MINIMUM_CLAIM = (MINIMUM, [("one-year warranty", MINIMUM[:-1])])
OEM_CLAIM = (OEM, [("made by OEM", OEM[:-1])])


def test_single_bid_answer_must_cite_the_addendum_revision():
    model = Model(
        claims("answered", ORIGINAL_CLAIM),
        claims("answered", ORIGINAL_CLAIM, MINIMUM_CLAIM, OEM_CLAIM),
    )
    response, trace = ask(
        "Bid1", BID1, "What warranty is required for the devices?", _warranty_search(), model
    )
    assert "Addendum statements in these passages" in model.calls[0].user
    assert "Addendum not addressed" in model.calls[1].user
    assert response.status == "answered" and response.validation.attempts == 2
    assert {c.document for c in response.citations} == {FINAL[0], "Addendum 1.pdf"}
    assert "one-year warranty for student Chromebooks" in response.answer
    assert not any("does not cite" in item for item in response.limitations)
    assert any(e.task == "addendum_revisions" for e in trace.events)


def test_an_answer_that_never_cites_the_addendum_carries_a_limitation():
    model = Model(claims("answered", ORIGINAL_CLAIM))
    response, _ = ask(
        "Bid1", BID1, "What warranty is required for the devices?", _warranty_search(), model
    )
    assert response.status == "answered" and len(model.calls) == 2
    (limitation,) = [item for item in response.limitations if "does not cite" in item]
    assert "Addendum 1 (clarify, applied)" in limitation and "made by OEM" in limitation


def test_questions_no_addendum_touches_see_unchanged_evidence():
    search = Search(
        [_result(BID1, FINAL, PAYMENT_BASE, page=7)],
        addenda=[_result(BID1, ADD1, WARRANTY_QA, addendum=1)],
    )
    model = Model(
        claims("answered", ("Payment terms are Net 30.", [("Net 30", "Payment terms are Net 30")]))
    )
    response, _ = ask("Bid1", BID1, "What are the payment terms?", search, model)
    assert "Addendum statements" not in model.calls[0].user
    assert "ADDENDUM No. 1" not in model.calls[0].user
    assert response.evidence_count == 1 and len(model.calls) == 1


# 14. Cross-bid Q&A resolves each bid independently


def _compare_search():
    return BidSearch(
        [
            _result(BID1, FINAL, WARRANTY_WINDOWS, page=6),
            _result(BID1, ADD1, WARRANTY_QA, page=1, addendum=1),
            _result(BID2, PORFP, BID2_WARRANTY, page=4),
            _result(BID2, BIDNET, CLOSING, html="g_101"),
        ]
    )


BID2_CLAIM = (
    "warranty period",
    BID2_WARRANTY,
    [("B", "5-year", BID2_WARRANTY[:-1])],
)
BID1_ORIGINAL = (
    "warranty period",
    "Warranty: 3-year minimum.",
    [("A", "3-year", "Warranty: 3-year minimum")],
)
BID1_MINIMUM = ("warranty period", MINIMUM, [("A", "one-year warranty", MINIMUM[:-1])])
BID1_OEM = ("warranty provider", OEM, [("A", "made by OEM", OEM[:-1])])


def test_comparison_reconciles_each_bid_against_its_own_addenda():
    model = Model(
        comparison(("Bid1", "answered", [BID1_ORIGINAL]), ("Bid2", "answered", [BID2_CLAIM])),
        comparison(
            ("Bid1", "answered", [BID1_ORIGINAL, BID1_MINIMUM, BID1_OEM]), ("Bid2", "not_found", [])
        ),
    )
    response, _ = compare(
        resolve_bids(["Bid1", "Bid2"]),
        "Compare the warranty requirements of both bids.",
        _compare_search(),
        model,
    )
    section_b = model.calls[0].user.split("=== Section B")[1]
    section_a = model.calls[0].user.split("=== Section B")[0]
    assert "Addendum statements" in section_a and "Addendum statements" not in section_b
    assert "Bid1: Addendum not addressed" in model.calls[1].user
    assert "Bid2: Addendum" not in model.calls[1].user
    bid1, bid2 = response.findings
    assert {c.document for c in bid1.citations} == {FINAL[0], "Addendum 1.pdf"}
    assert bid1.validation.attempts == 2
    assert bid2.validation.attempts == 1 and {c.document for c in bid2.citations} == {PORFP[0]}
    assert not any("Addendum" in item for item in bid2.limitations)


# 15. Another bid's citation cannot validate a reconciled claim


def test_cross_bid_citation_cannot_support_an_addendum_claim():
    borrowed = ("warranty provider", BID2_WARRANTY, [("B", "5-year", BID2_WARRANTY[:-1])])
    model = Model(comparison(("Bid1", "answered", [borrowed]), ("Bid2", "answered", [BID2_CLAIM])))
    response, _ = compare(
        resolve_bids(["Bid1", "Bid2"]),
        "Compare the warranty requirements of both bids.",
        _compare_search(),
        model,
    )
    bid1 = response.findings[0]
    assert bid1.status == "not_found"
    assert any(c.reason == "cross_bid" for c in bid1.validation.checks)
    foreign = [hit("x1", ADD1, DELIVERY_60, addendum=1, bid=BID2)]
    assert reconcile("Delivery Date", ["delivery"], foreign, BID1).revisions == []
    mixed = hit("x2", ADD1, DELIVERY_60, addendum=1)
    mixed.sources[0] = mixed.sources[0].model_copy(update={"bid_id": BID2})
    assert detect("Delivery Date", ["delivery"], [mixed]) == []


# 16. Missing metadata


def test_missing_addendum_number_and_dates_never_invent_an_order():
    hits = [
        hit("a1", ADD1, DELIVERY_60, addendum=None),
        hit("a2", ADD2, DELIVERY_90, addendum=2),
    ]
    history = reconcile("Delivery Date", ["delivery"], hits, BID1, dates={})
    assert history.status == "conflict"
    unnumbered = next(r for r in history.revisions if r.addendum_number is None)
    assert unnumbered.addendum_date is None and not unnumbered.orderable
    changes = to_changes("Delivery Date", history)
    assert {c.addendum_number for c in changes} == {None, 2}
    assert reconcile("Delivery Date", [], hits).revisions == []
    assert question_terms("What is it?") == []


# 17. Malformed output, timeouts and absent evidence fail safely


def test_malformed_output_timeout_and_absent_evidence_fail_safely():
    response, _ = ask(
        "Bid1",
        BID1,
        "What warranty is required for the devices?",
        _warranty_search(),
        Model("not json"),
    )
    assert response.status == "not_found" and not response.validation.passed

    class Timeout:
        provider, model = "fake", "timeout"

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise ModelError("llm_timeout", "The language model timed out")

    with pytest.raises(ModelError):
        ask(
            "Bid1",
            BID1,
            "What warranty is required for the devices?",
            _warranty_search(),
            Timeout(),
        )
    assert reconcile("Product Specification", WARRANTY_TERMS, [], BID1).status == "unchanged"
    abstain, _ = ask(
        "Bid1",
        BID1,
        "What warranty is required for the devices?",
        _warranty_search(),
        Model(ABSTAIN),
    )
    assert abstain.status == "not_found" and abstain.validation.passed


def _response(results):
    return SearchResponse(
        query=QueryDiagnostics(original="q", normalized="q", dense_query="q", sparse_query="q"),
        mode="hybrid_rerank",
        top_k=12,
        filters=SearchFilters(bid_ids=[BID1]),
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


def _delivery_model(request: ModelRequest) -> str:
    if "Field: Delivery Date" not in request.user:
        return json.dumps({"fields": {}})
    revised = "Previous rejection" in request.user
    needle = "60 days after award" if revised else "Delivery within 45 days after award"
    current = None
    for line in request.user.splitlines():
        if line.startswith("[") and "]" in line:
            current = line[1 : line.index("]")]
        elif needle in line and current:
            break
    value = DELIVERY_60 if revised else "Delivery within 45 days after award."
    return json.dumps(
        {"fields": {"Delivery Date": {"value": value, "evidence_ids": [current], "notes": "x"}}}
    )


class FakeModel:
    provider, model = "fake", "fake"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(_delivery_model(request), "fake", "fake", 1, 1, 1.0)


def test_extraction_applies_an_addendum_replacement_with_both_citations():
    base = _result(BID1, (RFP_FILE, DocumentType.rfp), DELIVERY_BASE, page=2)
    amended = _result(BID1, ADD1, DELIVERY_60, page=1, addendum=1)

    def search(request):
        assert request.filters.bid_ids == [BID1]
        if request.filters.doc_types == [DocumentType.addendum]:
            return _response([amended])
        if "deliver" in request.query.casefold():
            return _response([base])
        return _response([])

    output, state, events = run_extraction(
        "Bid1", BID1, search, FakeModel(), ExtractionConfig(concurrency=1)
    )
    assert output.fields["Delivery Date"].value == DELIVERY_60
    assert output.fields["Delivery Date"].sources == [OutputSource(file="Addendum 1.pdf", page=1)]
    (change,) = [c for c in output.addendum_changes if c.field == "Delivery Date"]
    assert (change.change_type, change.status) == ("replace", "applied")
    assert change.original_value == DELIVERY_BASE
    assert [(s.file, s.page) for s in change.sources] == [("Addendum 1.pdf", 1), (RFP_FILE, 2)]
    assert state.fields["Delivery Date"].retry_count == 1
    assert any(e.agent == "reconciliation" for e in events)
    BidExtraction.model_validate(output.model_dump())


def test_extraction_survives_a_failed_addendum_check_search():
    base = _result(BID1, (RFP_FILE, DocumentType.rfp), DELIVERY_BASE, page=2)

    def search(request):
        if request.filters.doc_types == [DocumentType.addendum]:
            raise SearchServiceError("Search timed out", "search_timeout")
        if "deliver" in request.query.casefold():
            return _response([base])
        return _response([])

    output, _state, events = run_extraction(
        "Bid1", BID1, search, FakeModel(), ExtractionConfig(concurrency=1)
    )
    assert output.fields["Delivery Date"].value == "Delivery within 45 days after award."
    assert not [c for c in output.addendum_changes if c.field == "Delivery Date"]
    assert any(e.agent == "reconciliation" and e.error == "search_timeout" for e in events)


# 18. Determinism and no duplicate change records


def test_repeated_runs_are_deterministic_without_duplicate_records():
    hits = [
        hit("e1", RFP, WARRANTY_WINDOWS, page=6),
        hit("a1", ADD1, WARRANTY_QA, page=1, addendum=1),
        hit("a2", ADD1, WARRANTY_QA_REPEAT, page=3, addendum=1),
        hit("a3", ADD1, WARRANTY_QA, page=1, addendum=1),
    ]
    first = reconcile("Product Specification", WARRANTY_TERMS, hits, BID1)
    again = reconcile("Product Specification", WARRANTY_TERMS, hits, BID1)
    reordered = reconcile("Product Specification", WARRANTY_TERMS, list(reversed(hits)), BID1)
    assert first == again
    assert [r.statement for r in first.revisions] == [r.statement for r in reordered.revisions]
    statements = [r.statement for r in first.revisions]
    assert len(statements) == len(set(statements))
    changes = to_changes("Product Specification", first)
    keys = [(c.field, c.addendum_number, c.quote) for c in changes]
    assert len(keys) == len(set(keys))
    assert to_changes("Product Specification", again) == changes


def test_identifiers_are_not_assumed_bid_specific():
    """The resolver has no bid labels or document names; the same text reconciles for any bid."""
    other = uuid.uuid4()
    hits = [hit("a1", ADD1, WARRANTY_QA, addendum=1, bid=other)]
    assert {r.statement for r in reconcile("x", ["warranty"], hits, other).material} == {
        MINIMUM,
        OEM,
    }
