# RFP platform architecture through Phase 4

The API, operator CLI and Celery worker share Settings, the SQLAlchemy model/engine factory,
the existing R2 Storage protocol and application logging conventions.

```mermaid
flowchart LR
  Folder[Operator folder] --> Discovery[Bounded discovery and SHA-256]
  Discovery --> Local[Offline parse-folder]
  Local --> JSON[Validated local JSON]
  Discovery --> Register[Production ingest-folder]
  Register --> R2[Private Cloudflare R2 sources]
  Register --> DB[(PostgreSQL jobs and provenance)]
  Register --> Redis[Redis / Celery]
  Redis --> Worker[Idempotent per-bid worker]
  R2 --> Worker
  Worker --> Parser[Bounded subprocess]
  Parser --> PDF[PyMuPDF / pdfplumber]
  PDF --> OCR[Page-level Tesseract fallback]
  Parser --> HTML[BeautifulSoup / lxml]
  Parser --> Artifact[ParsedDocument schema]
  Artifact --> R2JSON[Private R2 JSON artifact]
  R2JSON --> DB
  DB --> API[Versioned job/document status API]
```

Only the production path persists R2/PostgreSQL state. Parser modules accept bytes and
non-secret options; offline parsing requires neither live credentials nor infrastructure.
Untrusted source content is data only; no LLM, remote link resolution or executable HTML is used.

Source IDs include bid identity, relative path and original source SHA-256. Jobs include sorted
source identities and parser configuration/version. PostgreSQL advisory locks serialize all
registration/processing for a bid; document commits checkpoint successful work. R2 artifact
keys are stable by source/configuration. Duplicate terminal deliveries have no side effects.
Storage retry policy distinguishes transient provider/network failures from permanent errors.

ProcessingJob keeps its persisted parser configuration; workers never silently parse it with
different current defaults. Documents retain source/parsed keys and statuses, while job membership
is explicit in `job_documents`. Phase 1 rows can have null new provenance columns and remain valid.
The migration is reversible; downgrading maps partial states to failed before restoring the old
status constraint and removes only Phase 2 schema additions.

R2 writes, DB commits and broker publication cannot be atomic. Failed source registration can
leave reusable orphan objects. Queue publication follows DB commit; `dispatch-job` recovers
queued or interrupted jobs after broker/worker outages. Terminal parse failures are preserved.

Provenance includes one-based physical PDF pages and HTML element/heading/ID/table locators.
Native text and tables are both retained. OCR scores are recognition diagnostics. Scanned table
text is retained with a warning and no invented cells. No chunking, indexing, semantic deduplication,
embeddings, retrieval, agents, comparison or frontend is present in Phase 2.

## Phase 3 preparation

```mermaid
flowchart LR
  Parsed[Immutable ParsedDocument JSON] --> Normalize[Versioned normalization]
  Normalize --> Prose[Section and sentence-aware prose]
  Normalize --> Tables[Whole table row groups]
  Prose --> Result[Validated ChunkingResult]
  Tables --> Result
  Result --> Content[Canonical ChunkContent by exact text hash]
  Result --> Occurrence[Distinct DocumentChunk source occurrences]
  Result --> R2[Private revision/config R2 artifact]
  Content --> Generation[Transactional ChunkGeneration activation]
  Occurrence --> Generation
  R2 --> Generation
  Generation --> Active[Active generations for Phase 4]
  Generation --> History[Historical provenance retained]
```

`chunk-folder` operates locally without infrastructure or `.env`. `chunk-bid` registers production
jobs against current parsed document revisions and queues `rfp.chunk` on the existing Celery app.
There is no second Settings, storage adapter, DB connection layer or job framework. The existing
job-status API understands both ingestion and chunk jobs; generation inspection has a versioned
endpoint. Preparation is operator-triggered after parsing.

Source SHA-256 detects duplicate files, canonical SHA-256 identifies reusable normalized text,
and every occurrence retains its own document authority and locator. Shared content never means
shared source authority. Section metadata stays occurrence-specific; canonical text is the exact
text whose hash Phase 4 may use for embedding computation reuse. Lists/headings/OCR/table rows
are distinct chunk types. PDF ranges cover all contributing segments; HTML uses null pages.

Prose targets 600/800 estimated tokens with 100 overlap within continuous sections only. The
replaceable default counter uses ceil(UTF-8 bytes / 4), not an embedding-model tokenizer. Whole
table rows retain repeated headers, cell arrays and row/page citations; oversized indivisible
rows are warned rather than sliced. Exact whole-table prose copies are suppressed only when
unambiguous; uncertain duplicates and repeated legal statements remain evidence.

Migration 0003 introduces canonical content, source occurrences, generation/job membership and
parsed-artifact integrity hashes. A generation binds document revision, exact parsed-artifact
SHA-256 and full effective config/version hash. DB constraints enforce content/occurrence/input
uniqueness and one active generation per bid/source path. Prior generations remain historical.

Processing holds the existing per-bid advisory lock. Source input identity is snapshotted during
registration and rechecked against the latest document before execution. R2 reads/writes use
existing bounded retry helpers. Only a successfully stored/validated generation is persisted;
canonical content and occurrences are inserted with atomic activation in one DB transaction.
A failure cannot replace active chunks. Partial results preserve a prior complete generation.
Cross-bid canonical writes are conflict-safe; duplicate task deliveries create no new rows.

Legacy Phase 2 parsed hashes are backfilled once. Adding a new document processes just that new
document, while a source/config change creates an identifiable generation. A broker outage can
leave a queued job; `dispatch-job` recovers it. R2/DB are not a distributed transaction, so failed
DB activation can leave an orphan deterministic artifact but no partial active state.

Before Phase 4, select the embedding model/tokenizer, include model/input transformation in the
embedding-cache identity, and use active occurrences for metadata filters and citations. Do not
collapse addendum/RFP authority or cross-bid access boundaries when reusing canonical content.
No embeddings, Qdrant writes, BM25, retrieval, agents, extraction or reconciliation are implemented.

## Phase 4 hybrid search

```mermaid
flowchart LR
  Active[Active ChunkGenerations or local ChunkingResult JSON] --> Validate[Provenance validation]
  Validate --> Cache{Embedding cache by content hash + profile}
  Cache -- miss --> Embed[FastEmbed bge-small dense + Qdrant/bm25 sparse]
  Embed --> Store[Versioned binary artifact: R2 + PostgreSQL pointers / local dir]
  Cache -- hit --> Points
  Store --> Points[One point per source occurrence, index_active=false]
  Points --> Verify[Count/profile verification] --> Activate[set index_active=true]
  Activate --> Supersede[Retire other generations of same bid/path]
  Query[Query] --> Plan[Normalize, identifiers, additive synonyms]
  Plan --> Batch[query_batch_points: dense, sparse, identifier]
  Batch --> RRF[Application weighted RRF k=60]
  RRF --> Rerank[ms-marco-MiniLM cross-encoder + identifier bonus]
  Rerank --> Collapse[Collapse by content hash, attach all citations]
  Collapse --> Evidence[Evidence + citations + score components]
```

`rfp.search` is a library first: `SearchService.search(SearchRequest) -> SearchResponse` and
`IndexingService.index(generations)` are what the API, CLI, Celery task and future LangGraph tools
call. All inference happens through `ModelRuntime`, a lazy per-process loader guarded by a bounded
semaphore; the API additionally runs searches in a capacity-limited worker thread with a timeout.
Model downloads happen only via `init-models` unless `SEARCH__ALLOW_MODEL_DOWNLOAD=true`.

**Embedding profile.** A SHA-256 over dense identity (model, source, ONNX file SHA-256, dim,
max tokens, normalization, FastEmbed version), sparse identity (model, stopwords hash, BM25
k/b/avg_len/language, IDF modifier) and the input representation `canonical_text_v1`. The
collection is `{QDRANT_COLLECTION}__{profile[:12]}` with named vectors `dense` (cosine, 384) and
`bm25` (sparse, `Modifier.IDF`). A model or parameter change therefore yields a new collection and
a cold cache; models are never mixed in one collection. An existing collection with a different
vector configuration raises `collection_incompatible`; collections are never deleted or recreated.

**Text representation.** Exact canonical chunk text is embedded, so identical text in different
documents shares vectors while each occurrence keeps its own bid/document/addendum/page/locator
payload. The BGE query instruction is applied to dense queries only. Inputs beyond 512 tokens are
truncated by the tokenizer; this is recorded as `embedding_truncated` and a payload warning.

**Indexing.** Each generation is processed independently: unchanged detection (exact active/total
counts for the generation and profile), cache lookup, batched embedding of misses, cache write,
batched upsert with bounded retries, verification, activation and supersession. Any failure
deletes only that generation's inactive staging points, so previously active evidence stays
searchable. A transient Qdrant failure stops the run and leaves the remaining generations pending.
Production jobs reuse `ProcessingJob(kind="indexing")`, the per-bid advisory lock, deterministic
job IDs (bid + configured profile key + generation manifest), `chunk_index_states` per
generation/profile, deadline continuations and Celery retries for transient cache/vector-store
failures. Production embedding artifacts must go to R2; there is no local fallback.

**Retrieval.** Branch queries share one metadata filter that always includes `index_active=true`
and the profile fingerprint, plus optional bid, document, doc type, addendum, chunk type and path
filters. The identifier branch is a dense query restricted to points whose `identifiers` payload
contains a normalized or compact query identifier, because BM25 tokenization splits `PX-420`.
Fusion is weighted RRF over ranks (never raw scores). `hybrid_rerank` reranks the fused top
`rerank_depth` with the cross-encoder; matching identifiers add `identifier_rerank_bonus`. Under
`identifier_policy=require_evidence` a query naming identifiers that no candidate contains returns
no results with `identifier_evidence_not_found` instead of semantically similar but wrong evidence.
Results are collapsed by content hash and every filter-valid occurrence is attached as an
additional citation. Reranker/sparse failures are errors unless `allow_degraded` is enabled, in
which case the response is flagged `degraded` with a warning.

**Vector-store compatibility.** `qdrant-client` 1.19.x is pinned against Qdrant server 1.19.2
(cloud and compose). The client's background compatibility warning is enabled, and
`CollectionManager.check_server` enforces the same rule synchronously (equal major, minor
difference ≤ 1, server ≥ 1.10) before indexing or searching.

Structured logs record timings, candidate counts, score components, profile and a query hash, not
query text, document text, signed URLs or secrets. Phase 4 returns evidence only.

## Phase 5 extraction

```mermaid
flowchart LR
  Goal[extract-bid] --> Orchestrator
  Orchestrator --> Retrieval
  Retrieval --> Search[SearchService.search]
  Orchestrator --> Identity[Identity specialist]
  Orchestrator --> Logistics[Logistics specialist]
  Orchestrator --> Commercial[Commercial specialist]
  Orchestrator --> Product[Product specialist]
  Identity --> Validator
  Logistics --> Reconcile[Addendum reconciliation]
  Commercial --> Validator
  Product --> Validator
  Reconcile --> Validator
  Validator -->|reject, max 2 retries| Orchestrator
  Validator --> Summary[Summary specialist]
  Summary --> JSON[Bid JSON and trace]
```

`extract-bid` is read-only against loopback Qdrant. Independent field groups call the model
concurrently. Search calls on the shared client are serialized. The validator accepts a value
only when the cited hit belongs to the bid and the value is supported by that hit's text.
Addendum number alone does not override a field. Live model calls use `LLM_PROVIDER`,
`LLM_MODEL` and `LLM_API_KEY`. Missing credentials stop the command.
