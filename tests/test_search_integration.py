"""Live Qdrant server tests. Skipped unless RFP_QDRANT_INTEGRATION_URL points at a disposable
Qdrant (e.g. ``docker compose up -d qdrant``). Real-model checks also need RFP_MODEL_SMOKE=1 and
models already present in the cache (``python -m rfp.cli init-models``)."""

import os
import uuid

import pytest
from qdrant_client import QdrantClient, models
from search_fakes import BID_A, generation, sample_bid, services

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
    assert {str(p.id) for p in server} == {str(i) for i, _ in ours[: len(server)]}


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
