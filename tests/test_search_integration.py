"""Live Qdrant server tests. Skipped unless RFP_QDRANT_INTEGRATION_URL points at a disposable
Qdrant (e.g. ``docker compose up -d qdrant``). Real-model checks also need RFP_MODEL_SMOKE=1 and
models already present in the cache (``python -m rfp.cli init-models``)."""

import os
import uuid

import pytest
from qdrant_client import QdrantClient, models
from search_fakes import BID_A, BID_B, generation, sample_bid, services

from rfp.cli import local_settings
from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest
from rfp.search.collection import KEYWORD_FIELDS, create_qdrant_client
from rfp.search.fusion import reciprocal_rank_fusion

URL = os.environ.get("RFP_QDRANT_INTEGRATION_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="RFP_QDRANT_INTEGRATION_URL not set"),
]


@pytest.fixture
def live():
    settings = local_settings(URL or "")
    settings.qdrant_collection = f"rfp_it_{uuid.uuid4().hex[:8]}"
    client = create_qdrant_client(settings)
    indexer, search, _ = services(settings, client=client)
    yield settings, client, indexer, search
    for collection in client.get_collections().collections:
        if collection.name.startswith(settings.qdrant_collection + "__"):
            client.delete_collection(collection.name)  # disposable test collection only


def test_live_index_payload_indexes_and_search(live):
    _, client, indexer, search = live
    report = indexer.index([generation(r) for r in sample_bid()])
    assert report.succeeded
    schema = client.get_collection(report.collection).payload_schema
    assert set(KEYWORD_FIELDS) <= set(schema)
    assert indexer.index([generation(r) for r in sample_bid()]).outcomes[0].status == "unchanged"
    response = search.search(SearchRequest(query="PX-420", filters=SearchFilters(bid_ids=[BID_A])))
    assert response.results[0].citation.relative_path == "specs/pumps.pdf"
    assert response.results[0].citation.page_start == 1


def test_live_supersession_deletes_previous_generation_on_server(live):
    from search_fakes import chunked, pdf_document

    _, client, indexer, _ = live
    results = sample_bid()
    first = indexer.index([generation(r) for r in results])
    old = next(r for r in results if r.relative_path == "specs/pumps.pdf")
    new = chunked(pdf_document(BID_A, "specs/pumps.pdf", [["Pump model PX-800 text."]]))
    outcome = indexer.index([generation(new)]).outcomes[0]
    assert outcome.status == "completed"
    assert outcome.superseded_points_deleted == len(old.occurrences)
    source = models.Filter(
        must=[
            models.FieldCondition(
                key="relative_path", match=models.MatchValue(value=old.relative_path)
            )
        ]
    )
    points, _ = client.scroll(first.collection, scroll_filter=source, limit=100, with_payload=True)
    assert {p.payload["chunk_generation_id"] for p in points} == {str(new.generation_id)}


def test_live_server_version_is_compatible(live):
    from qdrant_client.common.version_check import is_compatible

    from rfp.search.collection import CollectionManager, client_version

    settings, client, indexer, _ = live
    server = client.info().version
    expected = os.environ.get("RFP_QDRANT_EXPECTED_VERSION")
    if expected:
        assert server == expected
    assert is_compatible(client_version(), server)
    manager = CollectionManager(client, settings.qdrant_collection, indexer.runtime.profile())
    assert manager.check_server() == server


def test_live_filters_scores_and_citations(live):
    _, _, indexer, search = live
    assert indexer.index([generation(r) for r in sample_bid(BID_A) + sample_bid(BID_B)]).succeeded
    response = search.search(
        SearchRequest(
            query="PX-420", mode=RetrievalMode.hybrid, filters=SearchFilters(bid_ids=[BID_B])
        )
    )
    top = response.results[0]
    assert top.citation.bid_id == BID_B and top.citation.relative_path == "specs/pumps.pdf"
    assert top.citation.page_start == 1
    scores = top.scores
    assert scores.dense_rank and scores.sparse_rank and scores.identifier_rank
    assert scores.dense_score is not None and scores.sparse_score is not None
    assert scores.rrf_score is not None and top.identifier_match.matched == ["PX-420"]
    assert set(response.candidate_counts) >= {"dense", "sparse", "identifier", "fused"}
    addendum = search.search(
        SearchRequest(
            query="proposals valid ninety days",
            mode=RetrievalMode.sparse,
            filters=SearchFilters(bid_ids=[BID_A], doc_types=["addendum"], addendum_numbers=[1]),
        )
    )
    assert [r.citation.relative_path for r in addendum.results] == ["addenda/addendum-1.pdf"]
    html = search.search(
        SearchRequest(query="pre-bid meeting vendors", filters=SearchFilters(bid_ids=[BID_A]))
    )
    assert html.results[0].citation.page_start is None
    assert html.results[0].citation.html_locators


def test_app_rrf_matches_server_side_rrf_ordering(live):
    settings, client, indexer, search = live
    report = indexer.index([generation(r) for r in sample_bid()])
    runtime = search.runtime
    query = "proposals pumps deadline"
    dense = list(map(float, runtime.dense.embed_query(query)))
    sparse = runtime.sparse.embed_query(query)
    sparse_vector = models.SparseVector(indices=list(sparse.indices), values=list(sparse.values))
    active = models.Filter(
        must=[models.FieldCondition(key="index_active", match=models.MatchValue(value=True))]
    )
    server = client.query_points(
        report.collection,
        prefetch=[
            models.Prefetch(query=dense, using="dense", filter=active, limit=40),
            models.Prefetch(query=sparse_vector, using="bm25", filter=active, limit=40),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=10,
    ).points
    dense_hits = client.query_points(
        report.collection, query=dense, using="dense", query_filter=active, limit=40
    ).points
    sparse_hits = client.query_points(
        report.collection, query=sparse_vector, using="bm25", query_filter=active, limit=40
    ).points
    ours = reciprocal_rank_fusion(
        {"dense": [str(p.id) for p in dense_hits], "sparse": [str(p.id) for p in sparse_hits]}
    )
    # Equal-score ties may legitimately break differently; compare the score-ordered sets.
    detail = {
        "points": client.count(report.collection, exact=True).count,
        "server": [(str(p.id), p.score) for p in server],
        "dense": [(str(p.id), p.score) for p in dense_hits],
        "sparse": [(str(p.id), p.score) for p in sparse_hits],
        "ours": [(str(i), s) for i, s in ours],
    }
    assert {str(p.id) for p in server} == {str(i) for i, _ in ours[: len(server)]}, detail


@pytest.mark.skipif(os.environ.get("RFP_MODEL_SMOKE") != "1", reason="RFP_MODEL_SMOKE!=1")
def test_real_models_end_to_end(tmp_path):
    from rfp.search.cache import LocalEmbeddingCache
    from rfp.search.indexing import IndexingService
    from rfp.search.models import ModelRuntime
    from rfp.search.service import SearchService

    settings = local_settings(URL or "")
    settings.qdrant_collection = f"rfp_it_{uuid.uuid4().hex[:8]}"
    client: QdrantClient = create_qdrant_client(settings)
    runtime = ModelRuntime(settings.search)
    indexer = IndexingService(runtime, client, LocalEmbeddingCache(tmp_path), settings)
    try:
        assert indexer.index([generation(r) for r in sample_bid()]).succeeded
        search = SearchService(runtime, client, settings)
        response = search.search(SearchRequest(query="PX-420", mode=RetrievalMode.hybrid_rerank))
        assert "PX-420" in response.results[0].text
    finally:
        client.delete_collection(indexer.collection().name)
