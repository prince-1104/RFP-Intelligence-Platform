"""Sanitized search/indexing failures: codes and safe messages only, never provider detail."""


class SearchServiceError(Exception):
    code = "search_failed"
    status = 500
    retryable = False
    message = "Search could not be completed"

    def __init__(self, message: str | None = None, code: str | None = None):
        if message:
            self.message = message
        if code:
            self.code = code
        super().__init__(self.code)


class InvalidSearchRequest(SearchServiceError):
    code = "invalid_search_request"
    status = 422
    message = "Invalid search request"


class ModelUnavailable(SearchServiceError):
    code = "embedding_model_unavailable"
    status = 503
    retryable = True
    message = "Local embedding/reranker model is not available; run `python -m rfp.cli init-models`"


class InferenceFailed(SearchServiceError):
    code = "inference_failed"
    status = 503
    retryable = True
    message = "Local model inference failed"


class InvalidVector(SearchServiceError):
    code = "invalid_vector"
    status = 500
    message = "Embedding output failed validation"


class IndexUnavailable(SearchServiceError):
    code = "index_not_found"
    status = 503
    retryable = True
    message = "Search index for the configured embedding profile does not exist; run indexing"


class CollectionIncompatible(SearchServiceError):
    code = "collection_incompatible"
    status = 503
    message = (
        "Existing Qdrant collection does not match the embedding profile; "
        "rebuild into a new collection (see README) instead of mixing models"
    )


class QdrantUnavailable(SearchServiceError):
    code = "vector_store_unavailable"
    status = 503
    retryable = True
    message = "Vector store is unavailable or timed out"


class QdrantRejected(QdrantUnavailable):
    """A deterministic 4xx rejection (credentials, validation); repeating it cannot succeed."""

    code = "vector_store_request_rejected"
    status = 502
    retryable = False
    message = "Vector store rejected the request"


class CacheUnavailable(SearchServiceError):
    code = "embedding_cache_unavailable"
    status = 503
    retryable = True
    message = "Embedding cache storage failed"


class IndexingFailed(SearchServiceError):
    code = "indexing_failed"
    status = 500
    message = "Indexing failed"
