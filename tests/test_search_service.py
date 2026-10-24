import json
import logging

import pytest
from qdrant_client import QdrantClient
from search_fakes import (
    BID_A,
    BID_B,
    FakeReranker,
    FakeSparse,
    chunked,
    generation,
    pdf_document,
    sample_bid,
    services,
)

from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest
from rfp.search.errors import (
    CollectionIncompatible,
    IndexUnavailable,
    InvalidSearchRequest,
    ModelUnavailable,
    SearchServiceError,
)
from rfp.search.models import ModelRuntime


@pytest.fixture
def indexed(settings):
    indexer, search, client = services(settings)
    results = sample_bid(BID_A) + sample_bid(BID_B)
    report = indexer.index([generation(r) for r in results])
    assert report.succeeded
    return search, client, report


def run(search, query, mode=RetrievalMode.hybrid_rerank, top_k=5, **filters):
    request = SearchRequest(query=query, mode=mode, top_k=top_k, filters=SearchFilters(**filters))
    return search.search(request)


@pytest.mark.parametrize("mode", list(RetrievalMode))
def test_all_modes_find_exact_identifier_with_pdf_citation(indexed, mode):
    search, _, _ = indexed
    response = run(search, "PX-420", mode=mode, bid_ids=[BID_A])
    assert response.results, response.warnings
    top = response.results[0]
    assert "PX-420" in top.text
    assert top.citation.relative_path == "specs/pumps.pdf"
    assert top.citation.original_file_name == "pumps.pdf"
    assert top.citation.page_start == 1 and top.citation.page_end == 1
    assert top.citation.bid_id == BID_A
    assert response.mode == mode
    assert response.profile.embedding_profile
    assert set(response.timings_ms) >= {"query_embedding", "vector_search", "fusion", "total"}
    if mode == RetrievalMode.dense:
        assert top.scores.dense_score is not None and top.scores.sparse_score is None
    if mode == RetrievalMode.sparse:
        assert top.scores.sparse_score is not None and top.scores.dense_score is None
    if mode in {RetrievalMode.hybrid, RetrievalMode.hybrid_rerank}:
        assert top.scores.rrf_score is not None
        assert top.identifier_match.matched == ["PX-420"]
        assert top.scores.identifier_rank == 1
    if mode == RetrievalMode.hybrid_rerank:
        assert top.scores.reranker_score is not None
        assert top.scores.identifier_bonus == settings_bonus(search)
        assert response.profile.reranker_model == search.config.reranker_model
    else:
        assert response.profile.reranker_model is None


def settings_bonus(search):
    return search.config.identifier_rerank_bonus


def test_one_based_page_citation_for_later_page(indexed):
    search, _, _ = indexed
    response = run(search, "submission deadline", bid_ids=[BID_A], chunk_types=["prose"])
    top = response.results[0]
    assert "deadline" in top.text
    assert top.citation.page_start == 2 and top.citation.section_path == ["2 Schedule"]
    assert top.citation.location_known


def test_html_citation_has_locator_and_null_page(indexed):
    search, _, _ = indexed
    response = run(search, "pre-bid meeting vendors", bid_ids=[BID_A])
    top = response.results[0]
    assert top.citation.relative_path == "notice.html"
    assert top.citation.page_start is None and top.citation.page_end is None
    assert top.citation.html_locators
    assert top.citation.section_path == ["Procurement notice"]
    assert top.citation.location_known


def test_bid_filter_isolates_bids(indexed):
    search, _, _ = indexed
    for bid in (BID_A, BID_B):
        response = run(search, "pumps proposals", top_k=20, bid_ids=[bid])
        assert response.results
        for result in response.results:
            assert result.citation.bid_id == bid
            assert all(c.bid_id == bid for c in result.additional_citations)


def test_metadata_filters(indexed):
    search, _, _ = indexed
    response = run(
        search, "proposals valid ninety days", bid_ids=[BID_A], doc_types=[DocumentType.addendum]
    )
    assert [r.citation.relative_path for r in response.results] == ["addenda/addendum-1.pdf"]
    by_number = run(search, "proposals valid ninety days", bid_ids=[BID_A], addendum_numbers=[1])
    assert by_number.results[0].citation.addendum_number == 1
    headings = run(search, "schedule", bid_ids=[BID_A], chunk_types=["heading"], top_k=10)
    assert {r.chunk_type for r in headings.results} == {"heading"}
    first = headings.results[0].citation.document_id
    scoped = run(search, "schedule", bid_ids=[BID_A], document_ids=[first], top_k=10)
    assert {r.citation.document_id for r in scoped.results} == {first}


def test_duplicate_text_collapsed_with_all_citations(indexed):
    search, _, _ = indexed
    response = run(search, "proposals remain valid ninety days", bid_ids=[BID_A])
    hashes = [r.content_hash for r in response.results]
    assert len(hashes) == len(set(hashes))
    top = response.results[0]
    paths = {top.citation.relative_path} | {c.relative_path for c in top.additional_citations}
    assert paths == {"rfp/terms.pdf", "addenda/addendum-1.pdf"}


def test_duplicates_not_collapsed_when_disabled(settings):
    settings.search.collapse_duplicates = False
    indexer, search, _ = services(settings)
    indexer.index([generation(r) for r in sample_bid()])
    response = run(search, "proposals remain valid ninety days", top_k=10)
    texts = [r.text for r in response.results]
    assert texts.count("All proposals must remain valid for ninety days.") == 2


def test_unknown_identifier_requires_evidence(indexed):
    search, _, _ = indexed
    response = run(search, "ZZ-9999 pump")
    assert response.results == []
    assert "identifier_evidence_not_found" in response.warnings
    assert response.query.identifiers == ["ZZ-9999"]


def test_identifier_policy_boost_returns_semantic_results(settings):
    settings.search.identifier_policy = "boost"
    indexer, search, _ = services(settings)
    indexer.index([generation(r) for r in sample_bid()])
    response = run(search, "ZZ-9999 pump")
    assert response.results and "identifier_evidence_not_found" not in response.warnings


def test_identifier_with_slash_and_compact_variant(indexed):
    search, _, _ = indexed
    response = run(search, "sku ab77/x", bid_ids=[BID_A])
    assert response.results and "AB-77/X" in response.results[0].text


def test_synonym_expansion_is_reported(indexed):
    search, _, _ = indexed
    response = run(search, "deadline", bid_ids=[BID_A], mode=RetrievalMode.sparse)
    assert response.query.expansions[0].term == "deadline"
    assert "deadline" in response.results[0].text
    assert response.query.dense_query == "deadline"
    assert response.query.sparse_query != response.query.normalized


def test_empty_results_for_unmatched_filter(indexed):
    search, _, _ = indexed
    response = run(search, "pumps", relative_paths=["nope.pdf"])
    assert response.results == [] and "no_results" in response.warnings


def test_top_k_and_query_validation(indexed):
    search, _, _ = indexed
    response = run(search, "pumps", top_k=1)
    assert response.result_count == 1
    with pytest.raises(InvalidSearchRequest):
        search.search(
            SearchRequest.model_construct(
                query="pumps", top_k=10_000, mode=RetrievalMode.hybrid, filters=SearchFilters()
            )
        )
    with pytest.raises(InvalidSearchRequest) as error:
        run(search, "x" * 5000)
    assert error.value.code == "query_too_long"


def test_missing_collection_is_clear_error(settings):
    _, search, _ = services(settings)
    with pytest.raises(IndexUnavailable) as error:
        run(search, "pumps")
    assert error.value.code == "index_not_found" and error.value.status == 503


def test_incompatible_collection_fails_without_recreation(settings):
    client = QdrantClient(":memory:")
    indexer, search, _ = services(settings, client=client)
    name = indexer.collection().name
    from qdrant_client import models

    client.create_collection(
        name, vectors_config={"dense": models.VectorParams(size=8, distance=models.Distance.COSINE)}
    )
    with pytest.raises(CollectionIncompatible):
        run(search, "pumps")
    with pytest.raises(CollectionIncompatible):
        indexer.index([generation(sample_bid()[1])])
    assert client.get_collection(name).config.params.vectors["dense"].size == 8


def test_missing_model_is_clear_error(settings):
    def missing():
        raise ModelUnavailable()

    runtime = ModelRuntime(settings.search, dense_factory=missing)
    with pytest.raises(ModelUnavailable) as error:
        runtime.profile()
    assert "init-models" in error.value.message


def test_reranker_failure_is_error_unless_degraded(settings):
    indexer, search, _ = services(settings, reranker=FakeReranker(fail=True))
    indexer.index([generation(r) for r in sample_bid()])
    with pytest.raises(SearchServiceError) as error:
        run(search, "pumps")
    assert error.value.code == "reranker_failed"
    settings.search.allow_degraded = True
    response = run(search, "pumps")
    assert response.degraded and response.results
    assert "reranker_unavailable:reranker_failed" in response.warnings


def test_sparse_failure_degrades_hybrid_only_when_allowed(settings):
    indexer, search, _ = services(settings, sparse=FakeSparse(fail=True))
    indexer.index([generation(r) for r in sample_bid()])
    with pytest.raises(SearchServiceError):
        run(search, "pumps", mode=RetrievalMode.hybrid)
    settings.search.allow_degraded = True
    response = run(search, "pumps", mode=RetrievalMode.hybrid)
    assert response.degraded and response.results
    with pytest.raises(SearchServiceError):
        run(search, "pumps", mode=RetrievalMode.sparse)


def test_staging_and_superseded_points_never_returned(settings):
    indexer, search, client = services(settings)
    old = chunked(pdf_document(BID_A, "a.pdf", [["Legacy pump model QX-100 text."]]))
    indexer.index([generation(old)])
    new = chunked(pdf_document(BID_A, "a.pdf", [["Current pump model QX-200 text."]]))
    gen = generation(new)
    from unittest.mock import patch

    from rfp.search.errors import QdrantUnavailable

    with patch.object(client, "set_payload", side_effect=QdrantUnavailable()):
        failed = indexer.index([gen])
    assert failed.outcomes[0].status == "failed"
    response = run(search, "QX-200 pump", top_k=10)
    assert all("QX-200" not in r.text for r in response.results)
    assert indexer.index([gen]).succeeded
    response = run(search, "pump model", top_k=10)
    assert {r.text for r in response.results} == {"Current pump model QX-200 text."}


def test_search_log_contains_metadata_not_query_text(indexed):
    search, _, _ = indexed
    records: list[logging.LogRecord] = []
    handler = logging.Handler(logging.INFO)
    handler.emit = records.append  # type: ignore[method-assign]
    target = logging.getLogger("rfp.search.service")
    target.addHandler(handler)
    previous = target.level
    target.setLevel(logging.INFO)
    try:
        run(search, "secret pump PX-420 query")
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)
    record = next(r for r in records if r.getMessage() == "search_completed")
    fields = record.fields
    assert fields["query_hash"] and fields["mode"] == "hybrid_rerank"
    assert "candidate_counts" in fields and "timings_ms" in fields
    assert "secret pump" not in json.dumps(fields)


def test_diagnostics_reports_collection(indexed):
    search, _, report = indexed
    diagnostics = search.diagnostics()
    assert diagnostics["exists"] and diagnostics["collection"] == report.collection
    assert diagnostics["points_count"] > 0
