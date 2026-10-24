from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_ingestion_service import database as database
from test_ingestion_service import store as store

from rfp.db.models import EmbeddingCacheEntry
from rfp.schemas.chunks import sha256
from rfp.search.cache import (
    CacheCorrupt,
    CachedEmbedding,
    LocalEmbeddingCache,
    R2EmbeddingCache,
    artifact_key,
    decode_artifact,
    encode_artifact,
)
from rfp.search.errors import CacheUnavailable
from rfp.search.models import SparseVector, compute_profile
from rfp.storage.r2 import StorageError

PROFILE = compute_profile({"model": "fake/dense", "dim": 4}, {"model": "fake/bm25"})
OTHER = compute_profile({"model": "fake/dense", "dim": 4, "version": "2"}, {"model": "fake/bm25"})


def entry(text: str, nnz: int = 2) -> CachedEmbedding:
    return CachedEmbedding(
        content_hash=sha256(text.encode()),
        dense=(0.5, 0.5, 0.5, 0.5),
        sparse=SparseVector(tuple(range(1, nnz + 1)), tuple(float(i) for i in range(nnz))),
        token_count=7,
        truncated=False,
    )


def test_artifact_roundtrip_including_empty_sparse():
    entries = [entry("a"), entry("b", nnz=0)]
    data = encode_artifact(PROFILE, entries)
    assert decode_artifact(data, PROFILE) == entries


def test_artifact_rejects_other_profile_and_corruption():
    data = encode_artifact(PROFILE, [entry("a")])
    with pytest.raises(CacheCorrupt):
        decode_artifact(data, OTHER)
    with pytest.raises(CacheCorrupt):
        decode_artifact(data[:-2], PROFILE)
    with pytest.raises(CacheCorrupt):
        decode_artifact(b"garbage" + data, PROFILE)
    with pytest.raises(ValueError):
        encode_artifact(PROFILE, [])


def test_key_includes_profile():
    assert artifact_key(PROFILE, "d") != artifact_key(OTHER, "d")
    assert artifact_key(PROFILE, "d") == f"embeddings/{PROFILE.fingerprint}/d.bin"


def test_local_cache_hit_miss_and_profile_isolation(tmp_path):
    cache = LocalEmbeddingCache(tmp_path)
    a, b = entry("a"), entry("b")
    assert cache.load(PROFILE, [a.content_hash]) == {}
    cache.store(PROFILE, [a, b])
    cache.store(PROFILE, [entry("c")])
    found = cache.load(PROFILE, [a.content_hash, b.content_hash, "f" * 64])
    assert found == {a.content_hash: a, b.content_hash: b}
    assert cache.load(OTHER, [a.content_hash]) == {}


def test_local_cache_skips_tampered_artifact(tmp_path):
    cache = LocalEmbeddingCache(tmp_path)
    a = entry("a")
    cache.store(PROFILE, [a])
    artifact = next((tmp_path / PROFILE.fingerprint).glob("*.bin"))
    artifact.write_bytes(artifact.read_bytes() + b"x")
    assert cache.load(PROFILE, [a.content_hash]) == {}


def test_local_cache_corrupt_index_raises(tmp_path):
    cache = LocalEmbeddingCache(tmp_path)
    cache.store(PROFILE, [entry("a")])
    (tmp_path / PROFILE.fingerprint / "index.json").write_text("{broken")
    with pytest.raises(CacheCorrupt):
        cache.load(PROFILE, [entry("a").content_hash])


def test_r2_cache_store_and_load(database, store):
    cache = R2EmbeddingCache(store, database)
    a, b = entry("a"), entry("b")
    cache.store(PROFILE, [a, b])
    (key,) = store.objects
    assert key.startswith(f"embeddings/{PROFILE.fingerprint}/")
    assert cache.load(PROFILE, [a.content_hash, b.content_hash]) == {
        a.content_hash: a,
        b.content_hash: b,
    }
    assert cache.load(OTHER, [a.content_hash]) == {}
    cache.store(PROFILE, [a])  # re-store updates pointer, never duplicates
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(EmbeddingCacheEntry)) == 2


def test_r2_read_failure_never_falls_back(database, store):
    cache = R2EmbeddingCache(store, database, attempts=1)
    a = entry("a")
    cache.store(PROFILE, [a])
    store.download.side_effect = StorageError("down")
    with pytest.raises(CacheUnavailable) as error:
        cache.load(PROFILE, [a.content_hash])
    assert error.value.code == "embedding_cache_unavailable" and error.value.retryable


def test_r2_write_failure_raises_without_pointer_rows(database):
    storage = Mock()
    storage.upload.side_effect = StorageError("down")
    cache = R2EmbeddingCache(storage, database, attempts=1)
    with pytest.raises(CacheUnavailable):
        cache.store(PROFILE, [entry("a")])
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(EmbeddingCacheEntry)) == 0


def test_r2_tampered_artifact_is_recomputed(database, store):
    cache = R2EmbeddingCache(store, database)
    a = entry("a")
    cache.store(PROFILE, [a])
    (key,) = store.objects
    store.objects[key] = store.objects[key] + b"tamper"
    assert cache.load(PROFILE, [a.content_hash]) == {}
