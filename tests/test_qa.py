"""Offline citation-grounded Q&A. Model calls are fakes; search responses are constructed."""

import json
import re
import uuid
from pathlib import Path

import pytest

from rfp import cli
from rfp.agents import qa_runner
from rfp.agents.errors import ModelError
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.qa import NOT_FOUND, QAConfig, QAResponse, ask
from rfp.agents.qa_runner import SampleQuestion, generate_samples
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
    SearchRequest,
    SearchResponse,
)

QUESTIONS = Path(__file__).resolve().parents[1] / "examples" / "qa_questions.json"
BID = uuid.UUID("5809c088-8514-58c3-a0e5-4d9e72adab0b")
OTHER = uuid.UUID("05876a57-4ff3-52ce-a5fe-9d605ebf94b3")
RFP = ("RFP.pdf", "RFP.pdf", DocumentType.rfp)
ADD1 = ("Addendum 1.pdf", "Addendum 1.pdf", DocumentType.addendum)
ADD2 = ("Addendum 2.pdf", "Addendum 2.pdf", DocumentType.addendum)
DUE = "Proposals are due no later than March 5, 2025 at 2:00 PM CST."
WARRANTY = "Vendors must provide a three year manufacturer warranty on all devices."
NEW_DUE = "The new due date for proposals is March 12, 2025 at 2:00 PM CST."
OTHER_DUE = "The new due date for proposals is March 19, 2025 at 2:00 PM CST."


def _result(bid, doc, text, page=1, addendum=None, html=None):
    file, path, doc_type = doc
    citation = Citation(
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
        page_start=None if html else page,
        page_end=None if html else page,
        html_locators=[] if html is None else [LocatorRef(relative_path=path, element_id=html)],
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


def _response(results, mode=RetrievalMode.hybrid_rerank):
    return SearchResponse(
        query=QueryDiagnostics(original="q", normalized="q", dense_query="q", sparse_query="q"),
        mode=mode,
        top_k=8,
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


class FakeSearch:
    def __init__(self, main, addenda=()):
        self.main = list(main)
        self.addenda = list(addenda)
        self.requests: list[SearchRequest] = []

    def __call__(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        if request.filters.doc_types == [DocumentType.addendum]:
            return _response(self.addenda, request.mode)
        return _response(self.main, request.mode)


class FakeModel:
    provider = "fake"
    model = "fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if callable(reply):
            reply = reply(request.user)
        if isinstance(reply, Exception):
            raise reply
        return ModelResponse(reply, self.provider, self.model, 13, 5, 1.0)


def eid(prompt: str, needle: str) -> str:
    lines = prompt.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if needle in line:
            match = re.match(r"\[(E\d+)\]", lines[index - 1])
            if match:
                return match.group(1)
    raise AssertionError(f"{needle!r} not in prompt")


def reply(status, answer, *cites):
    def build(prompt: str) -> str:
        citations = [
            {"evidence_id": eid(prompt, needle), "quote": quote} for needle, quote in cites
        ]
        return json.dumps({"status": status, "answer": answer, "citations": citations})

    return build


ABSTAIN = json.dumps({"status": "not_found", "answer": NOT_FOUND, "citations": []})


def run(search, model, question="What warranty is required?", config=None):
    return ask("Bid1", BID, question, search, model, config)


def test_supported_answer_has_valid_citation():
    search = FakeSearch([_result(BID, RFP, DUE, page=3), _result(BID, RFP, WARRANTY, page=7)])
    model = FakeModel(
        reply(
            "answered",
            "A three year manufacturer warranty is required.",
            ("three year", "three year manufacturer warranty"),
        )
    )
    response, trace = run(search, model)
    assert response.status == "answered"
    assert response.validation.passed and response.validation.attempts == 1
    citation = response.citations[0]
    assert (citation.document, citation.page, citation.html_element_id) == ("RFP.pdf", 7, None)
    assert citation.excerpt == "three year manufacturer warranty"
    assert response.confidence is None
    assert response.trace_id == trace.trace_id
    assert all(r.filters.bid_ids == [BID] for r in search.requests)
    assert trace.token_usage.calls == 1


def test_html_citation_uses_element_locator():
    doc = ("Bid.html", "Bid.html", DocumentType.bid_page)
    search = FakeSearch([_result(BID, doc, "Bid number: JA-207652", html="field-number")])
    model = FakeModel(reply("answered", "The bid number is JA-207652.", ("JA-207652", "JA-207652")))
    response, _ = run(search, model, "What is the bid number?")
    assert response.citations[0].page is None
    assert response.citations[0].html_element_id == "field-number"


def test_unanswerable_question_returns_not_found():
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    response, _ = run(search, FakeModel(ABSTAIN), "What liquidated damages apply?")
    assert (response.status, response.answer, response.citations) == ("not_found", NOT_FOUND, [])
    assert response.validation.passed

    empty = FakeModel(ABSTAIN)
    response, _ = run(FakeSearch([]), empty)
    assert response.status == "not_found" and not empty.calls


def test_fabricated_citation_is_rejected():
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    fabricated = json.dumps(
        {
            "status": "answered",
            "answer": "A five year warranty is required.",
            "citations": [{"evidence_id": "E99", "quote": "five year warranty"}],
        }
    )
    response, trace = run(search, FakeModel(fabricated))
    assert response.status == "not_found" and response.answer == NOT_FOUND
    assert not response.validation.passed
    assert any("not a retrieved passage" in r for r in response.validation.reasons)
    assert trace.status == "not_found"


def test_invented_quote_or_unsupported_fact_is_rejected():
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    bad_quote = reply("answered", "Warranty is required.", ("three year", "five year warranty"))
    response, _ = run(search, FakeModel(bad_quote))
    assert response.status == "not_found"
    assert any("quote is not in the cited passage" in r for r in response.validation.reasons)

    bad_fact = reply("answered", "A 5 year, 24/7 warranty.", ("three year", "three year"))
    response, _ = run(search, FakeModel(bad_fact))
    assert response.status == "not_found"
    assert any("does not appear" in r for r in response.validation.reasons)


def test_wrong_bid_evidence_is_never_used():
    leaked = "Other bid requires a five year warranty."
    model = FakeModel(ABSTAIN)
    response, _ = run(FakeSearch([_result(OTHER, RFP, leaked)]), model)
    assert response.status == "not_found" and response.evidence_count == 0 and not model.calls

    model = FakeModel(ABSTAIN)
    run(FakeSearch([_result(OTHER, RFP, leaked), _result(BID, RFP, WARRANTY, page=7)]), model)
    assert leaked not in model.calls[0].user and WARRANTY in model.calls[0].user


def test_conflicting_evidence_is_not_presented_as_fact():
    search = FakeSearch(
        [_result(BID, RFP, DUE, page=3)],
        [_result(BID, ADD1, NEW_DUE, addendum=1), _result(BID, ADD2, OTHER_DUE, addendum=2)],
    )
    as_fact = reply("answered", "Proposals are due March 19, 2025.", ("March 19", "March 19, 2025"))
    conflict = reply(
        "conflict",
        "Addendum 1 says March 12, 2025 and Addendum 2 says March 19, 2025.",
        ("March 12", "March 12, 2025"),
        ("March 19", "March 19, 2025"),
    )
    model = FakeModel(as_fact, conflict)
    response, _ = run(search, model, "When are proposals due?")
    assert response.status == "conflict"
    assert {c.addendum_number for c in response.citations} == {1, 2}
    assert "conflicting addendum due dates" in model.calls[1].user

    one_source = reply("conflict", "Sources disagree.", ("March 12", "March 12, 2025"))
    response, _ = run(search, FakeModel(one_source), "When are proposals due?")
    assert response.status == "not_found"


def test_explicit_addendum_revision_is_applied():
    search = FakeSearch([_result(BID, RFP, DUE, page=3)], [_result(BID, ADD1, NEW_DUE, addendum=1)])
    stale = reply(
        "answered", "Proposals are due March 5, 2025.", ("no later than", "March 5, 2025")
    )
    revised = reply(
        "answered",
        "Addendum 1 moved the due date to March 12, 2025 at 2:00 PM CST.",
        ("new due date", "new due date for proposals is March 12, 2025 at 2:00 PM CST"),
    )
    model = FakeModel(stale, revised)
    response, trace = run(search, model, "When are proposals due?")
    assert response.status == "answered" and response.validation.attempts == 2
    assert response.citations[0].addendum_number == 1
    assert any(r.filters.doc_types == [DocumentType.addendum] for r in search.requests)
    assert [e.task for e in trace.events if e.agent == "qa"] == ["answer", "correct"]


def test_addendum_number_in_question_scopes_lookup():
    search = FakeSearch([_result(BID, RFP, DUE)], [_result(BID, ADD2, OTHER_DUE, addendum=2)])
    run(search, FakeModel(ABSTAIN), "What changed in Addendum 2?")
    scoped = [r for r in search.requests if r.filters.doc_types == [DocumentType.addendum]]
    assert scoped and scoped[0].filters.addendum_numbers == [2]


def test_malformed_model_output_fails_safely():
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    for text in ("not json", json.dumps({"status": "maybe", "answer": "x"})):
        response, _ = run(search, FakeModel(text))
        assert response.status == "not_found" and not response.validation.passed
        assert any("llm_malformed" in r for r in response.validation.reasons)


def test_correction_attempt_is_bounded():
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    model = FakeModel("not json")
    run(search, model)
    assert len(model.calls) == 2
    assert model.calls[1].purpose == "qa:correct"

    model = FakeModel("not json")
    run(search, model, config=QAConfig(max_corrections=0))
    assert len(model.calls) == 1


def test_model_errors_and_missing_configuration_fail_clearly(tmp_path, monkeypatch, capsys):
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    with pytest.raises(ModelError) as error:
        run(search, FakeModel(ModelError("llm_timeout", "timed out")))
    assert error.value.code == "llm_timeout"

    monkeypatch.chdir(tmp_path)
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(qa_runner, "local_search", lambda url: pytest.fail("search reached"))
    assert cli.main(["ask", "--bid-id", "Bid1", "--question", "Warranty?"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "llm_not_configured"

    sample_args = ["ask-samples", "--questions", str(QUESTIONS), "--output", str(tmp_path / "qa")]
    assert cli.main(sample_args) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "llm_not_configured"
    assert not (tmp_path / "qa").exists()

    remote = ["ask", "--bid-id", "Bid1", "--question", "x?", "--qdrant-url", "https://q.example"]
    assert cli.main([*remote, "--llm-mode", "test"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "unsafe_search_target"


def test_cli_json_output_and_trace(tmp_path, monkeypatch, capsys):
    search = FakeSearch([_result(BID, RFP, DUE, page=3), _result(BID, RFP, WARRANTY, page=7)])
    monkeypatch.setattr(qa_runner, "local_search", lambda url: search)
    fixture = tmp_path / "fixture.json"
    answer = {
        "status": "answered",
        "answer": "A three year manufacturer warranty.",
        "citations": [{"evidence_id": "E2", "quote": "three year manufacturer warranty"}],
    }
    fixture.write_text(json.dumps([json.dumps(answer)]), encoding="utf-8")
    question = "What warranty is required for the devices?"
    args = ["ask", "--bid-id", "Bid1", "--question", question, "--llm-mode", "test"]
    out = tmp_path / "out" / "answer.json"
    code = cli.main(
        [*args, "--fixture", str(fixture), "--trace-dir", str(tmp_path / "t"), "--output", str(out)]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    QAResponse.model_validate({k: v for k, v in payload.items() if k != "trace_path"})
    assert payload["status"] == "answered" and payload["model"]["provider"] == "fake"
    assert json.loads(out.read_text(encoding="utf-8"))["trace_id"] == payload["trace_id"]
    trace_text = (tmp_path / "t" / f"qa_{payload['trace_id']}.json").read_text(encoding="utf-8")
    trace = json.loads(trace_text)
    assert trace["question_chars"] == len(question) and question not in trace_text
    assert [e["agent"] for e in trace["events"]] == ["retrieval", "qa"]
    assert [e["sequence"] for e in trace["events"]] == [0, 1]
    assert WARRANTY not in trace_text

    assert cli.main(["ask", "--bid-id", "../x", "--question", "q?", "--llm-mode", "test"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "invalid_bid_id"
    assert cli.main(["ask", "--bid-id", "Bid1", "--question", " ", "--llm-mode", "test"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "invalid_question"
    evaluation = ["--output", "evaluation/answer.json"]
    assert (
        cli.main(["ask", "--bid-id", "Bid1", "--question", "q?", "--llm-mode", "test", *evaluation])
        == 2
    )


def test_generated_samples_carry_model_identity(tmp_path):
    search = FakeSearch([_result(BID, RFP, WARRANTY, page=7)])
    questions = [
        SampleQuestion(bid_id="Bid1", question="What warranty is required?"),
        SampleQuestion(bid_id="Bid2", question="What liquidated damages apply?"),
    ]
    summary = generate_samples(
        questions=questions, output_dir=tmp_path / "qa", search=search, model=FakeModel(ABSTAIN)
    )
    assert summary["questions"] == 2 and summary["model"]["provider"] == "fake"
    saved = json.loads((tmp_path / "qa" / "qa_samples.json").read_text(encoding="utf-8"))
    assert {item["bid_id"] for item in saved} == {"Bid1", "Bid2"}
    assert len(list((tmp_path / "qa" / "traces").glob("qa_*.json"))) == 2
    assert "`fake`" in (tmp_path / "qa" / "qa_samples.md").read_text(encoding="utf-8")
