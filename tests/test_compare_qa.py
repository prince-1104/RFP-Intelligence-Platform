"""Cross-bid comparison (`ask --compare-bids`) and the affidavit false-abstention regression.

Scripted models and synthetic passages only; these tests are not evidence of live answer quality.
"""

import json
import re

import pytest
from test_qa_grounding import (
    ABSTAIN,
    AFFIDAVIT_Q,
    BID1,
    BID2,
    BIDNET,
    CLOSING,
    CONTRACT,
    CONTRACT_TEXT,
    FINAL,
    MERCURY,
    MERCURY_FORM,
    MERCURY_REQ,
    PORFP,
    Model,
    _result,
    claims,
)

from rfp.agents import qa_runner
from rfp.agents.compare import COMPARE_WORDS, compare, focus_query, resolve_bids
from rfp.agents.errors import ExtractionError, ModelError
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.qa import NOT_FOUND, QAConfig, ask, gather_evidence
from rfp.agents.qa_runner import execute_compare
from rfp.cli import main
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import (
    ProfileInfo,
    QueryDiagnostics,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)

LOCAL = "http://127.0.0.1:6333"
WARRANTY_Q = "Compare the warranty requirements of both bids."
BID1_WARRANTY = (
    "4.2 Warranty\nThe Contractor shall provide a minimum three (3) year manufacturer warranty "
    "covering parts and labor for all equipment."
)
BID2_WARRANTY = (
    "6. Warranty: All laptops must include a 5-year ProSupport warranty with next business day "
    "onsite service."
)
SAME_WARRANTY = "All laptops must include a three (3) year warranty."
BID1_OTHER = "Proposals must be delivered to the Purchasing Office in a sealed envelope."
BID2_OTHER = CLOSING
CHECKBOX = (
    "Business type (check one):\n[ ] Corporation [ ] Partnership\n"
    "Check this box if the business has filed a Corporate Affidavit with the State."
)


class BidSearch:
    """Applies the bid filter like the real search service; records every request."""

    def __init__(self, results):
        self.results = list(results)
        self.requests: list[SearchRequest] = []

    def __call__(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        chosen = [
            r
            for r in self.results
            if r.citation.bid_id in request.filters.bid_ids
            and (
                not request.filters.doc_types
                or r.citation.document_type in request.filters.doc_types
            )
        ]
        return SearchResponse(
            query=QueryDiagnostics(original="q", normalized="q", dense_query="q", sparse_query="q"),
            mode=request.mode,
            top_k=request.top_k or 8,
            filters=SearchFilters(bid_ids=request.filters.bid_ids),
            results=chosen,
            result_count=len(chosen),
            profile=ProfileInfo(
                embedding_profile="abc",
                collection="local",
                dense_model="fake",
                sparse_model="fake",
                fastembed_version="fake",
            ),
        )


def sid(prompt: str, letter: str, needle: str) -> str:
    """Evidence id of the first passage in section `letter` whose text contains the needle."""
    current = None
    for line in prompt.splitlines():
        match = re.match(r"\[([A-Z]\d+)\]", line)
        if match:
            current = match.group(1)
        elif needle in line and current and current.startswith(letter):
            return current
    raise AssertionError(f"{needle!r} not in section {letter}")


def comparison(*bids):
    """bids: (label, status, [(aspect, text, [(letter, needle, quote)])])."""

    def build(prompt: str) -> str:
        return json.dumps(
            {
                "bids": [
                    {
                        "bid_id": label,
                        "status": status,
                        "claims": [
                            {
                                "aspect": aspect,
                                "text": text,
                                "citations": [
                                    {"evidence_id": sid(prompt, letter, needle), "quote": quote}
                                    for letter, needle, quote in cites
                                ],
                            }
                            for aspect, text, cites in items
                        ],
                    }
                    for label, status, items in bids
                ]
            }
        )

    return build


BID1_CLAIM = (
    "warranty period",
    "The Contractor shall provide a minimum three (3) year manufacturer warranty.",
    [("A", "three (3) year", "The Contractor shall provide a minimum three (3) year manufacturer")],
)
BID1_CLAIM_FULL = (
    "warranty period",
    "The Contractor shall provide a minimum three (3) year manufacturer warranty.",
    [
        (
            "A",
            "three (3) year",
            "The Contractor shall provide a minimum three (3) year manufacturer warranty",
        )
    ],
)
BID2_CLAIM = (
    "warranty period",
    "All laptops must include a 5-year ProSupport warranty.",
    [("B", "5-year", "All laptops must include a 5-year ProSupport warranty")],
)


def warranty_search(bid1=BID1_WARRANTY, bid2=BID2_WARRANTY):
    return BidSearch(
        [
            _result(BID1, FINAL, bid1, page=12),
            _result(BID1, FINAL, BID1_OTHER, page=3),
            _result(BID2, PORFP, bid2, page=4),
            _result(BID2, BIDNET, BID2_OTHER, html="g_101"),
        ]
    )


def run(search, model, question=WARRANTY_Q, bids=("Bid1", "Bid2")):
    selected = resolve_bids(list(bids))
    return compare(selected, question, search, model)


# Bid selection and safety


def test_bid_selection_rejects_empty_single_duplicate_invalid_and_too_many():
    for values, code in [
        ([], "invalid_comparison"),
        (["Bid1"], "invalid_comparison"),
        (["Bid1", "Bid1"], "duplicate_bid"),
        (["Bid1", "bid1"], "duplicate_bid"),
        (["Bid1", str(resolve_bids(["Bid1", "Bid2"])[0][1])], "duplicate_bid"),
        (["Bid1", "../etc"], "invalid_bid_id"),
        (["A", "B", "C", "D", "E"], "invalid_comparison"),
    ]:
        with pytest.raises(ExtractionError) as error:
            resolve_bids(values)
        assert error.value.code == code, values
    assert [label for label, _ in resolve_bids(["Bid1", "Bid2"])] == ["Bid1", "Bid2"]


def test_unknown_bid_is_rejected_before_any_model_call():
    search = BidSearch([_result(BID1, FINAL, BID1_WARRANTY, page=12)])
    model = Model(ABSTAIN)
    with pytest.raises(ExtractionError) as error:
        run(search, model)
    assert error.value.code == "unknown_bid" and "Bid2" in error.value.message
    assert model.calls == []


def test_remote_qdrant_and_unsafe_collection_are_refused():
    with pytest.raises(ExtractionError) as remote:
        execute_compare(
            bids=["Bid1", "Bid2"],
            question=WARRANTY_Q,
            qdrant_url="https://cluster.cloud.qdrant.io:6333",
            llm_mode="test",
            fixture=None,
            trace_dir=None,
            output=None,
        )
    assert remote.value.code == "unsafe_search_target"
    base = ["ask", "--compare-bids", "Bid1", "Bid2", "--question", WARRANTY_Q]
    with pytest.raises(SystemExit):
        main([*base, "--collection", "Bad Name!"])
    with pytest.raises(SystemExit):
        main(["ask", "--compare-bids", "--question", WARRANTY_Q])
    with pytest.raises(SystemExit):
        main(["ask", "--bid-id", "Bid1", "--compare-bids", "Bid1", "Bid2", "--question", "x?"])


def test_cli_routes_compare_and_single_bid_requests(monkeypatch, capsys, tmp_path):
    seen: list[tuple[str, object]] = []

    def fake_compare(**kwargs):
        seen.append(("compare", kwargs["bids"]))
        raise ExtractionError("duplicate_bid", "Each bid may be selected only once")

    def fake_ask(**kwargs):
        seen.append(("ask", kwargs["bid"]))
        raise ExtractionError("invalid_bid_id", "Bid id must be a short label or UUID")

    monkeypatch.setattr(qa_runner, "execute_compare", fake_compare)
    monkeypatch.setattr(qa_runner, "execute_ask", fake_ask)
    trace = ["--trace-dir", str(tmp_path)]
    assert main(["ask", "--compare-bids", "Bid1", "Bid1", "--question", "q?", *trace]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "duplicate_bid"
    assert main(["ask", "--bid-id", "Bid1", "--question", "q?", *trace]) == 2
    assert seen == [("compare", ["Bid1", "Bid1"]), ("ask", "Bid1")]


def test_comparison_wording_is_removed_from_retrieval_queries():
    assert focus_query(WARRANTY_Q) == "warranty requirements"
    assert focus_query("What are the differences in delivery terms between the two bids?") == (
        "What are the in delivery terms"
    )
    assert focus_query("Compare both bids") == "Compare both bids"
    assert COMPARE_WORDS.search("Which affidavits are required?") is None


# Comparison behaviour


def test_different_warranty_requirements_are_compared_with_per_bid_citations(tmp_path):
    search = warranty_search()
    model = Model(
        comparison(("Bid1", "answered", [BID1_CLAIM]), ("Bid2", "answered", [BID2_CLAIM]))
    )
    response, trace = run(search, model)

    assert response.status == "answered" and response.missing_evidence == []
    assert response.bid_ids == ["Bid1", "Bid2"]
    first, second = response.findings
    assert first.bid_id == "Bid1" and "three (3) year" in first.answer
    assert second.bid_id == "Bid2" and "5-year" in second.answer
    assert {c.document for c in first.citations} == {FINAL[0]}
    assert {c.document for c in second.citations} == {PORFP[0]}
    assert [c.page for c in first.citations] == [12] and [c.page for c in second.citations] == [4]
    assert all(c.excerpt in BID1_WARRANTY.replace("\n", " ") for c in first.citations)
    assert all(c.excerpt in BID2_WARRANTY for c in second.citations)
    assert [(a.aspect, a.relation) for a in response.aspects] == [("warranty period", "different")]
    assert "Bid1: The Contractor shall provide" in response.answer
    assert "warranty period: cited statements differ between Bid1, Bid2." in response.answer
    assert response.validation.passed and response.trace_id == trace.trace_id
    assert trace.bid_uuids == [BID1, BID2] and trace.mode == "comparison"
    assert [e.field for e in trace.events if e.task == "bid_probe"] == ["Bid1", "Bid2"]
    assert all(len(r.filters.bid_ids) == 1 for r in search.requests)
    assert {r.filters.bid_ids[0] for r in search.requests} == {BID1, BID2}
    prompt = model.calls[0].user
    assert prompt.index("Section A: bid Bid1") < prompt.index("Section B: bid Bid2")
    section_a = prompt.split("Section B:")[0]
    assert "ProSupport" not in section_a and "Purchasing Office" in section_a


def test_identical_warranty_requirements_are_reported_as_identical():
    search = warranty_search(SAME_WARRANTY, SAME_WARRANTY)
    claim = "All laptops must include a three (3) year warranty."
    model = Model(
        comparison(
            ("Bid1", "answered", [("warranty", claim, [("A", "three (3)", claim[:-1])])]),
            ("Bid2", "answered", [("Warranty.", claim, [("B", "three (3)", claim[:-1])])]),
        )
    )
    response, _ = run(search, model)
    assert response.status == "answered"
    assert [(a.aspect, a.relation) for a in response.aspects] == [("warranty", "identical")]
    assert response.aspects[0].claims == {"Bid1": [claim], "Bid2": [claim]}
    assert {c.document for c in response.findings[1].citations} == {PORFP[0]}


def test_missing_warranty_evidence_in_one_bid_is_stated_not_invented():
    search = BidSearch(
        [
            _result(BID1, FINAL, BID1_WARRANTY, page=12),
            _result(BID2, BIDNET, BID2_OTHER, html="g_101"),
        ]
    )
    model = Model(comparison(("Bid1", "answered", [BID1_CLAIM]), ("Bid2", "not_found", [])))
    response, _ = run(search, model)
    assert response.status == "partial" and response.missing_evidence == ["Bid2"]
    assert response.findings[1].answer == NOT_FOUND and response.findings[1].citations == []
    assert f"Bid2: {NOT_FOUND}" in response.answer
    assert response.aspects[0].relation == "one_sided"
    assert "no validated statement for Bid2" in response.answer
    assert any(item.startswith("Bid2: Not found in documents") for item in response.limitations)


def test_missing_evidence_in_both_bids_abstains():
    search = BidSearch(
        [
            _result(BID1, FINAL, BID1_OTHER, page=3),
            _result(BID2, BIDNET, BID2_OTHER, html="g_101"),
        ]
    )
    model = Model(comparison(("Bid1", "not_found", []), ("Bid2", "not_found", [])))
    response, _ = run(search, model, "Compare the liquidated damages of both bids.")
    assert (response.status, response.answer) == ("not_found", NOT_FOUND)
    assert response.missing_evidence == ["Bid1", "Bid2"] and response.aspects == []
    assert all(f.validation.reasons == ["Model abstained"] for f in response.findings)


def test_cross_bid_citation_is_rejected_and_never_attributed():
    search = warranty_search()
    borrowed = (
        "warranty period",
        "All laptops must include a 5-year ProSupport warranty.",
        [("B", "5-year", "All laptops must include a 5-year ProSupport warranty")],
    )
    model = Model(comparison(("Bid1", "answered", [borrowed]), ("Bid2", "answered", [BID2_CLAIM])))
    response, _ = run(search, model)

    assert len(model.calls) == 2
    assert "Bid1: Claim 'All laptops must include a 5-year" in model.calls[1].user
    assert "belongs to bid Bid2, not Bid1" in model.calls[1].user
    assert response.status == "partial" and response.missing_evidence == ["Bid1"]
    bid1 = response.findings[0]
    assert bid1.status == "not_found" and not bid1.validation.passed
    assert any(c.reason == "cross_bid" for c in bid1.validation.checks)
    assert response.findings[1].status == "answered"
    assert all(c.document == PORFP[0] for c in response.findings[1].citations)


def test_quote_from_another_bid_under_own_id_and_fabricated_ids_are_rejected():
    search = warranty_search()
    pasted = (
        "warranty period",
        "All laptops must include a 5-year ProSupport warranty.",
        [("A", "three (3) year", "All laptops must include a 5-year ProSupport warranty")],
    )

    def fabricated(prompt: str) -> str:
        payload = json.loads(comparison(("Bid2", "answered", [BID2_CLAIM]))(prompt))
        payload["bids"].insert(
            0,
            {
                "bid_id": "Bid1",
                "status": "answered",
                "claims": [
                    {
                        "aspect": "warranty period",
                        "text": "A three (3) year warranty applies.",
                        "citations": [{"evidence_id": "A9", "quote": "three (3) year"}],
                    }
                ],
            },
        )
        return json.dumps(payload)

    response, _ = run(
        search,
        Model(comparison(("Bid1", "answered", [pasted]), ("Bid2", "answered", [BID2_CLAIM]))),
    )
    assert response.findings[0].status == "not_found"
    assert any("not a verbatim span" in r for r in response.findings[0].validation.reasons)
    response, _ = run(search, Model(fabricated))
    assert response.findings[0].status == "not_found"
    assert any("not a retrieved passage" in r for r in response.findings[0].validation.reasons)
    assert response.findings[1].status == "answered"


def test_valid_bid_keeps_its_claims_while_the_other_is_corrected():
    search = warranty_search()
    paraphrase = (
        "warranty period",
        "All laptops must include a 5-year ProSupport warranty.",
        [("B", "5-year", "laptops need a 5 year ProSupport warranty")],
    )
    model = Model(
        comparison(("Bid1", "answered", [BID1_CLAIM_FULL]), ("Bid2", "answered", [paraphrase])),
        comparison(("Bid1", "not_found", []), ("Bid2", "answered", [BID2_CLAIM])),
    )
    response, _ = run(search, model)
    assert response.status == "answered"
    assert response.findings[0].validation.attempts == 1
    assert response.findings[1].validation.attempts == 2
    assert "Bid1:" not in model.calls[1].user.split("Passages:")[0]


def test_final_attempt_salvage_reports_dropped_claims_without_a_kept_note():
    search = warranty_search()
    bad = (
        "warranty period",
        "All laptops must include a 5-year ProSupport warranty with onsite repair in 10 days.",
        [("B", "5-year", "All laptops must include a 5-year ProSupport warranty")],
    )
    reply = comparison(
        ("Bid1", "answered", [BID1_CLAIM_FULL]), ("Bid2", "answered", [BID2_CLAIM, bad])
    )
    response, _ = run(search, Model(reply))
    reasons = response.findings[1].validation.reasons
    assert response.findings[1].status == "answered" and reasons[0].startswith("Dropped")
    assert not any(reason.startswith("Kept claims") for reason in reasons)


def test_malformed_model_output_abstains_and_timeout_propagates():
    search = warranty_search()
    response, trace = run(search, Model("not json at all"))
    assert (response.status, response.answer) == ("not_found", NOT_FOUND)
    assert all(not f.validation.passed for f in response.findings)
    assert all("llm_malformed" in f.validation.reasons[1] for f in response.findings)
    assert [e.error is not None for e in trace.events if e.agent == "qa"] == [True, True]

    class Timeout:
        provider, model = "fake", "timeout"

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise ModelError("llm_timeout", "The language model timed out")

    with pytest.raises(ModelError) as error:
        run(search, Timeout())
    assert error.value.code == "llm_timeout"


def test_retry_limit_follows_qa_config():
    search = warranty_search()
    model = Model("not json")
    response, _ = compare(
        resolve_bids(["Bid1", "Bid2"]), WARRANTY_Q, search, model, QAConfig(max_corrections=0)
    )
    assert len(model.calls) == 1 and response.status == "not_found"


# Affidavit false abstention (qa_v7: a valid Mercury claim was lost when the correction abstained)


def affidavit_search(extra=()):
    return BidSearch(
        [
            _result(BID2, PORFP, MERCURY_REQ, page=2),
            _result(BID2, CONTRACT, CONTRACT_TEXT, page=3),
            _result(BID2, MERCURY, MERCURY_FORM, page=1),
            *extra,
        ]
    )


QA_V7_FIRST = claims(
    "answered",
    (
        "Vendors must provide a Mercury Affidavit.",
        [("Mercury Affidavit:", "The Master Contractor must provide a Mercury Affidavit:")],
    ),
    (
        "Vendors must submit a Contract Affidavit.",
        [("Contract Affidavit", "as if made as of the date of this Contract Affidavit")],
    ),
)


def test_affidavit_requirement_is_retrieved_from_the_rfp():
    search = affidavit_search()
    events: list = []
    hits = gather_evidence(AFFIDAVIT_Q, BID2, search, QAConfig(), events)
    assert any("must provide a Mercury Affidavit" in h.text for h in hits)
    porfp = next(h for h in hits if "Mercury Affidavit:" in h.text)
    assert porfp.sources[0].file == PORFP[0] and porfp.sources[0].page == 2
    assert any(r.query == "affidavit affidavits" for r in search.requests)
    assert all(r.filters.bid_ids == [BID2] for r in search.requests)


def test_validated_affidavit_claim_survives_an_abstaining_correction():
    model = Model(QA_V7_FIRST, ABSTAIN)
    response, trace = ask("Bid2", BID2, AFFIDAVIT_Q, affidavit_search(), model)

    assert len(model.calls) == 2
    assert response.status == "answered"
    assert response.answer == "Vendors must provide a Mercury Affidavit."
    assert [(c.document, c.page) for c in response.citations] == [(PORFP[0], 2)]
    assert response.validation.reasons[0] == (
        "Kept claims validated on attempt 1; attempt 2 did not produce a validated answer"
    )
    assert "Dropped unsupported claim: Vendors must submit a Contract Affidavit." in (
        response.validation.reasons
    )
    assert response.completeness.omitted == ["Contract Affidavit"]
    assert any("not addressed: Contract Affidavit" in item for item in response.limitations)
    assert [e.decision for e in trace.events if e.agent == "qa"] == ["answered", "not_found"]


def test_checkbox_instruction_is_not_reported_as_a_required_document():
    extra = [_result(BID2, CONTRACT, CHECKBOX, page=1)]
    reply = claims(
        "answered",
        (
            "Vendors must provide a Mercury Affidavit.",
            [("Mercury Affidavit:", "The Master Contractor must provide a Mercury Affidavit:")],
        ),
        (
            "Vendors must submit a Corporate Affidavit.",
            [("Corporate Affidavit", "has filed a Corporate Affidavit with the State")],
        ),
    )
    response, trace = ask("Bid2", BID2, AFFIDAVIT_Q, affidavit_search(extra), Model(reply, ABSTAIN))
    assert response.status == "answered"
    assert "Corporate" not in response.answer
    assert "Dropped unsupported claim: Vendors must submit a Corporate Affidavit." in (
        response.validation.reasons
    )
    assert any(
        "no linked quote states a requirement for 'Corporate Affidavit'" in r
        for r in trace.events[-2].reasons
    )
    only_checkbox = BidSearch([_result(BID2, CONTRACT, CHECKBOX, page=1)])
    checkbox_reply = claims(
        "answered",
        (
            "Vendors must submit a Corporate Affidavit.",
            [("Corporate Affidavit", "has filed a Corporate Affidavit with the State")],
        ),
    )
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, only_checkbox, Model(checkbox_reply, ABSTAIN))
    assert (response.status, response.answer) == ("not_found", NOT_FOUND)


def test_absent_affidavit_requirement_still_abstains():
    search = BidSearch([_result(BID2, BIDNET, CLOSING, html="g_101")])
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, search, Model(ABSTAIN))
    assert (response.status, response.answer) == ("not_found", NOT_FOUND)
    invented = claims(
        "answered",
        (
            "Vendors must submit a Mercury Affidavit.",
            [("Closing Date", "Closing Date\n06/10/2024 02:00 PM EDT")],
        ),
    )
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, search, Model(invented, ABSTAIN))
    assert (response.status, response.answer) == ("not_found", NOT_FOUND)


def test_another_bids_affidavit_cannot_satisfy_the_question():
    search = BidSearch(
        [
            _result(BID1, PORFP, MERCURY_REQ, page=2),
            _result(BID2, BIDNET, CLOSING, html="g_101"),
        ]
    )
    model = Model(ABSTAIN)
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, search, model)
    assert response.status == "not_found"
    assert "Mercury" not in model.calls[0].user
    assert all(r.filters.bid_ids == [BID2] for r in search.requests)

    model = Model(
        comparison(
            (
                "Bid1",
                "answered",
                [
                    (
                        "affidavits",
                        "Vendors must provide a Mercury Affidavit.",
                        [
                            (
                                "A",
                                "Mercury Affidavit:",
                                "The Master Contractor must provide a Mercury Affidavit:",
                            )
                        ],
                    )
                ],
            ),
            (
                "Bid2",
                "answered",
                [
                    (
                        "affidavits",
                        "Vendors must provide a Mercury Affidavit.",
                        [
                            (
                                "A",
                                "Mercury Affidavit:",
                                "The Master Contractor must provide a Mercury Affidavit:",
                            )
                        ],
                    )
                ],
            ),
        )
    )
    response, _ = run(search, model, "Which affidavits are required in each bid?")
    assert response.findings[0].status == "answered"
    assert response.findings[1].status == "not_found"
    assert response.missing_evidence == ["Bid2"]
    assert all(c.document == PORFP[0] for c in response.findings[0].citations)


def test_document_type_filters_stay_bid_scoped_for_addendum_queries():
    search = BidSearch(
        [
            _result(BID1, FINAL, BID1_WARRANTY, page=12),
            _result(BID2, PORFP, BID2_WARRANTY, page=4),
        ]
    )
    model = Model(comparison(("Bid1", "not_found", []), ("Bid2", "not_found", [])))
    run(search, model, "Compare the proposal due dates of both bids.")
    addendum = [r for r in search.requests if r.filters.doc_types == [DocumentType.addendum]]
    assert addendum and all(len(r.filters.bid_ids) == 1 for r in addendum)
