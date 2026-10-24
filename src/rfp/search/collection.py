"""Profile-specific Qdrant collection management; never deletes or recreates collections."""

import logging
import re
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from rfp.core.config import Settings
from rfp.core.logging import log_event
from rfp.search.errors import (
    CollectionIncompatible,
    IndexUnavailable,
    QdrantUnavailable,
    SearchServiceError,
)
from rfp.search.models import EmbeddingProfile, collection_name

logger = logging.getLogger("rfp.search.collection")

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
MIN_SERVER_VERSION = (1, 10)

KEYWORD_FIELDS = (
    "bid_id",
    "document_id",
    "doc_type",
    "chunk_type",
    "chunk_generation_id",
    "relative_path",
    "profile_fingerprint",
    "identifiers",
    "content_hash",
)
INTEGER_FIELDS = ("addendum_number",)
BOOL_FIELDS = ("index_active",)


def create_qdrant_client(settings: Settings) -> QdrantClient:
    api_key = settings.qdrant_api_key.get_secret_value() or None
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=api_key,
        timeout=settings.search.qdrant_timeout_seconds,
        prefer_grpc=False,
        # Compatibility is verified explicitly (and sanitized) by CollectionManager.
        check_compatibility=False,
    )


def qdrant_call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except SearchServiceError:
        raise
    except UnexpectedResponse as exc:
        status = exc.status_code or 0
        if status == 404:
            raise IndexUnavailable() from None
        if status in {401, 403}:
            raise QdrantUnavailable(
                "Vector store rejected the configured credentials", "vector_store_unauthorized"
            ) from None
        raise QdrantUnavailable() from None
    except (ResponseHandlingException, httpx.HTTPError, OSError, TimeoutError):
        raise QdrantUnavailable() from None


def _version(text: str) -> tuple[int, int]:
    match = re.match(r"^v?(\d+)\.(\d+)", text)
    if not match:
        raise QdrantUnavailable("Vector store version is unknown", "vector_store_version_unknown")
    return int(match.group(1)), int(match.group(2))


class CollectionManager:
    def __init__(self, client: QdrantClient, base_name: str, profile: EmbeddingProfile):
        self.client = client
        self.profile = profile
        self.name = collection_name(base_name, profile)

    def _is_local(self) -> bool:
        # qdrant-client local mode (tests/offline); it has no server version endpoint.
        return getattr(self.client, "_client", None).__class__.__name__ == "QdrantLocal"

    def check_server(self) -> str:
        if self._is_local():
            return "local"
        version = qdrant_call(self.client.info).version
        if _version(version) < MIN_SERVER_VERSION:
            raise QdrantUnavailable(
                "Qdrant server must be at least 1.10 for the Query API and sparse IDF",
                "vector_store_version_unsupported",
            )
        return version

    def exists(self) -> bool:
        return qdrant_call(lambda: self.client.collection_exists(self.name))

    def ensure(self) -> None:
        self.check_server()
        if not self.exists():
            qdrant_call(
                lambda: self.client.create_collection(
                    collection_name=self.name,
                    vectors_config={
                        DENSE_VECTOR: models.VectorParams(
                            size=self.profile.dense_dim, distance=models.Distance.COSINE
                        )
                    },
                    sparse_vectors_config={
                        SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)
                    },
                )
            )
            log_event(logger, "collection_created", collection=self.name)
        self.verify()
        self._ensure_payload_indexes()

    def verify(self) -> models.CollectionInfo:
        if not self.exists():
            raise IndexUnavailable()
        info = qdrant_call(lambda: self.client.get_collection(self.name))
        configured = info.config.params.vectors
        vectors = configured if isinstance(configured, dict) else {}
        sparse = info.config.params.sparse_vectors or {}
        dense = vectors.get(DENSE_VECTOR)
        sparse_params = sparse.get(SPARSE_VECTOR)
        if (
            dense is None
            or dense.size != self.profile.dense_dim
            or dense.distance != models.Distance.COSINE
            or sparse_params is None
            or sparse_params.modifier != models.Modifier.IDF
            or set(vectors) != {DENSE_VECTOR}
            or set(sparse) != {SPARSE_VECTOR}
        ):
            log_event(logger, "collection_incompatible", logging.ERROR, collection=self.name)
            raise CollectionIncompatible()
        return info

    def _ensure_payload_indexes(self) -> None:
        if self._is_local():
            return
        existing = qdrant_call(lambda: self.client.get_collection(self.name)).payload_schema or {}
        wanted: list[tuple[str, Any]] = [
            *((name, models.PayloadSchemaType.KEYWORD) for name in KEYWORD_FIELDS),
            *((name, models.PayloadSchemaType.INTEGER) for name in INTEGER_FIELDS),
            *((name, models.PayloadSchemaType.BOOL) for name in BOOL_FIELDS),
        ]
        for name, schema in wanted:
            if name in existing:
                continue
            qdrant_call(
                partial(
                    self.client.create_payload_index,
                    self.name,
                    field_name=name,
                    field_schema=schema,
                    wait=True,
                )
            )

    def diagnostics(self) -> dict[str, Any]:
        if not self.exists():
            return {"collection": self.name, "exists": False}
        info = self.verify()
        return {
            "collection": self.name,
            "exists": True,
            "status": str(info.status),
            "points_count": info.points_count,
            "payload_indexes": sorted((info.payload_schema or {}).keys()),
            "embedding_profile": self.profile.fingerprint,
        }
