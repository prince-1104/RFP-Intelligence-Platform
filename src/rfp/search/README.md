# `rfp.search` — Phase 4 hybrid evidence search

| Module | Responsibility |
| --- | --- |
| `models.py` | Embedder/reranker protocols, FastEmbed wrappers, vector validation, embedding profile, `ModelRuntime` (lazy, bounded concurrency) |
| `cache.py` | Binary embedding artifacts (`RFPEMB1`), `LocalEmbeddingCache`, `R2EmbeddingCache` (R2 + `embedding_cache_entries`) |
| `collection.py` | Qdrant client, sanitized error mapping, profile-specific collection creation/verification, payload indexes |
| `payload.py` | Deterministic point IDs, citation payloads, `Citation` reconstruction |
| `indexing.py` | Idempotent per-generation staging/verify/activate/supersede; local and PostgreSQL generation loaders |
| `jobs.py` | `ProcessingJob(kind="indexing")` registration/processing, `chunk_index_states` |
| `query.py`, `identifiers.py` | Deterministic normalization, additive synonym expansion, identifier extraction |
| `fusion.py` | Weighted reciprocal rank fusion |
| `service.py` | `SearchService`: branch queries, fusion, reranking, duplicate collapse, diagnostics, logging |
| `wiring.py` | Composition for API, CLI and Celery |

Agent/tool usage (future phases) should call the library directly:

```python
from rfp.schemas.search import SearchRequest, SearchFilters
from rfp.search.wiring import search_service

response = search_service(settings).search(
    SearchRequest(query="PX-420 quantity", filters=SearchFilters(bid_ids=[bid_id]))
)
for result in response.results:
    print(result.text, result.citation.relative_path, result.citation.page_start)
```

Tests inject deterministic fakes through `ModelRuntime(config, dense_factory=..., ...)` and an
in-memory `QdrantClient(":memory:")`; see `tests/search_fakes.py`. Unit tests never download models.
