import threading
import time
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse
from search_fakes import FakeDense, FakeSparse, runtime

from rfp.schemas.search import SearchConfig
from rfp.search.collection import (
    BOOL_FIELDS,
    INTEGER_FIELDS,
    KEYWORD_FIELDS,
    CollectionManager,
    qdrant_call,
)
from rfp.search.errors import (
    CollectionIncompatible,
    IndexUnavailable,
    InferenceFailed,
    InvalidVector,
    ModelUnavailable,
    QdrantUnavailable,
)
from rfp.search.models import (
    ModelRuntime,
    compute_profile,
    configured_profile_key,
    validate_dense,
    validate_sparse,
)

PROFILE = compute_profile(FakeDense().identity(), FakeSparse().identity())


def unexpected(status):
    return UnexpectedResponse(status, "reason", b"secret body", httpx.Headers())


def test_qdrant_errors_are_sanitized():
    with pytest.raises(IndexUnavailable):
        qdrant_call(lambda: (_ for _ in ()).throw(unexpected(404)))
    with pytest.raises(QdrantUnavailable) as error:
        qdrant_call(lambda: (_ for _ in ()).throw(unexpected(403)))
    assert error.value.code == "vector_store_unauthorized"
    assert "secret" not in str(error.value) and error.value.__cause__ is None
    with pytest.raises(QdrantUnavailable) as error:
        qdrant_call(lambda: (_ for _ in ()).throw(httpx.ConnectError("https://key@host")))
    assert error.value.retryable and "host" not in error.value.message


def test_collection_created_with_named_vectors_and_idf():
    client = QdrantClient(":memory:")
    manager = CollectionManager(client, "rfp", PROFILE)
    manager.ensure()
    manager.ensure()  # idempotent
    params = client.get_collection(manager.name).config.params
    assert params.vectors["dense"].size == PROFILE.dense_dim
    assert params.vectors["dense"].distance == models.Distance.COSINE
    assert params.sparse_vectors["bm25"].modifier == models.Modifier.IDF
    assert manager.name == f"rfp__{PROFILE.short}"


def test_server_payload_indexes_and_version_check():
    client = MagicMock()
    client._client = object()
    client.info.return_value = models.VersionInfo(title="qdrant", version="1.19.2")
    client.collection_exists.return_value = True
    manager = CollectionManager(client, "rfp", PROFILE)
    info = MagicMock()
    info.config.params.vectors = {
        "dense": models.VectorParams(size=PROFILE.dense_dim, distance=models.Distance.COSINE)
    }
    info.config.params.sparse_vectors = {
        "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
    }
    info.payload_schema = {"bid_id": object()}
    client.get_collection.return_value = info
    manager.ensure()
    client.create_collection.assert_not_called()
    created = {call.kwargs["field_name"] for call in client.create_payload_index.call_args_list}
    assert created == set(KEYWORD_FIELDS + INTEGER_FIELDS + BOOL_FIELDS) - {"bid_id"}
    client.info.return_value = models.VersionInfo(title="qdrant", version="1.9.0")
    with pytest.raises(QdrantUnavailable) as error:
        manager.check_server()
    assert error.value.code == "vector_store_version_unsupported"
    client.delete_collection.assert_not_called()


@pytest.mark.parametrize(
    ("server", "client_version", "compatible"),
    [
        ("1.19.2", "1.19.1", True),
        ("1.20.0", "1.19.1", True),
        ("1.13.4", "1.19.1", False),
        ("2.0.0", "1.19.1", False),
    ],
)
def test_client_server_compatibility_enforced(monkeypatch, server, client_version, compatible):
    client = MagicMock()
    client._client = object()
    client.info.return_value = models.VersionInfo(title="qdrant", version=server)
    monkeypatch.setattr("rfp.search.collection.client_version", lambda: client_version)
    manager = CollectionManager(client, "rfp", PROFILE)
    if compatible:
        assert manager.check_server() == server
        return
    with pytest.raises(CollectionIncompatible) as error:
        manager.ensure()
    assert error.value.code == "vector_store_version_incompatible"
    client.create_collection.assert_not_called()


def test_remote_client_enables_compatibility_check(settings, monkeypatch):
    from rfp.search import collection

    built = MagicMock()
    monkeypatch.setattr(collection, "QdrantClient", built)
    collection.create_qdrant_client(settings)
    assert built.call_args.kwargs["check_compatibility"] is True
    assert built.call_args.kwargs["prefer_grpc"] is False


def test_vector_validation():
    assert len(validate_dense(np.ones(4), 4)) == 4
    for bad in (np.ones(3), np.zeros(4), np.array([1, np.nan, 1, 1]), np.ones((2, 2))):
        with pytest.raises(InvalidVector):
            validate_dense(bad, 4)
    assert validate_sparse([5, 1], [0.5, 1.0]).indices == (1, 5)
    for indices, values in (([1, 1], [1, 2]), ([-1], [1]), ([1], [np.inf]), ([1, 2], [1])):
        with pytest.raises(InvalidVector):
            validate_sparse(indices, values)


def test_profile_changes_with_any_identity_field():
    base = compute_profile({"model": "m", "dim": 4}, {"model": "s", "k": 1.2})
    assert base == compute_profile({"model": "m", "dim": 4}, {"model": "s", "k": 1.2})
    assert (
        base.fingerprint
        != compute_profile({"model": "m", "dim": 4}, {"model": "s", "k": 1.3}).fingerprint
    )
    assert (
        base.fingerprint
        != compute_profile({"model": "m", "dim": 8}, {"model": "s", "k": 1.2}).fingerprint
    )


def test_configured_profile_key_ignores_query_only_settings():
    config = SearchConfig()
    key = configured_profile_key(config, "rfp")
    assert key == configured_profile_key(SearchConfig(default_top_k=9), "rfp")
    assert key != configured_profile_key(SearchConfig(bm25_k=1.5), "rfp")
    assert key != configured_profile_key(config, "other")


def test_runtime_loads_lazily_once_and_wraps_failures():
    loads = []

    def factory():
        loads.append(1)
        return FakeDense()

    model_runtime = ModelRuntime(SearchConfig(), dense_factory=factory)
    assert model_runtime.loaded() == []
    _ = model_runtime.dense, model_runtime.dense
    assert loads == [1] and model_runtime.loaded() == ["dense"]
    with pytest.raises(InferenceFailed) as error:
        model_runtime.infer(lambda: 1 / 0, "dense_query_failed")
    assert error.value.code == "dense_query_failed" and error.value.__cause__ is None

    def broken():
        raise OSError("/secret/path missing")

    with pytest.raises(ModelUnavailable) as error:
        _ = ModelRuntime(SearchConfig(), sparse_factory=broken).sparse
    assert "/secret" not in error.value.message


def test_runtime_bounds_concurrent_inference():
    model_runtime = runtime(SearchConfig(max_concurrent_inference=2))
    active, peak, lock = [0], [0], threading.Lock()

    def work():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1

    threads = [threading.Thread(target=model_runtime.infer, args=(work,)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 2


def test_real_model_wrappers_refuse_unsupported_models_without_download(tmp_path):
    from rfp.search.models import FastEmbedDense

    config = SearchConfig(dense_model="not/a-model", model_cache_dir=str(tmp_path))
    with pytest.raises(ModelUnavailable) as error:
        FastEmbedDense(config)
    assert error.value.code == "unsupported_model"
    mismatch = SearchConfig(dense_dim=768, model_cache_dir=str(tmp_path))
    with pytest.raises(ModelUnavailable) as error:
        FastEmbedDense(mismatch)
    assert error.value.code == "dense_dimension_mismatch"


def test_missing_local_model_files_fail_offline(tmp_path):
    from rfp.search.models import FastEmbedDense

    with pytest.raises(ModelUnavailable):
        FastEmbedDense(SearchConfig(model_cache_dir=str(tmp_path / "empty")))
