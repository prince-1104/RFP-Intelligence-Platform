import math
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from rfp.evaluation.retrieval import (
    EvalCase,
    Judgment,
    citation_problems,
    evaluate_case,
    gains,
    load_dataset,
    ndcg_at,
    recall_at,
    reciprocal_rank_at,
    summarize,
)
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

BID = uuid.uuid4()
DATASET = Path(__file__).resolve().parents[1] / "evaluation" / "assessment_retrieval.json"


def result(rank, text, path="rfp.pdf", page=3, element=None, bid=BID):
    html = not path.endswith(".pdf")
    citation = Citation(
        occurrence_id=uuid.uuid4(),
        chunk_generation_id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        content_hash="h",
        bid_id=bid,
        document_id=uuid.uuid4(),
        original_file_name=path,
        relative_path=path,
        document_type=DocumentType.rfp,
        chunk_type="prose",
        page_start=None if html else page,
        page_end=None if html else page,
        html_locators=[LocatorRef(relative_path=path, element_id=element)] if html else [],
        location_known=True,
        normalization_version="1",
        chunking_version="2",
    )
    return EvidenceResult(
        rank=rank,
        occurrence_id=citation.occurrence_id,
        content_id=citation.content_id,
        content_hash="h",
        text=text,
        chunk_type="prose",
        citation=citation,
        scores=ScoreComponents(final_score=1.0),
        identifier_match=IdentifierMatch(),
    )


def case(*judgments, kind="requirement"):
    return EvalCase(id="c-1", bid="Bid1", kind=kind, query="q", judgments=list(judgments))


WARRANTY = Judgment(document="rfp.pdf", page=3, text=["warranty of  FIVE years"], grade=2)
SUPPORT = Judgment(document="rfp.pdf", page=4, text=["on-site service"], grade=1)


def test_judgment_requires_text_page_and_document_to_match():
    assert WARRANTY.matches(result(1, "The warranty of five years applies."))
    assert not WARRANTY.matches(result(1, "The warranty of five years applies.", page=2))
    assert not WARRANTY.matches(result(1, "A warranty applies.", page=3))
    assert not WARRANTY.matches(result(1, "The warranty of five years.", path="other.pdf"))


def test_html_judgment_matches_on_element_id():
    judgment = Judgment(document="bid.html", html_element_id="g_99", text=["due date"])
    assert judgment.matches(result(1, "Due date: Oct 1", path="bid.html", element="g_99"))
    assert not judgment.matches(result(1, "Due date: Oct 1", path="bid.html", element="g_90"))


def test_locator_rules_are_enforced():
    with pytest.raises(ValidationError):
        Judgment(document="rfp.pdf", text=["x"])
    with pytest.raises(ValidationError):
        Judgment(document="bid.html", page=1, text=["x"])
    with pytest.raises(ValidationError):
        EvalCase(
            id="c-2", bid="Bid1", kind="unknown_identifier", query="JA-1", judgments=[WARRANTY]
        )


def test_metrics_for_relevant_result_at_rank_two():
    results = [result(1, "unrelated"), result(2, "warranty of five years"), result(3, "x")]
    c = case(WARRANTY)
    assert recall_at(c, results, 5) == 1.0
    assert recall_at(c, results, 1) == 0.0
    assert reciprocal_rank_at(c, results, 10) == 0.5
    assert ndcg_at(c, results, 10) == pytest.approx(1 / math.log2(3))


def test_duplicate_hits_are_credited_once_and_grades_weight_ndcg():
    results = [
        result(1, "on-site service", page=4),
        result(2, "warranty of five years"),
        result(3, "warranty of five years"),
    ]
    c = case(WARRANTY, SUPPORT)
    assert gains(c, results) == [1, 2, 0]
    ideal = 3 + 1 / math.log2(3)
    actual = 1 + 3 / math.log2(3)
    assert ndcg_at(c, results, 10) == pytest.approx(actual / ideal)
    assert recall_at(c, results, 5) == 1.0


def test_no_relevant_results_scores_zero():
    c = case(WARRANTY)
    assert (recall_at(c, [], 5), reciprocal_rank_at(c, [], 10), ndcg_at(c, [], 10)) == (0, 0, 0)


def test_citation_problems_detect_leaks_and_bad_locators():
    leaked, problems = citation_problems([result(1, "x", bid=uuid.uuid4())], BID)
    assert leaked and not problems
    bad_pdf = result(1, "x")
    bad_pdf.citation.page_start = None
    bad_html = result(2, "x", path="bid.html", element="g_1")
    bad_html.citation.html_locators = []
    _, problems = citation_problems([bad_pdf, bad_html], BID)
    assert len(problems) == 2


def response(results, warnings=()):
    return SearchResponse(
        query=QueryDiagnostics(original="q", normalized="q", dense_query="q", sparse_query="q"),
        mode=RetrievalMode.hybrid,
        top_k=10,
        filters=SearchFilters(),
        results=results,
        result_count=len(results),
        warnings=list(warnings),
        profile=ProfileInfo(
            embedding_profile="p",
            collection="c",
            dense_model="d",
            sparse_model="s",
            fastembed_version="0",
        ),
    )


def test_unknown_identifier_passes_only_with_no_evidence_and_warning():
    unknown = EvalCase(
        id="u-1", bid="Bid1", kind="unknown_identifier", query="JA-9", answerable=False
    )
    ok = evaluate_case(
        unknown, BID, "hybrid", lambda _: response([], ["identifier_evidence_not_found"])
    )
    fabricated = evaluate_case(unknown, BID, "hybrid", lambda _: response([result(1, "x")]))
    assert ok.unanswerable_ok is True and fabricated.unanswerable_ok is False


def test_search_errors_are_recorded_per_case_and_summarized():
    def boom(_):
        raise RuntimeError("down")

    failed = evaluate_case(case(WARRANTY), BID, "hybrid", boom)
    hit = evaluate_case(
        case(WARRANTY), BID, "hybrid", lambda _: response([result(1, "warranty of five years")])
    )
    summary = summarize([failed, hit])
    assert failed.error and summary["errors"] == 1 and summary["scored_cases"] == 1
    assert summary["recall@5"] == summary["mrr@10"] == summary["hit@5"] == 1.0


def test_committed_dataset_meets_coverage_requirements():
    dataset = load_dataset(DATASET)
    answerable = [c for c in dataset.cases if c.answerable]
    assert len(answerable) >= 30
    kinds = {c.kind for c in dataset.cases}
    assert {
        "identifier",
        "specification",
        "deadline",
        "addendum_change",
        "affidavit",
        "unknown_identifier",
        "unanswerable",
    } <= kinds
    assert {c.bid for c in dataset.cases} == set(dataset.bids) == {"Bid1", "Bid2"}
