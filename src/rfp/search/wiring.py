"""Composition of the shared indexing/search services for API, CLI and Celery entry points."""

from pathlib import Path

from sqlalchemy.engine import Engine

from rfp.core.config import Settings
from rfp.search.cache import LocalEmbeddingCache, R2EmbeddingCache
from rfp.search.collection import create_qdrant_client
from rfp.search.errors import CacheUnavailable
from rfp.search.indexing import IndexingService
from rfp.search.models import shared_runtime
from rfp.search.service import SearchService


def production_indexing_service(settings: Settings, engine: Engine) -> IndexingService:
    if not settings.r2_configured:
        # Production embedding artifacts require R2; there is no local-storage fallback.
        raise CacheUnavailable(
            "R2 is not configured; production indexing cannot persist the embedding cache",
            "r2_not_configured",
        )
    from rfp.storage.r2 import CloudflareR2Storage

    cache = R2EmbeddingCache(
        CloudflareR2Storage(settings),
        engine,
        attempts=settings.ingestion_storage_attempts,
        max_bytes=settings.search.max_cache_artifact_bytes,
    )
    return IndexingService(
        shared_runtime(settings.search), create_qdrant_client(settings), cache, settings
    )


def local_indexing_service(settings: Settings, cache_dir: Path | None = None) -> IndexingService:
    cache = LocalEmbeddingCache(
        cache_dir or Path(settings.search.local_cache_dir),
        settings.search.max_cache_artifact_bytes,
    )
    return IndexingService(
        shared_runtime(settings.search), create_qdrant_client(settings), cache, settings
    )


def search_service(settings: Settings) -> SearchService:
    return SearchService(shared_runtime(settings.search), create_qdrant_client(settings), settings)
