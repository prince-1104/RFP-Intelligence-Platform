import uuid
from unittest.mock import patch

import pytest
from qdrant_client import models
from search_fakes import (
    BID_A,
    DIM,
    ESTIMATED,
    FakeDense,
    FakeSparse,
    MemoryCache,
    chunked,
    generation,
    pdf_document,
    sample_bid,
    services,
)

from rfp.schemas.chunks import ChunkConfig
from rfp.search.collection import DENSE_VECTOR, SPARSE_VECTOR
from rfp.search.errors import QdrantUnavailable
from rfp.search.indexing import load_local_artifacts
from rfp.search.payload import point_id


def all_points(client, name):
    points, _ = client.scroll(name, limit=1000, with_payload=True, with_vectors=True)
    return points


def test_index_creates_one_point_per_occurrence_with_deterministic_ids(settings):
    indexer, _, client = services(settings)
    results = sample_bid()
    report = indexer.index([generation(r) for r in results], run_id="run-1")
    assert report.succeeded
    occurrences = [o for r in results for o in r.occurrences]
    points = all_points(client, report.collection)
    assert len(points) == len(occurrences)
    expected = {point_id(o.occurrence_id, o.generation_id) for o in occurrences}
    assert {str(p.id) for p in points} == expected
    point = points[0]
    assert set(point.vector) == {DENSE_VECTOR, SPARSE_VECTOR}
    assert len(point.vector[DENSE_VECTOR]) == DIM
    payload = point.payload
    assert payload["index_active"] is True
    assert payload["profile_fingerprint"] == report.profile_fingerprint
    assert payload["indexing_run_id"] == "run-1"
    assert report.collection.startswith(settings.qdrant_collection + "__")


def test_shared_content_embedded_once_and_reused_from_cache(settings):
    dense = FakeDense()
    cache = MemoryCache()
    indexer, _, client = services(settings, cache=cache, dense=dense)
    results = sample_bid()
    report = indexer.index([generation(r) for r in results])
    unique = {o.content_hash for r in results for o in r.occurrences}
    total = sum(len(r.occurrences) for r in results)
    assert total > len(unique)  # duplicate text across terms.pdf and the addendum
    assert dense.documents_embedded == len(unique)
    assert report.as_dict()["points"] == total

    # A fresh collection (e.g. after a Qdrant loss) is rebuilt entirely from cache.
    fresh_dense = FakeDense()
    rebuilt, _, _ = services(settings, cache=cache, dense=fresh_dense)
    again = rebuilt.index([generation(r) for r in results])
    assert again.succeeded and fresh_dense.documents_embedded == 0
    assert again.as_dict()["cache_misses"] == 0


def test_reindex_is_idempotent_and_unchanged(settings):
    dense = FakeDense()
    indexer, _, client = services(settings, dense=dense)
    gens = [generation(r) for r in sample_bid()]
    first = indexer.index(gens)
    calls = dense.documents_embedded
    points = {str(p.id) for p in all_points(client, first.collection)}
    second = indexer.index(gens)
    assert second.succeeded
    assert {o.status for o in second.outcomes} == {"unchanged"}
    assert dense.documents_embedded == calls
    assert {str(p.id) for p in all_points(client, first.collection)} == points


def test_incremental_new_generation_supersedes_only_its_source(settings):
    indexer, _, client = services(settings)
    results = sample_bid()
    indexer.index([generation(r) for r in results])
    spec_old = next(r for r in results if r.relative_path == "specs/pumps.pdf")
    changed = chunked(
        pdf_document(
            BID_A,
            "specs/pumps.pdf",
            [["Pump model PX-500 replaces the prior model."]],
            doc_type=spec_old.occurrences[0].document_type,
        )
    )
    assert changed.generation_id != spec_old.generation_id
    report = indexer.index([generation(changed)])
    outcome = report.outcomes[0]
    assert outcome.status == "completed"
    assert outcome.superseded_points_removed == len(spec_old.occurrences)
    points = all_points(client, report.collection)
    generations = {p.payload["chunk_generation_id"] for p in points}
    assert str(spec_old.generation_id) not in generations
    assert str(changed.generation_id) in generations
    untouched = [r for r in results if r.relative_path != "specs/pumps.pdf"]
    for result in untouched:
        assert str(result.generation_id) in generations


def test_multi_batch_upsert_is_not_reported_as_a_retry(settings):
    small = settings.model_copy(
        update={"search": settings.search.model_copy(update={"upsert_batch_size": 2})}
    )
    indexer, _, _ = services(small)
    long = chunked(
        pdf_document(BID_A, "long.pdf", [[f"Clause {i} requires PX-{i}."] for i in range(5)]),
        ChunkConfig(target_tokens=8, max_tokens=10, overlap_tokens=0, **ESTIMATED),
    )
    outcome = indexer.index([generation(long)]).outcomes[0]
    assert outcome.status == "completed" and outcome.point_count >= 3
    assert outcome.upsert_batches == -(-outcome.point_count // 2)
    assert outcome.attempts == 1 and outcome.upsert_retries == 0


def test_transient_upsert_failure_is_retried_once_and_counted(settings):
    sleeps = []
    indexer, _, client = services(settings)
    indexer.sleep = sleeps.append
    original = client.upsert
    calls = iter([QdrantUnavailable()])

    def flaky(*args, **kwargs):
        failure = next(calls, None)
        if failure:
            raise failure
        return original(*args, **kwargs)

    with patch.object(client, "upsert", side_effect=flaky):
        outcome = indexer.index([generation(sample_bid()[1])]).outcomes[0]
    assert outcome.status == "completed"
    assert outcome.attempts == 2 and outcome.upsert_retries == 1 and sleeps == [1]


def test_deterministic_rejection_is_not_retried(settings):
    from qdrant_client.http.exceptions import UnexpectedResponse

    sleeps = []
    indexer, _, client = services(settings)
    indexer.sleep = sleeps.append
    rejected = UnexpectedResponse(400, "Bad Request", b'{"status":"bad"}', None)
    with patch.object(client, "upsert", side_effect=rejected):
        outcome = indexer.index([generation(sample_bid()[1])]).outcomes[0]
    assert outcome.status == "failed" and outcome.error_code == "vector_store_request_rejected"
    assert outcome.attempts == 1 and outcome.upsert_retries == 0 and sleeps == []


def test_failed_upsert_retries_bounded_and_keeps_previous_active(settings):
    sleeps = []
    indexer, _, client = services(settings)
    results = sample_bid()
    indexer.index([generation(r) for r in results])
    indexer.sleep = sleeps.append
    spec_old = next(r for r in results if r.relative_path == "specs/pumps.pdf")
    changed = chunked(pdf_document(BID_A, "specs/pumps.pdf", [["Pump model PX-900 text."]]))
    with patch.object(client, "upsert", side_effect=QdrantUnavailable()):
        report = indexer.index([generation(changed)])
    outcome = report.outcomes[0]
    assert outcome.status == "failed" and outcome.error_code == "vector_store_unavailable"
    assert outcome.attempts == settings.search.upsert_attempts
    assert len(sleeps) == settings.search.upsert_attempts - 1
    assert not report.succeeded
    active = [
        p
        for p in all_points(client, report.collection)
        if p.payload["relative_path"] == "specs/pumps.pdf"
    ]
    assert active and all(p.payload["index_active"] for p in active)
    assert {p.payload["chunk_generation_id"] for p in active} == {str(spec_old.generation_id)}


def test_transient_failure_stops_run_and_leaves_rest_pending(settings):
    indexer, _, client = services(settings)
    gens = [generation(r) for r in sample_bid()]
    indexer.index([])  # creates collection
    with patch.object(client, "upsert", side_effect=QdrantUnavailable()):
        report = indexer.index(gens)
    statuses = [o.status for o in report.outcomes]
    assert statuses[0] == "failed" and set(statuses[1:]) == {"pending"}


def test_verification_failure_discards_staging_without_activation(settings):
    indexer, search, client = services(settings)
    gens = [generation(r) for r in sample_bid()]
    original = client.count

    def short_count(name, count_filter=None, exact=True):
        result = original(name, count_filter=count_filter, exact=exact)
        return models.CountResult(count=max(result.count - 1, 0)) if result.count else result

    with patch.object(client, "count", side_effect=short_count):
        report = indexer.index(gens[:1])
    assert report.outcomes[0].error_code == "index_verification_failed"
    assert all_points(client, report.collection) == []


def test_invalid_chunk_input_fails_safely(settings):
    indexer, _, client = services(settings)
    result = sample_bid()[0]
    broken = generation(result)
    broken.occurrences = [
        broken.occurrences[0].model_copy(update={"content_hash": "0" * 64}),
        *broken.occurrences[1:],
    ]
    report = indexer.index([broken])
    assert report.outcomes[0].error_code == "invalid_chunk_input"
    empty = generation(result)
    empty.occurrences = []
    assert indexer.index([empty]).outcomes[0].error_code == "empty_chunk_generation"


def test_dimension_mismatch_from_embedder_is_rejected(settings):
    class WrongDense(FakeDense):
        def embed_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]

    indexer, _, client = services(settings, dense=WrongDense())
    report = indexer.index([generation(sample_bid()[1])])
    assert report.outcomes[0].error_code == "dense_dimension_mismatch"


def test_invalid_sparse_vector_is_rejected(settings):
    from rfp.search.models import SparseVector

    class BadSparse(FakeSparse):
        def embed_documents(self, texts):
            return [SparseVector((3, 3), (1.0, 2.0)) for _ in texts]

    indexer, _, _ = services(settings, sparse=BadSparse())
    report = indexer.index([generation(sample_bid()[1])])
    assert report.outcomes[0].error_code == "invalid_sparse_vector"


def test_truncation_is_recorded_in_payload(settings):
    class Tiny(FakeDense):
        max_tokens = 3

    indexer, _, client = services(settings, dense=Tiny())
    report = indexer.index([generation(sample_bid()[1])])
    assert report.outcomes[0].truncated_inputs == 1
    payload = all_points(client, report.collection)[0].payload
    assert payload["embedding_truncated"] is True
    assert "embedding_input_truncated" in payload["warnings"]


def test_profile_change_uses_separate_collection(settings):
    cache = MemoryCache()
    first, _, client = services(settings, cache=cache)
    gens = [generation(sample_bid()[1])]
    a = first.index(gens)
    second, _, _ = services(settings, client=client, cache=cache, dense=FakeDense(version="2"))
    b = second.index(gens)
    assert a.collection != b.collection and a.profile_fingerprint != b.profile_fingerprint
    assert b.outcomes[0].cache_hits == 0  # caches never cross profiles


def test_load_local_artifacts_prefers_current_config(settings, tmp_path):
    result = sample_bid()[1]
    other = chunked(
        pdf_document(BID_A, result.relative_path, [["Different text PX-1."]]),
        ChunkConfig(target_tokens=30, max_tokens=40, overlap_tokens=8, **ESTIMATED),
    )
    (tmp_path / "a.json").write_text(result.model_dump_json())
    (tmp_path / "b.json").write_text(other.model_dump_json())
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "summary.json").write_text("{}")
    gens, failures = load_local_artifacts(tmp_path, settings)
    assert [g.generation_id for g in gens] == [result.generation_id]
    assert failures == [{"path": "broken.json", "code": "invalid_chunk_artifact"}]


def test_load_local_artifacts_reports_ambiguity(settings, tmp_path):
    a = chunked(pdf_document(BID_A, "x.pdf", [["Alpha text PX-1."]]))
    b = chunked(pdf_document(BID_A, "x.pdf", [["Beta text PX-2."]]))
    (tmp_path / "a.json").write_text(a.model_dump_json())
    (tmp_path / "b.json").write_text(b.model_dump_json())
    gens, failures = load_local_artifacts(tmp_path, settings)
    assert gens == [] and failures == [{"path": "x.pdf", "code": "ambiguous_local_generation"}]


def test_load_local_artifacts_missing_directory(settings, tmp_path):
    from rfp.search.errors import IndexingFailed

    with pytest.raises(IndexingFailed) as error:
        load_local_artifacts(tmp_path / "missing", settings)
    assert error.value.code == "input_directory_not_found"


def test_point_ids_differ_per_generation():
    occurrence = uuid.uuid4()
    assert point_id(occurrence, uuid.uuid4()) != point_id(occurrence, uuid.uuid4())
