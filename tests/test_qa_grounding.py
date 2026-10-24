"""Offline regressions for quote location, claim grounding, list completeness and QA scoring.

Passages mirror text from the assessment bids; the model is a scripted fake.
"""

import json
import uuid
from pathlib import Path

import pytest

from rfp.agents.checklist import candidates, list_keyword
from rfp.agents.errors import ModelError
from rfp.agents.grounding import claim_problems
from rfp.agents.llm import ModelRequest, ModelResponse, parse_json_object
from rfp.agents.qa import LIST_LIMITATION, NOT_FOUND, ask
from rfp.agents.quotes import locate, verbatim
from rfp.agents.schemas import EvidenceHit, SourceRef
from rfp.evaluation.qa import QAGold, load_gold, score
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import (
    Citation,
    EvidenceResult,
    IdentifierMatch,
    LocatorRef,
    ProfileInfo,
    QueryDiagnostics,
    ScoreComponents,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)

ROOT = Path(__file__).resolve().parents[1]
BID1 = uuid.UUID("5809c088-8514-58c3-a0e5-4d9e72adab0b")
BID2 = uuid.UUID("05876a57-4ff3-52ce-a5fe-9d605ebf94b3")
PORFP = ("PORFP_-_Dell_Laptop_Final.pdf", DocumentType.rfp)
CONTRACT = ("Contract_Affidavit.pdf", DocumentType.affidavit)
MERCURY = ("Mercury_Affidavit.pdf", DocumentType.affidavit)
BIDNET = ("Dell Laptops - Bid Information.html", DocumentType.bid_page)
FINAL = ("JA-207652 FINAL.pdf", DocumentType.rfp)
ADD2 = ("Addendum 2 RFP JA-207652.pdf", DocumentType.addendum)

MERCURY_REQ = "7. ENERGY STAR certified product\n\n8. The Master Contractor must provide a Mercury Affidavit:\nhttps://doit.maryland.gov/hwmercury_affidavit.pdf"
CONTRACT_TEXT = (
    "executed by ________________ for the purpose of obtaining the contract to which this "
    "Exhibit is\nattached remains true and correct in all respects as if made as of the date "
    "of this Contract Affidavit"
)
MERCURY_FORM = "MERCURY AFFIDAVIT\nAUTHORIZED REPRESENTATIVE THEREBY AFFIRM THAT:"
CLOSING = "Dates\nPublication\n05/28/2024 11:47 AM EDT\nClosing Date\n06/10/2024 02:00 PM EDT"
SCHEDULE = "Pre-Proposal Meeting\n10-JUN-2024 14:00:00\nQuestions Deadline\n11-JUN-2024 16:00:00"
PRE_PROPOSAL = (
    "A pre-proposal meeting will be held at 2:00 PM CST via TEAMS video conference on June 10,\n"
    "2024. Please submit (in writing) any questions regarding this RFP."
)
ADD2_TEXT = (
    "The Purpose of this Addendum is to extend the due date of this RFP.\n\n"
    "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST.\n\n"
    "Please sign this addendum and submit along with your copies of the proposal. ALL OTHER "
    "PROVISIONS\nAND OTHER TERMS AND CONDITIONS REMAIN UNCHANGED."
)
BOND = (
    "The Offeror must comply with any\ninsurance, bid bond, or liability requirements of the "
    "District as noted elsewhere in this solicitation document."
)


def _result(bid, doc, text, page=1, html=None, addendum=None, page_end=None):
    name, doc_type = doc
    citation = Citation(
        occurrence_id=uuid.uuid4(),
        chunk_generation_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="abc",
        bid_id=bid,
        document_id=uuid.uuid4(),
        original_file_name=name,
        relative_path=name,
        document_type=doc_type,
        addendum_number=addendum,
        chunk_type="prose",
        page_start=None if html else page,
        page_end=None if html else (page_end or page),
        html_locators=[] if html is None else [LocatorRef(relative_path=name, element_id=html)],
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


class Search:
    def __init__(self, results, addenda=()):
        self.results, self.addenda = list(results), list(addenda)
        self.requests: list[SearchRequest] = []

    def __call__(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        chosen = (
            self.addenda if request.filters.doc_types == [DocumentType.addendum] else self.results
        )
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


class Model:
    provider, model = "fake", "fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        text = reply(request.user) if callable(reply) else reply
        return ModelResponse(text, self.provider, self.model, 10, 5, 1.0)


def eid(prompt: str, needle: str) -> str:
    """Evidence id of the first passage block whose text contains the needle."""
    current = None
    for line in prompt.splitlines():
        if line.startswith("[E") and "]" in line:
            current = line[1 : line.index("]")]
        elif needle in line and current:
            return current
    raise AssertionError(f"{needle!r} not in prompt")


def claims(status, *items, unresolved=()):
    """items: (claim text, [(needle, quote), ...])."""

    def build(prompt: str) -> str:
        return json.dumps(
            {
                "status": status,
                "claims": [
                    {
                        "text": text,
                        "citations": [
                            {"evidence_id": eid(prompt, needle), "quote": quote}
                            for needle, quote in cites
                        ],
                    }
                    for text, cites in items
                ],
                "unresolved": [{"item": i, "reason": r} for i, r in unresolved],
            }
        )

    return build


ABSTAIN = json.dumps({"status": "not_found", "claims": []})


# Quote location


def test_exact_and_whitespace_variants_are_accepted():
    assert verbatim("Closing Date 06/10/2024 02:00 PM EDT", CLOSING) == (
        "Closing Date 06/10/2024 02:00 PM EDT"
    )
    assert verbatim("on June 10, 2024.", PRE_PROPOSAL) == "on June 10, 2024."
    assert verbatim("ALL OTHER PROVISIONS AND OTHER TERMS", ADD2_TEXT) is not None


def test_punctuation_and_typography_variants_map_to_source_text():
    assert verbatim("Closing Date: 06/10/2024 02:00 PM EDT", CLOSING) == (
        "Closing Date 06/10/2024 02:00 PM EDT"
    )
    assert verbatim(
        "\u201cThe Master Contractor must provide a Mercury Affidavit\u201d", MERCURY_REQ
    )
    assert verbatim("Buyer\u2019s Email", "addressed to the Buyer's Email.") == "Buyer's Email"


def test_paraphrased_or_stitched_quotes_are_rejected():
    assert verbatim("Proposals are due on June 10, 2024", CLOSING) is None
    assert verbatim("Closing Date ... 02:00 PM EDT", CLOSING) is None
    assert verbatim("Publication 06/10/2024", CLOSING) is None
    assert verbatim("", CLOSING) is None
    assert verbatim("x" * 700, "x" * 800) is None


def test_repeated_text_resolves_to_a_real_span():
    passage = "Warranty: 3-year minimum\nTier 2\nWarranty: 3-year minimum"
    start, end = locate("Warranty: 3-year minimum", passage) or (0, 0)
    assert passage[start:end] == "Warranty: 3-year minimum"


# Claim grounding


def test_details_absent_from_the_quote_are_rejected():
    problems = claim_problems(
        "The pre-proposal meeting is on 10-JUN-2024 at 2:00 PM CST via TEAMS.",
        ["Pre-Proposal Meeting 10-JUN-2024 14:00:00"],
    )
    assert any("2:00" in p for p in problems)
    assert any("CST" in p for p in problems)
    assert any("teams" in p for p in problems)
    assert not claim_problems(
        "The pre-proposal meeting is at 2:00 PM CST via TEAMS video conference on June 10, 2024.",
        [PRE_PROPOSAL.replace("\n", " ")],
    )


def test_unchanged_and_negation_need_explicit_support():
    due = "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST."
    problems = claim_problems(
        "Addendum 2 moved the due date to July 9, 2024; all other provisions remain unchanged.",
        [due],
        file_names=["Addendum 2 RFP JA-207652.pdf"],
    )
    assert any("unchanged" in p for p in problems)
    assert claim_problems("A bid bond is not required.", [BOND])
    assert not claim_problems(
        "All other provisions remain unchanged.",
        ["ALL OTHER PROVISIONS AND OTHER TERMS AND CONDITIONS REMAIN UNCHANGED"],
    )


def test_requirement_wording_needs_a_requirement_quote():
    problems = claim_problems(
        "Vendors must submit a Contract Affidavit.", ["this Contract Affidavit"]
    )
    assert any("requirement wording" in p for p in problems)
    assert not claim_problems(
        "The Master Contractor must provide a Mercury Affidavit.",
        ["The Master Contractor must provide a Mercury Affidavit"],
    )


def test_item_requirement_needs_item_and_requirement_in_one_quote():
    unrelated = "the business shall: (a) Maintain a workplace free of drug and alcohol abuse"
    problems = claim_problems(
        "Vendors must submit a Contract Affidavit.",
        [unrelated, "this Contract Affidavit"],
        strict_requirements=True,
        required_items=["Contract Affidavit"],
    )
    assert "no linked quote states a requirement for 'Contract Affidavit'" in problems
    assert not claim_problems(
        "The Master Contractor must provide a Mercury Affidavit.",
        ["The Master Contractor must provide a Mercury Affidavit"],
        strict_requirements=True,
        required_items=["Mercury Affidavit"],
    )


def test_leading_zeros_are_the_only_tolerated_format_difference():
    assert not claim_problems(
        "The conference is 06/10/2024 at 3:00 PM EDT.", ["06/10/2024 03:00 PM EDT"]
    )
    assert claim_problems("The conference is June 10, 2024.", ["06/10/2024 03:00 PM EDT"])


# Checklist


def test_affidavit_candidates_come_from_text_and_file_names():
    hits = [
        EvidenceHit(
            evidence_id=f"E{i}",
            bid_id=BID2,
            text=text,
            rank=1,
            mode="hybrid",
            final_score=0.1,
            sources=[
                SourceRef(
                    file=doc[0], relative_path=doc[0], document_type=doc[1], page=1, bid_id=BID2
                )
            ],
        )
        for i, (doc, text) in enumerate(
            [(PORFP, MERCURY_REQ), (CONTRACT, CONTRACT_TEXT), (MERCURY, MERCURY_FORM)], start=1
        )
    ]
    question = "Which affidavits must vendors submit with their proposal?"
    assert list_keyword(question) == "affidavit"
    found = candidates(question, hits)
    assert set(found) == {"Mercury Affidavit", "Contract Affidavit"}
    assert found["Mercury Affidavit"] == ["E1", "E3"]
    assert candidates("When are proposals due?", hits) == {}


# End-to-end regressions with a scripted model


AFFIDAVIT_Q = "Which affidavits must vendors submit with their proposal?"


def _affidavit_search():
    return Search(
        [
            _result(BID2, PORFP, MERCURY_REQ, page=2),
            _result(BID2, CONTRACT, CONTRACT_TEXT, page=3),
            _result(BID2, MERCURY, MERCURY_FORM, page=1),
        ]
    )


def test_affidavit_answer_keeps_both_supported_items():
    model = Model(
        claims(
            "answered",
            (
                "The Master Contractor must provide a Mercury Affidavit.",
                [("Mercury Affidavit:", "The Master Contractor must provide a Mercury Affidavit")],
            ),
            (
                "The bid package includes a Contract Affidavit form.",
                [("Contract Affidavit", "this Contract Affidavit")],
            ),
            unresolved=[("Contract Affidavit", "Passages do not say it is due with the proposal")],
        )
    )
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, _affidavit_search(), model)
    assert response.status == "answered"
    assert "Mercury Affidavit" in response.answer and "Contract Affidavit" in response.answer
    assert set(response.completeness.covered) == {"Mercury Affidavit", "Contract Affidavit"}
    assert response.completeness.omitted == [] and response.completeness.exhaustive is False
    assert LIST_LIMITATION in response.limitations
    assert {c.document for c in response.citations} == {
        "PORFP_-_Dell_Laptop_Final.pdf",
        "Contract_Affidavit.pdf",
    }
    assert "Contract Affidavit" in model.calls[0].user.split("Passages:")[0]


def test_omitted_list_item_is_flagged_not_hidden():
    only_mercury = claims(
        "answered",
        (
            "The Master Contractor must provide a Mercury Affidavit.",
            [("Mercury Affidavit:", "The Master Contractor must provide a Mercury Affidavit")],
        ),
    )
    model = Model(only_mercury)
    response, trace = ask("Bid2", BID2, AFFIDAVIT_Q, _affidavit_search(), model)
    assert len(model.calls) == 2
    assert "Item not addressed: Contract Affidavit" in model.calls[1].user
    assert response.status == "answered"
    assert response.completeness.omitted == ["Contract Affidavit"]
    assert any("not addressed: Contract Affidavit" in item for item in response.limitations)
    assert trace.events[-1].reasons == ["Item not addressed: Contract Affidavit"]


def test_missing_list_evidence_is_not_fabricated():
    search = Search([_result(BID2, PORFP, MERCURY_REQ, page=2)])
    model = Model(
        claims(
            "answered",
            (
                "Vendors must provide a Mercury Affidavit and a Contract Affidavit.",
                [("Mercury Affidavit:", "The Master Contractor must provide a Mercury Affidavit")],
            ),
        )
    )
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, search, model)
    assert response.status == "not_found"
    assert any("'Contract' is not in the linked quote" in r for r in response.validation.reasons)


def test_due_date_with_punctuated_quote_is_accepted_first_time():
    search = Search([_result(BID2, BIDNET, CLOSING, html="g_101")])
    model = Model(
        claims(
            "answered",
            (
                "Proposals close 06/10/2024 02:00 PM EDT.",
                [("Closing Date", "Closing Date: 06/10/2024 02:00 PM EDT")],
            ),
        )
    )
    response, _ = ask("Bid2", BID2, "When are proposals due?", search, model)
    assert response.status == "answered" and response.validation.attempts == 1
    citation = response.citations[0]
    assert citation.html_element_id == "g_101" and citation.page is None
    assert citation.excerpt == "Closing Date 06/10/2024 02:00 PM EDT"


TABLE = (
    "06/10/2024\nPORFP Issue Date:\nmm/dd/yyyy\n\n05/24/2024\nPROPOSAL DUE\nDATE and TIME:\n\n"
    "Place of Performance:\nMD State Treasurer's Office"
)


def test_stitched_table_quote_is_rejected_but_separate_quotes_pass():
    search = Search([_result(BID2, PORFP, TABLE, page=1)])
    stitched = claims(
        "answered",
        (
            "Proposals are due on 06/10/2024.",
            [("PROPOSAL DUE", "PROPOSAL DUE DATE and TIME: 06/10/2024")],
        ),
    )
    separate = claims(
        "answered",
        (
            "Proposals are due on 06/10/2024.",
            [("PROPOSAL DUE", "PROPOSAL DUE DATE and TIME:"), ("06/10/2024", "06/10/2024")],
        ),
    )
    model = Model(stitched, separate)
    response, _ = ask("Bid2", BID2, "When are proposals due?", search, model)
    assert "not a verbatim span" in model.calls[1].user
    assert response.status == "answered"
    assert [c.excerpt for c in response.citations] == ["PROPOSAL DUE DATE and TIME:", "06/10/2024"]
    assert any(r.filters.doc_types == [DocumentType.bid_page] for r in search.requests)


def test_valid_quote_keeps_claim_when_a_sibling_quote_is_not_verbatim():
    search = Search([_result(BID2, PORFP, TABLE, page=1)])
    mixed = claims(
        "answered",
        (
            "Proposals are due on 06/10/2024.",
            [
                ("PROPOSAL DUE", "PROPOSAL DUE DATE and TIME: 06/10/2024"),
                ("06/10/2024", "06/10/2024"),
            ],
        ),
    )
    model = Model(mixed)
    response, _ = ask("Bid2", BID2, "When are proposals due?", search, model)
    assert len(model.calls) == 2
    assert response.status == "answered"
    assert [c.excerpt for c in response.citations] == ["06/10/2024"]
    assert any("Discarded non-verbatim quote" in r for r in response.validation.reasons)
    assert any("discarded" in item for item in response.limitations)
    only_bad = claims(
        "answered",
        (
            "Proposals are due on 06/10/2024.",
            [("PROPOSAL DUE", "PROPOSAL DUE DATE and TIME: 06/10/2024")],
        ),
    )
    response, _ = ask("Bid2", BID2, "When are proposals due?", search, Model(only_bad))
    assert response.status == "not_found"


def test_literal_backslash_n_in_table_cells_counts_as_a_line_break():
    passage = (
        "Only Master Contractors that are awarded a contract under the\\nDesktop, Laptop and "
        "Tablet 2015 Master Contract,\\n060B5400007, are eligible to submit a bid"
    )
    quote = (
        "awarded a contract under the\nDesktop, Laptop and Tablet 2015 Master Contract,"
        "\n060B5400007"
    )
    assert verbatim(quote, passage) == (
        "awarded a contract under the Desktop, Laptop and Tablet 2015 Master Contract, 060B5400007"
    )
    assert locate("the nDesktop", passage) is None


def test_raw_line_breaks_inside_json_strings_are_parsed():
    text = '```json\n{"status": "answered", "quote": "PROPOSAL DUE\n06/10/2024"}\n```'
    assert parse_json_object(text)["quote"] == "PROPOSAL DUE\n06/10/2024"
    with pytest.raises(ModelError):
        parse_json_object("no json here")


def test_repeated_identical_claims_are_reported_once():
    search = Search([_result(BID2, PORFP, CLOSING, page=1)])
    claim = ("Proposals close on 06/10/2024.", [("Closing Date", "Closing Date 06/10/2024")])
    response, _ = ask(
        "Bid2", BID2, "When do proposals close?", search, Model(claims("answered", claim, claim))
    )
    assert response.status == "answered"
    assert len(response.claims) == 1


def test_final_attempt_drops_only_unsupported_claims():
    reply = claims(
        "answered",
        (
            "The Master Contractor must provide a Mercury Affidavit.",
            [("Mercury Affidavit:", "The Master Contractor must provide a Mercury Affidavit")],
        ),
        (
            "Vendors must submit a Contract Affidavit.",
            [("Contract Affidavit", "this Contract Affidavit")],
        ),
    )
    model = Model(reply)
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, _affidavit_search(), model)
    assert len(model.calls) == 2 and response.status == "answered"
    assert response.answer == "The Master Contractor must provide a Mercury Affidavit."
    assert response.validation.reasons == [
        "Dropped unsupported claim: Vendors must submit a Contract Affidavit."
    ]
    assert response.completeness.omitted == ["Contract Affidavit"]
    assert any("removed" in item for item in response.limitations)
    assert {c.document for c in response.citations} == {"PORFP_-_Dell_Laptop_Final.pdf"}


def test_requirement_words_echoed_from_question_are_tolerated_outside_lists():
    warranty = "Dell Limited Hardware Warranty Extended for all machines purchased - 3 Years"
    search = Search([_result(BID2, PORFP, warranty, page=3)])
    answer = claims(
        "answered",
        ("The extended warranty required is 3 Years.", [("Dell Limited", warranty)]),
    )
    question = "What extended warranty is required for the Dell laptops?"
    response, _ = ask("Bid2", BID2, question, search, Model(answer))
    assert response.status == "answered" and response.validation.attempts == 1


def test_failed_quote_feedback_names_exact_values_in_the_passage():
    search = Search([_result(BID2, PORFP, TABLE, page=1)])
    stitched = claims(
        "answered",
        (
            "Proposals are due 06/10/2024.",
            [("PROPOSAL DUE", "PROPOSAL DUE DATE and TIME: 06/10/2024")],
        ),
    )
    model = Model(stitched, ABSTAIN)
    ask("Bid2", BID2, "When are proposals due?", search, model)
    assert "does contain '06/10/2024'" in model.calls[1].user


def test_extra_reply_keys_and_plain_unresolved_items_are_tolerated():
    search = Search([_result(BID2, PORFP, MERCURY_REQ, page=2)])
    text = json.dumps(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "The Master Contractor must provide a Mercury Affidavit.",
                    "citations": [
                        {
                            "evidence_id": "E1",
                            "quote": "The Master Contractor must provide a Mercury Affidavit",
                            "page": 2,
                        }
                    ],
                }
            ],
            "unresolved": ["Contract Affidavit"],
            "conflict": [],
        }
    )
    response, _ = ask("Bid2", BID2, AFFIDAVIT_Q, search, Model(text))
    assert response.status == "answered"
    assert response.completeness.unresolved[0].item == "Contract Affidavit"


def test_unsupported_extra_fact_is_corrected():
    search = Search([_result(BID1, FINAL, SCHEDULE + "\n" + PRE_PROPOSAL, page=1, page_end=2)])
    overreach = claims(
        "answered",
        (
            "Yes, the pre-proposal meeting is 10-JUN-2024 at 2:00 PM CST via TEAMS.",
            [("Pre-Proposal Meeting", "Pre-Proposal Meeting\n10-JUN-2024 14:00:00")],
        ),
    )
    grounded = claims(
        "answered",
        (
            "A pre-proposal meeting will be held at 2:00 PM CST via TEAMS video conference on "
            "June 10, 2024.",
            [
                (
                    "pre-proposal meeting will be held",
                    "A pre-proposal meeting will be held at 2:00 PM CST via TEAMS video "
                    "conference on June 10, 2024.",
                )
            ],
        ),
    )
    model = Model(overreach, grounded)
    response, _ = ask("Bid1", BID1, "Is there a pre-proposal conference?", search, model)
    assert response.status == "answered" and response.validation.attempts == 2
    assert "time zone 'CST'" in model.calls[1].user
    assert (response.citations[0].page, response.citations[0].page_end) == (1, 2)


def test_unchanged_claim_without_quote_is_rejected():
    search = Search([], [_result(BID1, ADD2, ADD2_TEXT, addendum=2)])
    answer = claims(
        "answered",
        (
            "Addendum 2 moved the due date to July 9, 2024; all other provisions remain unchanged.",
            [
                (
                    "new due date",
                    "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST.",
                )
            ],
        ),
    )
    response, _ = ask("Bid1", BID1, "What changed in Addendum 2?", search, Model(answer))
    assert response.status == "not_found"
    assert any("unchanged" in r for r in response.validation.reasons)


def test_addendum_revision_takes_precedence():
    base = "Proposals are due no later than 27-JUN-2024 14:00:00."
    search = Search(
        [_result(BID1, FINAL, base, page=2)], [_result(BID1, ADD2, ADD2_TEXT, addendum=2)]
    )
    stale = claims(
        "answered", ("Proposals are due 27-JUN-2024.", [("no later than", "27-JUN-2024")])
    )
    revised = claims(
        "answered",
        (
            "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST.",
            [
                (
                    "new due date",
                    "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST.",
                )
            ],
        ),
    )
    model = Model(stale, revised)
    question = "What is the final proposal submission deadline after all addenda?"
    response, _ = ask("Bid1", BID1, question, search, model)
    assert response.status == "answered"
    assert response.citations[0].addendum_number == 2


def test_unanswerable_question_still_abstains_and_bids_stay_isolated():
    search = Search([_result(BID2, PORFP, MERCURY_REQ, page=2), _result(BID1, FINAL, BOND, page=9)])
    model = Model(ABSTAIN)
    response, _ = ask(
        "Bid2", BID2, "What liquidated damages apply for late delivery?", search, model
    )
    assert (response.status, response.answer) == ("not_found", NOT_FOUND)
    assert "Offeror" not in model.calls[0].user and "Mercury Affidavit" in model.calls[0].user
    assert all(request.filters.bid_ids == [BID2] for request in search.requests)


def test_cross_bid_and_fabricated_citations_are_rejected():
    search = Search([_result(BID2, PORFP, MERCURY_REQ, page=2)])
    fabricated = json.dumps(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "A bid bond is required.",
                    "citations": [{"evidence_id": "E7", "quote": "bid bond"}],
                }
            ],
        }
    )
    response, _ = ask("Bid2", BID2, "Is a bid bond required?", search, Model(fabricated))
    assert response.status == "not_found"
    assert any("not a retrieved passage" in r for r in response.validation.reasons)


# Evaluation


def test_bid_bond_answer_is_scored_against_reviewed_source_evidence():
    gold = load_gold(ROOT / "evaluation" / "qa_reviewed.json")
    case = next(c for c in gold.cases if c.id == "bid1-bid-bond")
    assert case.review_status == "verified" and case.answerable is False
    assumed = {
        "bid_id": "Bid1",
        "question": case.question,
        "status": "answered",
        "answer": "A bid bond is required.",
        "citations": [
            {
                "document": "JA-207652 Student and Staff Computing Devices FINAL.pdf",
                "page": 9,
                "excerpt": "insurance, bid bond, or liability requirements",
            }
        ],
    }
    report = score(QAGold(**{**gold.model_dump(), "cases": [case]}), [assumed])
    assert report["cases"][0]["correct"] is False
    abstained = {**assumed, "status": "not_found", "answer": NOT_FOUND, "citations": []}
    report = score(QAGold(**{**gold.model_dump(), "cases": [case]}), [abstained])
    assert report["summary"]["gold_metrics"]["correct_abstention_rate"] == 1.0


def test_needs_review_cases_are_excluded_from_gold_metrics():
    gold = load_gold(ROOT / "evaluation" / "qa_reviewed.json")
    cases = [c.model_copy(update={"review_status": "needs_review"}) for c in gold.cases[:2]]
    responses = [
        {"bid_id": c.bid_id, "question": c.question, "status": "not_found", "answer": NOT_FOUND}
        for c in cases
    ]
    report = score(QAGold(**{**gold.model_dump(), "cases": cases}), responses)
    assert report["summary"]["needs_review"] == [c.id for c in cases]
    assert report["summary"]["gold_metrics"]["answer_correctness"] is None
    assert report["summary"]["gold_metrics"]["false_abstention_rate"] is None


def test_scoring_reports_omitted_items_and_source_pages():
    gold = load_gold(ROOT / "evaluation" / "qa_reviewed.json")
    case = next(c for c in gold.cases if c.id == "bid2-affidavits")
    response = {
        "bid_id": "Bid2",
        "question": case.question,
        "status": "answered",
        "answer": "The Master Contractor must provide a Mercury Affidavit.",
        "citations": [
            {
                "document": "PORFP_-_Dell_Laptop_Final.pdf",
                "page": 2,
                "excerpt": "The Master Contractor must provide a Mercury Affidavit",
            }
        ],
    }
    report = score(QAGold(**{**gold.model_dump(), "cases": [case]}), [response])
    row = report["cases"][0]
    assert row["items_missing"] == ["Contract Affidavit"] and row["source_hit"] is True
    assert report["summary"]["gold_metrics"]["list_item_completeness"] == 0.5

    overclaim = {
        **response,
        "answer": response["answer"] + " Vendors must submit a Contract Affidavit.",
    }
    row = score(QAGold(**{**gold.model_dump(), "cases": [case]}), [overclaim])["cases"][0]
    assert row["correct"] is False and row["forbidden_claims"]
