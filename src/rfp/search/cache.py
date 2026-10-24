"""Durable embedding cache keyed by canonical content hash + complete embedding profile.

Vectors live in immutable, versioned binary batch artifacts (SHA-256 addressed). Production
artifacts go to R2 with PostgreSQL pointer rows; offline development uses the same artifact
format in a local directory. There is deliberately no fallback between the two.
"""

import io
import json
import logging
import re
import struct
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol

import numpy as np
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from rfp.core.logging import log_event
from rfp.db.models import EmbeddingCacheEntry
from rfp.ingestion.service import download_source, storage_call
from rfp.schemas.chunks import sha256, stable_json
from rfp.search.errors import CacheUnavailable
from rfp.search.models import MAX_SPARSE_INDEX, EmbeddingProfile, SparseVector
from rfp.storage.r2 import Storage, StorageError

logger = logging.getLogger("rfp.search.cache")

MAGIC = b"RFPEMB1\n"
FORMAT_VERSION = 1
MAX_HEADER_BYTES = 64 * 1024 * 1024
HASH = re.compile(r"^[a-f0-9]{64}$")
CACHE_NAMESPACE = uuid.UUID("0b8f6f3e-58f5-4d5e-9d3c-6c0c2b8e4a10")


class CacheCorrupt(CacheUnavailable):
    code = "embedding_cache_corrupt"
    retryable = False
    message = "Embedding cache artifact failed validation"


@dataclass(frozen=True)
class CachedEmbedding:
    content_hash: str
    dense: tuple[float, ...]
    sparse: SparseVector
    token_count: int
    truncated: bool


def encode_artifact(profile: EmbeddingProfile, entries: Sequence[CachedEmbedding]) -> bytes:
    if not entries:
        raise ValueError("Embedding artifact requires entries")
    dim = profile.dense_dim
    header = {
        "format_version": FORMAT_VERSION,
        "profile_fingerprint": profile.fingerprint,
        "dense_dim": dim,
        "dtype": "float32-le/uint32-le",
        "entries": [
            {
                "content_hash": entry.content_hash,
                "sparse_nnz": len(entry.sparse.indices),
                "token_count": entry.token_count,
                "truncated": entry.truncated,
            }
            for entry in entries
        ],
    }
    header_bytes = stable_json(header)
    dense = np.asarray([entry.dense for entry in entries], dtype="<f4")
    if dense.shape != (len(entries), dim) or not np.all(np.isfinite(dense)):
        raise ValueError("Dense vectors do not match the profile dimension")
    output = io.BytesIO()
    output.write(MAGIC)
    output.write(struct.pack("<I", len(header_bytes)))
    output.write(header_bytes)
    output.write(dense.tobytes())
    for entry in entries:
        output.write(np.asarray(entry.sparse.indices, dtype="<u4").tobytes())
        output.write(np.asarray(entry.sparse.values, dtype="<f4").tobytes())
    return output.getvalue()


def decode_artifact(data: bytes, profile: EmbeddingProfile) -> list[CachedEmbedding]:
    try:
        if not data.startswith(MAGIC):
            raise ValueError
        offset = len(MAGIC)
        (header_length,) = struct.unpack_from("<I", data, offset)
        offset += 4
        if header_length > MAX_HEADER_BYTES:
            raise ValueError
        header = json.loads(data[offset : offset + header_length])
        offset += header_length
        dim = int(header["dense_dim"])
        entries = header["entries"]
        if (
            header.get("format_version") != FORMAT_VERSION
            or header.get("profile_fingerprint") != profile.fingerprint
            or dim != profile.dense_dim
            or not isinstance(entries, list)
            or not entries
        ):
            raise ValueError
        dense_bytes = len(entries) * dim * 4
        dense = np.frombuffer(data, dtype="<f4", count=len(entries) * dim, offset=offset).reshape(
            len(entries), dim
        )
        offset += dense_bytes
        if not np.all(np.isfinite(dense)):
            raise ValueError
        result = []
        for row, entry in enumerate(entries):
            nnz = int(entry["sparse_nnz"])
            content_hash = str(entry["content_hash"])
            if nnz < 0 or not HASH.match(content_hash):
                raise ValueError
            indices = np.frombuffer(data, dtype="<u4", count=nnz, offset=offset)
            offset += nnz * 4
            values = np.frombuffer(data, dtype="<f4", count=nnz, offset=offset)
            offset += nnz * 4
            index_list = [int(i) for i in indices]
            if (
                any(b <= a for a, b in zip(index_list, index_list[1:], strict=False))
                or any(i > MAX_SPARSE_INDEX for i in index_list)
                or not np.all(np.isfinite(values))
            ):
                raise ValueError
            result.append(
                CachedEmbedding(
                    content_hash=content_hash,
                    dense=tuple(float(v) for v in dense[row]),
                    sparse=SparseVector(tuple(index_list), tuple(float(v) for v in values)),
                    token_count=int(entry["token_count"]),
                    truncated=bool(entry["truncated"]),
                )
            )
        if offset != len(data):
            raise ValueError
        return result
    except CacheCorrupt:
        raise
    except Exception:
        raise CacheCorrupt() from None


def artifact_key(profile: EmbeddingProfile, digest: str) -> str:
    return f"embeddings/{profile.fingerprint}/{digest}.bin"


class EmbeddingCache(Protocol):
    kind: str

    def load(
        self, profile: EmbeddingProfile, hashes: Sequence[str]
    ) -> dict[str, CachedEmbedding]: ...

    def store(self, profile: EmbeddingProfile, entries: Sequence[CachedEmbedding]) -> None: ...


class LocalEmbeddingCache:
    """Offline development cache: same artifact format, JSON pointer index per profile."""

    kind = "local"

    def __init__(self, root: Path, max_bytes: int = 256 * 1024 * 1024):
        self.root = root
        self.max_bytes = max_bytes

    def _directory(self, profile: EmbeddingProfile) -> Path:
        return self.root / profile.fingerprint

    def _index(self, profile: EmbeddingProfile) -> dict[str, dict]:
        path = self._directory(profile) / "index.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format_version") != FORMAT_VERSION:
                raise ValueError
            return dict(data["entries"])
        except Exception:
            raise CacheCorrupt() from None

    def load(self, profile: EmbeddingProfile, hashes: Sequence[str]) -> dict[str, CachedEmbedding]:
        index = self._index(profile)
        wanted = defaultdict(list)
        for content_hash in set(hashes):
            pointer = index.get(content_hash)
            if pointer:
                wanted[pointer["artifact"]].append(content_hash)
        found: dict[str, CachedEmbedding] = {}
        for digest, members in wanted.items():
            path = self._directory(profile) / f"{digest}.bin"
            try:
                if path.stat().st_size > self.max_bytes:
                    raise CacheCorrupt()
                data = path.read_bytes()
            except OSError:
                log_event(logger, "embedding_cache_artifact_missing", logging.WARNING)
                continue
            if sha256(data) != digest:
                log_event(logger, "embedding_cache_artifact_invalid", logging.WARNING)
                continue
            decoded = {entry.content_hash: entry for entry in decode_artifact(data, profile)}
            found.update({h: decoded[h] for h in members if h in decoded})
        return found

    def store(self, profile: EmbeddingProfile, entries: Sequence[CachedEmbedding]) -> None:
        if not entries:
            return
        data = encode_artifact(profile, entries)
        digest = sha256(data)
        directory = self._directory(profile)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.bin"
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
        index = self._index(profile)
        for position, entry in enumerate(entries):
            index[entry.content_hash] = {"artifact": digest, "position": position}
        index_path = directory / "index.json"
        temporary_index = index_path.with_suffix(".tmp")
        temporary_index.write_text(
            json.dumps({"format_version": FORMAT_VERSION, "entries": index}, sort_keys=True),
            encoding="utf-8",
        )
        temporary_index.replace(index_path)


class R2EmbeddingCache:
    """Production cache: R2 artifacts via the existing Storage protocol + PostgreSQL pointers."""

    kind = "r2"

    def __init__(self, storage: Storage, engine: Engine, attempts: int = 3, max_bytes: int = 0):
        self.storage = storage
        self.engine = engine
        self.attempts = attempts
        self.max_bytes = max_bytes or 256 * 1024 * 1024

    def load(self, profile: EmbeddingProfile, hashes: Sequence[str]) -> dict[str, CachedEmbedding]:
        unique = sorted(set(hashes))
        rows: list[EmbeddingCacheEntry] = []
        with Session(self.engine) as session:
            for start in range(0, len(unique), 500):
                rows.extend(
                    session.scalars(
                        select(EmbeddingCacheEntry).where(
                            EmbeddingCacheEntry.profile_fingerprint == profile.fingerprint,
                            EmbeddingCacheEntry.content_hash.in_(unique[start : start + 500]),
                        )
                    ).all()
                )
        grouped: dict[tuple[str, str], list[EmbeddingCacheEntry]] = defaultdict(list)
        for row in rows:
            if row.dense_dim == profile.dense_dim:
                grouped[(row.artifact_key, row.artifact_sha256)].append(row)
        found: dict[str, CachedEmbedding] = {}
        for (key, digest), members in grouped.items():
            try:
                data = storage_call(
                    partial(download_source, self.storage, key, self.max_bytes), self.attempts
                )
            except (StorageError, ValueError):
                # No silent recomputation or local fallback when production storage fails.
                log_event(logger, "embedding_cache_read_failed", logging.ERROR)
                raise CacheUnavailable() from None
            if sha256(data) != digest:
                log_event(logger, "embedding_cache_artifact_invalid", logging.WARNING)
                continue
            decoded = decode_artifact(data, profile)
            for row in members:
                if row.position < len(decoded):
                    entry = decoded[row.position]
                    if entry.content_hash == row.content_hash:
                        found[row.content_hash] = entry
        return found

    def store(self, profile: EmbeddingProfile, entries: Sequence[CachedEmbedding]) -> None:
        if not entries:
            return
        data = encode_artifact(profile, entries)
        digest = sha256(data)
        key = artifact_key(profile, digest)

        def upload():
            self.storage.upload(
                key,
                io.BytesIO(data),
                content_type="application/octet-stream",
                metadata={"profile": profile.short, "format": str(FORMAT_VERSION)},
            )

        try:
            storage_call(upload, self.attempts)
        except StorageError:
            log_event(logger, "embedding_cache_write_failed", logging.ERROR)
            raise CacheUnavailable() from None
        with Session(self.engine) as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
            for position, entry in enumerate(entries):
                values = {
                    "id": uuid.uuid5(
                        CACHE_NAMESPACE, f"{entry.content_hash}:{profile.fingerprint}"
                    ),
                    "content_hash": entry.content_hash,
                    "profile_fingerprint": profile.fingerprint,
                    "artifact_key": key,
                    "artifact_sha256": digest,
                    "position": position,
                    "dense_dim": profile.dense_dim,
                    "sparse_nnz": len(entry.sparse.indices),
                    "token_count": entry.token_count,
                    "truncated": entry.truncated,
                }
                statement = insert(EmbeddingCacheEntry).values(**values)
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["content_hash", "profile_fingerprint"],
                        set_={
                            name: statement.excluded[name]
                            for name in (
                                "artifact_key",
                                "artifact_sha256",
                                "position",
                                "dense_dim",
                                "sparse_nnz",
                                "token_count",
                                "truncated",
                            )
                        },
                    )
                )
            session.commit()
