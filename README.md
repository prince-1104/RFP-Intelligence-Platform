# RFP Intelligence Platform — Phase 4

Phase 2 adds document ingestion, PDF/HTML parsing, native tables and Tesseract OCR to the
Phase 1 infrastructure. Phase 3 adds normalization, structure-aware chunks, canonical content,
source occurrences and incremental generations. Phase 4 adds a hybrid RAG evidence search engine
(dense + BM25 + exact identifiers, RRF, cross-encoder reranking, citations) with incremental
Qdrant indexing, a REST API and CLI. Phase 5 adds a typed multi-agent extraction workflow
on that search service. A web UI remains optional and is not implemented.
The repository was empty at Phase 1 implementation start.

## Architecture

Python 3.12, FastAPI and Pydantic settings power the API. SQLAlchemy/PostgreSQL holds Bid,
Document and ProcessingJob records; Alembic owns schema changes. Redis is the Celery broker.
Qdrant stores hybrid dense/sparse search points (Phase 4); FastEmbed runs ONNX models locally. Cloudflare R2 is the sole object-storage adapter;
no filesystem or MinIO fallback exists. Tesseract and English data are installed in the backend
image and used on pages with insufficient native text. API, migration runner and worker share
that image. The operator CLI uploads sources to R2 and queues ingestion through Celery.

```
src/rfp/
  api/             app factory, health checks
  core/            settings, structured logging
  db/              models, engine, migrations
  storage/         storage protocol, R2 adapter, connectivity command
  workers/         Celery factory, entrypoint, diagnostic ping task
  schemas/         validated parsed document/page/block/table contracts
  ingestion/       discovery, parsers, OCR, process isolation and persistence
  chunking/        normalization, token estimates, structured chunks and incremental persistence
  search/          embeddings, cache, Qdrant indexing, hybrid retrieval, reranking, citations
  agents/          extraction orchestrator, specialists, reconciliation, validation, traces
  evaluation/      labeled retrieval judgments and metrics (Recall@5, MRR@10, nDCG@10)
evaluation/        labeled assessment retrieval dataset (55 cases, both bids)
scripts/           local-only evaluation, reranker parity, latency and memory benchmarks
tests/             offline unit and migration tests
```

Phase 1 UUID defaults remain available. Ingestion uses deterministic UUIDs for bid/source/job
identities. Statuses are constrained to pending/processing/completed/partially_completed/failed.
Foreign keys prevent removal of referenced bids; object keys and non-null Celery task IDs are
unique. Timestamps use server defaults; SQLAlchemy updates `updated_at` on ORM/Core updates.
Direct SQL writers must update that field themselves. No extracted-field schema is invented.

## Prerequisites and configuration

Install Docker Desktop with Linux containers and Docker Compose v2. For host development use
Python 3.12 and uv 0.13.5 (`python -m pip install uv==0.13.5`). Dependencies are locked in
`uv.lock`; use frozen installs. Image tags are pinned; base OS patches can still change.

Copy `.env.example` to `.env` (PowerShell: `Copy-Item .env.example .env`). Real credentials belong
only in that private, gitignored file or a deployment secret manager. Never commit it. The
example database password is strictly for local development. Change it for deployment, and
URL-encode reserved characters in the database connection string. Local infrastructure binds
only to loopback. Authentication, TLS termination and production network policy are outside
this foundation; do not expose this development Compose stack publicly.

Settings require valid `DATABASE_URL`, `REDIS_URL`, and `QDRANT_URL`. R2 can be absent at startup
and during tests. To use it, set `R2_ACCOUNT_ID` (endpoint derived automatically),
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET_NAME`. An explicit
`R2_ENDPOINT_URL` must be a Cloudflare R2 HTTPS endpoint; `R2_REGION` defaults to `auto`.
The adapter rejects incomplete configuration when instantiated. Provider/model/API-key settings
are reserved and never used to call an LLM. `TESSERACT_CMD` identifies the executable for OCR;
host installations may need an absolute Windows path.

For Qdrant Cloud, set `QDRANT_URL` to the cluster HTTPS endpoint and `QDRANT_API_KEY` in
private `.env`. The key is masked in settings and sent only in the `api-key` header.
Readiness checks both cluster health and authenticated collection-list access when a key is
configured; it does not create collections. Local Qdrant uses an empty API key. Compose still
starts its local Qdrant service, but the backend always checks the configured URL.

## Start with Docker

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl.exe http://localhost:8000/health/live
curl.exe http://localhost:8000/health/ready
```

The one-shot `migrate` service applies migrations before API/worker start. Redis and Qdrant must
also be healthy. API documentation is at http://localhost:8000/docs. Stop with
`docker compose down`; volumes are retained. Do not use `down -v` unless deleting local data
is intentional. Worker shutdown allows its bounded diagnostic tasks to finish.

Manual schema operations:

```powershell
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic check
```

## Host development and verification

```powershell
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen ruff check src tests examples
uv run --frozen ruff format --check src tests examples
uv run --frozen mypy src/rfp
```

Run only local infrastructure with `docker compose up -d postgres redis qdrant`. For Python
running on the host, change database/Redis/Qdrant hostnames in private `.env` to `localhost`,
then run:

```powershell
uv run --frozen alembic upgrade head
uv run --frozen uvicorn rfp.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Celery workers are intended to run in Linux containers. The `rfp.ping` diagnostic returns
`pong`, ignores stored results, and does not create database jobs. Check a running worker with:

```powershell
docker compose exec worker celery -A rfp.workers.entrypoint:app inspect ping
```

Liveness never contacts dependencies. Readiness performs bounded PostgreSQL `SELECT 1`, Redis
PING and Qdrant `/readyz` checks and returns 503 with dependency names when unavailable.
R2 is reported as `not_configured` or `configured_not_checked`; readiness does not certify
external access. LLMs are not checked. Request IDs are validated or regenerated and returned
in `X-Request-ID`. API errors use an `error` envelope with code, safe message and request ID.
Application logs use JSON events without URLs, headers, payloads or exception contents.
Uvicorn access logging is disabled to avoid logging query-string secrets.

## R2 connectivity

After configuring a private bucket and credentials with appropriate object permissions:

```powershell
docker compose run --rm api python -m rfp.storage.check
# Or with host dependencies installed:
uv run --frozen python -m rfp.storage.check
```

This issues `HeadBucket` only: it verifies bucket access, not write/delete permissions. Unit
tests exercise upload, streaming download, metadata, delete, presigning and sanitized failures
using Stubber/mocks. They need no R2 account or paid model. The adapter never sets public ACLs;
bucket public-access policy must also stay disabled. Presigned URLs are bearer credentials,
limited here to one hour; do not log or publish them. Downloads use 1 MiB chunks. The initial
upload uses S3 PutObject (single-object upload, no multipart implementation in this phase).

SQLite tests exercise portable constraints and migration/model parity. They do not replace
PostgreSQL integration verification. See `VERIFICATION.md` for checks actually executed here.

## Phase 2: offline parsing

Supported source formats are `.pdf`, `.html` and `.htm`, case-insensitive, recursively discovered
under any supplied folder. No folder names or assessment values are embedded in parser rules.
Hidden/temporary files, unsupported formats, symlinks and junctions are ignored with reasons.
Discovery reports ignored paths and enforces file count, individual size, total size and entry
limits. It hashes sources with SHA-256; duplicate bytes at different relative paths remain
distinct documents. Files that change between discovery and upload/parse are rejected.

Install the frozen dependencies, generate a synthetic sample, and parse it:

```powershell
uv sync --frozen
uv run --frozen python examples/create_sample.py
uv run --frozen python -m rfp.cli parse-folder --path artifacts/synthetic_bid --output artifacts/parsed
```

This command ignores private `.env` entirely and supplies inert local dependency URLs; it never
connects to PostgreSQL, Redis, Qdrant, R2 or an LLM. Parser/OCR settings can be supplied as process
environment variables. For example, PowerShell `$env:OCR_DPI='180'`. Set `TESSERACT_CMD` to a
host installation or use `--tesseract-cmd`. `--force-ocr` diagnoses pages even when native text
is present. Output must be outside the source directory. Partial or failed parsing, empty
folders, invalid input and exceeded limits return exit code 1; argument errors return 2.
Complete parsing returns 0. Every accepted source has a validated UUID-named JSON artifact;
`summary.json` reports parsed, partially parsed, failed and ignored files.

The generated sample's actual summary is:

```json
{"parsed": 2, "partially_parsed": 0, "failed": 0, "ignored": 0}
```

`examples/create_sample.py --include-scan --output artifacts/synthetic_ocr_bid` additionally
creates a scanned PDF. It needs real Tesseract for successful parsing. The Docker image already
contains Tesseract and English language data; parser unit tests mock recognition as appropriate.

PyMuPDF extracts native PDF blocks in layout reading order, preserving page boundaries,
one-based page numbers and bounding boxes in PDF points. pdfplumber extracts native table rows
independently. The first row is retained even when headers are ambiguous. Tables have stable IDs
and page/table locators; text is retained alongside tables rather than discarded as duplication.
PDF corruption, passwords, page failures and configured page limits produce explicit warnings.
Readable remainder pages produce partial success; a wholly unreadable document fails.

OCR runs when native alphanumeric characters are below `OCR_MIN_TEXT_CHARS` **or** native word
count is below `OCR_MIN_WORDS`. Defaults: 40 characters, 5 words, 200 DPI, English, 30 seconds per
OCR call, and at most 16 million rendered pixels. Rendering scales down when necessary.
Structured Tesseract words are grouped into ordered lines with positions and mean non-negative
word confidence on a 0–100 scale. That confidence is not a calibrated probability of fact
correctness. Native evidence survives force-OCR. Images are explicitly closed. OCR failures,
empty results and unresolved scanned-table structure remain visible in warnings. OCR text is
preserved without inventing table rows or values.

BeautifulSoup/lxml parses HTML titles, headings, paragraphs, list items, tables, links and IDs.
It preserves header/navigation text, and removes scripts, styles, templates and explicitly
hidden nodes. It never fetches referenced resources or executes document instructions. HTML
uses null page numbers and locators containing relative paths, headings, IDs, element indexes
and table indexes. Merged cells are retained with a warning rather than guessed expansion.

Normalization uses Unicode NFC and conservative whitespace cleanup. Identifiers, model/part
numbers, quantities and original line evidence are retained; there is no semantic deduplication
or chunking. Deterministic rules classify bid_page/rfp/addendum/specs/affidavit/other and extract
explicit addendum numbers. Publication dates require an explicit label plus ISO or spelled-out
month date; absent/ambiguous values are null. Modification times are never publication dates.

## Parsed JSON and provenance

`src/rfp/schemas/parsed.py` defines versioned Pydantic contracts. Documents include bid/document
UUIDs, original name, normalized relative path, original SHA-256, content type, classification,
nullable date/addendum number, parser/version/config fingerprint, status, physical PDF page
count, pages and warnings. Pages contain text, blocks, tables, OCR details and warnings. Blocks
and tables carry source locators; layout boxes use the original PDF coordinate system.
HTML has `page_count: 0` and one logical page with `page_number: null`.

Source identity is a UUID derived from bid ID + relative path + source hash. Bid identity uses
an explicit `--bid-id` string or normalized directory name, never document content. Use explicit
IDs to distinguish unrelated folders with the same name. Artifact keys additionally include a
parser configuration/version fingerprint. Reprocessing unchanged bytes/config produces stable
identities and sorted UTF-8 JSON. Empty results cannot be marked successful.

Parsing runs in a killable subprocess for both CLI and Celery. Defaults limit a document to
50 MiB, a folder to 500 MiB/500 supported files, 500 PDF pages and 300 seconds per document.
Linux children also have a 2 GiB address-space limit and a CPU limit; Windows uses timeout and
input/image/node limits without an OS address-space cap. Parser errors are safe warning codes.
These controls reduce risk but are not a general-purpose sandbox for hostile native documents;
production deployments should also apply container CPU/memory quotas and network isolation.

## Production R2/Celery ingestion

Run migrations (`0002` adds provenance, parsed-object references, job membership and partial
states), start the stack and execute the operator CLI on a folder visible to that process:

```powershell
docker compose up --build -d
# Host invocation requires localhost database and Redis URLs in its environment:
uv run --frozen python -m rfp.cli ingest-folder --path ./data/my_bid --bid-id my-procurement-2026
```

For the generated local sample inside Docker (PowerShell):

```powershell
docker compose cp artifacts/synthetic_bid api:/tmp/rfp-synthetic-bid
docker compose exec api python -m rfp.cli ingest-folder --path /tmp/rfp-synthetic-bid --bid-id synthetic-verification
```

The ingestion command requires complete R2 configuration and fails with `r2_not_configured`
when absent. It uploads sources using the existing storage interface, commits PostgreSQL rows,
then publishes `rfp.ingest` to the existing Celery app. Registration is deterministic and safely
repeatable; changed source bytes or parser options create a distinct job. No local-storage
fallback exists. Temporary local files are private parser scratch space, not persisted storage.

The worker downloads each source through R2, checks its SHA-256, parses it, uploads validated
JSON and commits document state independently. Failed parses also get warning-bearing JSON
artifacts when storage is available. Source keys are
`bids/{bid_uuid}/documents/{document_uuid}/source.pdf` (or `.html`/`.htm`); artifacts are
`bids/{bid_uuid}/documents/{document_uuid}/parsed/{parser_fingerprint}.json`.
Keys never include arbitrary user path segments, ACLs are never public, and signed URLs/content
are not logged. Provider/network failures have bounded retries/backoff; authorization/not-found
failures do not retry. PostgreSQL advisory locks serialize processing per bid. Duplicate delivery
of terminal jobs does nothing; completed matching documents are reused.

Jobs map persisted pending/processing/completed/partially_completed/failed to API
queued/running/succeeded/partially_succeeded/failed. Work checkpoints between documents after
roughly four minutes and queues continuation to stay within worker time limits. Per-document
commits survive worker restarts. R2 and database/broker commits are not distributed transactions:
failed registration can leave stable orphan source objects, and broker failure leaves a queued
job. Inspect it and redispatch with:

```powershell
docker compose exec api python -m rfp.cli dispatch-job --job-id JOB_UUID
curl.exe http://localhost:8000/api/v1/jobs/JOB_UUID
curl.exe http://localhost:8000/api/v1/documents/DOCUMENT_UUID
```

Job/document endpoints expose sanitized status, provenance and parsed-object keys for future
chunking. Document records point to their latest parsed configuration; historical object keys
are immutable by configuration. This phase provides no archive-upload endpoint or remote
filesystem-path API. Operators invoke ingestion on controlled local folders.

Known limitations: scanned tables remain ordered OCR text; native table headers can be
ambiguous; complex PDF reading order and malformed HTML layout may need manual review; dates
without explicit supported publication labels stay null. Trusted classification overrides can
be set using `METADATA_TYPE_PATTERNS` (a JSON object mapping document types to regex patterns).
Conflicting override matches yield `other`; pattern configuration is included in artifact identity.
Failed terminal jobs preserve their
failure and need operator intervention or changed source/configuration for a new run. Native
PDF parsing may consume significant resources until the subprocess limit is reached.

## Phase 3: local chunk preparation

Phase 3 consumes the existing validated `ParsedDocument` JSON contract, leaving parsed artifacts
immutable. No new dependency or tokenizer download is required. From a populated environment:

```powershell
uv run --frozen python -m rfp.cli chunk-folder --path artifacts/parsed --output artifacts/chunks
```

To reproduce the complete synthetic local example:

```powershell
uv run --frozen python examples/create_sample.py
uv run --frozen python -m rfp.cli parse-folder --path artifacts/synthetic_bid --output artifacts/parsed
uv run --frozen python -m rfp.cli chunk-folder --path artifacts/parsed --output artifacts/chunks
```

The tested native PDF + HTML sample produces 2 source documents, 6 chunk occurrences, 6 canonical
contents and no failures. Its parser `summary.json` is skipped. Re-running writes identical
per-document artifacts and reports 2 unchanged outputs. The CLI ignores private `.env`, needs
no credentials/infrastructure, and reads optional chunk configuration from process environment.
Malformed JSON is reported by relative input path and safe code while valid documents continue.
Output must be outside the input folder; symlink files/directories are excluded and output
symlinks are rejected. Files, source bytes, output text, chunks and execution time
are bounded. One file is written per generation UUID, plus a summary with source/chunk counts,
canonical reuse, exact-source duplicates, failures, partial documents and skipped inputs.
Exit codes: 0 for successful preparation (including explicitly configured empty skips), 1 for
failures/partial documents/no usable inputs, and 2 for invalid CLI arguments.

Normalization version `1.0` uses Unicode NFC, conservative whitespace cleanup, limited wrapped
line joining and removal of explicit discretionary soft-hyphen line wraps. ASCII hyphens remain
intact, including ambiguous word wrapping, to protect model numbers, SKUs and legal compounds.
Numbers, dates, times, money, units, percentages, legal wording, lists and repetitions are kept.
There is no stemming, paraphrasing, semantic deduplication or instruction execution. The
normalized evidence hash captures content and ordering before chunk overlap.

`ChunkConfig` lives in `src/rfp/schemas/chunks.py` and is part of shared Settings. Use nested
environment settings, for example in PowerShell:

```powershell
$env:CHUNKING__TARGET_TOKENS='600'
$env:CHUNKING__MAX_TOKENS='800'
$env:CHUNKING__OVERLAP_TOKENS='100'
$env:CHUNKING__TABLE_ROW_LIMIT='20'
```

Validation requires overlap < target <= maximum. The default `utf8_bytes_v1` counter estimates
tokens as `ceil(UTF-8 byte length / 4)`, with identifier `utf8-bytes-div4-v1`. These are **estimates**,
not an embedding-model tokenizer's exact counts. The `TokenCounter` protocol is replaceable;
Phase 4 must select/validate the actual embedding tokenizer and revise the effective configuration
before relying on hard model context limits. Supported normalization/chunking versions are
explicit: an unknown version or counter is rejected rather than pretending to implement it.

Prose chunks preserve explicit sections and numeric heading paths, prefer paragraph/sentence/
word boundaries, and carry approximately 100 estimated tokens of overlap only within the same
continuous section and chunk type. Unrelated sections, lists, headings and OCR evidence remain
separate. Unknown HTML heading levels are not invented; numbered headings support hierarchy,
and unstructured residual page text is preserved separately with its available locator.
Oversized unbreakable identifiers are preserved with a budget warning rather than split.

Tables use `table_rows` occurrences, not flattened prose. Each row group repeats identifiable
headers, retains typed cell arrays and original row order, and records table ID, one-based row
range, source page/HTML locator, extraction method and warnings. Original repeated header rows
remain source rows. Groups obey row and token budgets, except an indivisible oversized row is
preserved and flagged. Missing headers stay null. Scanned tables remain honest OCR/text evidence.
An exact whole-table copy is removed from ordinary body blocks only when the structured rows
match one unambiguous group of complete adjacent blocks. Uncertain copies are preserved with
`table_prose_overlap_uncertain`. No repeated legal statement is removed as semantic duplication.

Every occurrence records bid/document/source revision, type/addendum metadata, normalized text,
section path, sequence, versions/configuration hash, estimated count/counter ID, parsing warnings
and all source segments. PDF ranges cover every contributing one-based page; HTML ranges are
null and retain element IDs/headings/indexes. Character spans refer to normalized block text,
not byte offsets in the original PDF/HTML. Layout boxes and OCR diagnostics remain available.

## Canonical content, generations and incremental processing

Three distinct reuse levels remain visible:

1. Original SHA-256 identifies byte-identical source files; each file/path retains its own
   document ID and source references.
2. Canonical `ChunkContent` stores exact normalized chunk text once by SHA-256, even across
   bids. Its content UUID derives from that hash. This deduplicates computation and storage.
3. Each `DocumentChunk` occurrence keeps an independent citation, authority, filters, section,
   order and source segments. Repeated text remains evidence; occurrences are never collapsed
   merely because their content ID matches another document or addendum.

Canonical text deliberately excludes a synthetic section-heading prefix; section context stays
on occurrences. Phase 4 must preserve occurrence-specific authority and metadata filters when
reusing an embedding, and must include any added heading context in its embedding-cache key.

Migration `0003` adds `chunk_contents`, `chunk_generations`, `document_chunks` and
`job_chunk_generations`, plus parsed-artifact SHA-256 and a source-hash index on Document. New
parses store their JSON hash directly; legacy Phase 2 records are backfilled once by bounded
R2 download. Adding a document does not reparse or rechunk existing completed documents.

A generation UUID binds document revision + exact parsed-artifact SHA-256 + effective config
hash. The config includes versions, counter, budgets and limits. Canonical and occurrence IDs
are stable. A partial unique DB index permits only one active generation per bid/source path;
separate uniqueness constraints protect inputs, content hashes and occurrence order. A source
revision gets a new Document ID; the latest registered revision is selected by creation time
with UUID tie-breaking. Snapshot checks reject stale parsed keys/hashes before activation.

New artifacts are uploaded privately to
`bids/{bid}/documents/{revision}/chunks/{parsed_sha256}/{config_hash}.json`. Artifact key/hash,
schema version, normalized hash, config, status and counts are persisted. Old generations and
their occurrences remain historical. R2 upload and DB activation use separate systems, so an
orphan chunk object may remain if the DB transaction fails. Activation itself is atomic:
canonical insertion, occurrence insertion and previous-generation deactivation commit together.
Failure rolls everything back and preserves the last successful active generation. A partial
generation does not replace an existing complete generation; if no active generation exists,
its readable evidence can become active with partial status.

## Production chunk worker

After starting/migrating the shared backend:

```powershell
docker compose up --build -d
docker compose exec api python -m rfp.cli chunk-bid --bid-id BID_UUID
curl.exe http://localhost:8000/api/v1/jobs/JOB_UUID
curl.exe http://localhost:8000/api/v1/chunk-generations/GENERATION_UUID
```

`chunk-bid` uses PostgreSQL/R2 and requires full R2 configuration. It registers deterministic
jobs and queues `rfp.chunk` on the existing Celery app. `dispatch-job --job-id JOB_UUID` chooses
the correct ingestion/chunk task for recovery. Completed repeated deliveries do nothing.
An explicit `chunk-bid` rerun can retry failed generations or reactivate a successful historical
configuration without duplicating occurrences. Existing successful documents are reused, while
new or changed inputs/configurations receive their applicable generation. Preparation is
explicitly invoked after ingestion; it is not silently triggered by parsing.

The shared per-bid PostgreSQL advisory lock serializes ingestion and chunk preparation. Canonical
inserts use conflict-safe database inserts, including when different bids prepare identical text
concurrently. Retries/backoff are bounded for transient storage failures. Documents checkpoint
independently; permanent failures stay visible while the remainder continues. Legacy snapshot
read failures are recorded and yield partial/failed jobs rather than silently claiming success.
Batch continuations keep work within configured worker time limits. Parsed integrity and
provenance are validated before use; no local storage fallback exists.

The Phase 3 limits default to 64 MiB per input JSON, 5 million represented input characters,
20 million output text characters, 10,000 chunks, 120 seconds per document and 240 seconds per
batch, plus existing folder limits. Output expansion (for example, repeated large table headers)
is bounded. Full configuration examples are in `.env.example`. A resource-limited generation
fails atomically instead of replacing active evidence with truncated chunks.

Phase 4 handoff: read active `ChunkGeneration` records, join `DocumentChunk` to `ChunkContent`,
or load the matching validated `ChunkingResult` R2 artifact. Cache embedding computation by
canonical hash **and** actual model/version/input transformation; retain per-occurrence bid,
document type, addendum, pages and locators for filters and citations. Review partial states and
warnings, choose the real tokenizer, and preserve revision/activation rules. No embeddings,
Qdrant writes, keyword search, retrieval, reranking, extraction or reconciliation exist here.

## Phase 4: hybrid evidence search

Search returns cited evidence, never answers. Design details are in `docs/architecture.md` and
`src/rfp/search/README.md`.

| Component | Default (all in `SEARCH__*` settings) |
| --- | --- |
| Dense | FastEmbed `BAAI/bge-small-en-v1.5`, 384-dim, cosine, BGE query instruction |
| Sparse | FastEmbed `Qdrant/bm25` with Qdrant IDF modifier |
| Identifiers | Exact payload match branch for codes such as `PX-420`, `RFP-2026-014` |
| Fusion | Application weighted RRF, k=60, candidate depth 40 per branch |
| Reranker | FastEmbed `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder, depth 20, batch size 1 |
| Output | top_k 5 (max 50), duplicates collapsed with all citations attached |

Modes: `dense`, `sparse`, `hybrid` (dense + sparse + identifier RRF) and `hybrid_rerank`
(default). Filters: bid, document, doc type, addendum number, chunk type; active generations and
the current embedding profile are always enforced.

### Local (no PostgreSQL/R2/.env)

```powershell
docker compose up -d qdrant
.\.venv\Scripts\python -m rfp.cli init-models            # one-time download into .cache/fastembed
.\.venv\Scripts\python -m rfp.cli index-folder --path artifacts/chunks
.\.venv\Scripts\python -m rfp.cli search --local --query "PX-420"
.\.venv\Scripts\python -m rfp.cli search --local --query "installation requirements" --mode hybrid --top-k 3 --json
.\.venv\Scripts\python examples\search_smoke.py          # repeatable end-to-end check
```

`index-folder` reads Phase 3 `ChunkingResult` JSON (one generation per bid/path; the current
chunk configuration wins over stale artifacts), caches embeddings in `.cache/embeddings` and is
idempotent: a rerun reports every generation `unchanged` without recomputation. Changing a model
or BM25 parameter creates a new profile-specific collection and cache namespace. Reports give
`superseded_points_deactivated` and `superseded_points_deleted` (recounted in Qdrant); a delete
that leaves stale points fails the generation with `superseded_delete_unverified`, and a rerun
retries it.

### Retrieval evaluation and reranker benchmarks (local only)

These scripts refuse any non-loopback Qdrant URL or configured cloud credentials and never read
`.env`. They need the assessment bids indexed in local Qdrant.

```powershell
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify      # labels grounded in artifacts
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --out artifacts\eval\report.json
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --modes hybrid_rerank --rerank-depth 10
.\.venv\Scripts\python scripts\rerank_parity.py parity           # batched vs one pair per call
.\.venv\Scripts\python scripts\rerank_parity.py bench --stride 4 # p50/p95 per depth and batch
.\.venv\Scripts\python scripts\rerank_parity.py memory --stride 4  # fresh process per config
```

`evaluation/assessment_retrieval.json` holds 55 cases (50 answerable, 3 unknown identifiers,
2 unanswerable) with document, one-based page or HTML element ID and required text per judgment.
Labels are only applied to search output; they never influence retrieval. Items still needing
human review are listed in its `needs_human_labeling` field.

### Production

```powershell
docker compose up --build -d
docker compose exec api python -m rfp.cli init-models    # fills the shared model_cache volume
docker compose exec api python -m rfp.cli index-bid --bid-id BID_UUID
curl.exe -X POST http://localhost:8000/api/v1/index -H "Content-Type: application/json" -d '{\"bid_id\": \"BID_UUID\"}'
curl.exe http://localhost:8000/api/v1/index/jobs/JOB_UUID
curl.exe "http://localhost:8000/api/v1/search?q=PX-420&bid_id=BID_UUID&top_k=5"
curl.exe -X POST http://localhost:8000/api/v1/search -H "Content-Type: application/json" -d '{\"query\": \"submission deadline\", \"filters\": {\"bid_ids\": [\"BID_UUID\"], \"doc_types\": [\"addendum\"]}}'
curl.exe http://localhost:8000/api/v1/index/status
```

Production indexing reads active chunk generations from PostgreSQL, stores embedding batch
artifacts in R2 (`embeddings/{profile}/{sha256}.bin`) with `embedding_cache_entries` pointer rows,
and runs on the existing Celery app as `rfp.index`. `index-bid --inline` processes in the CLI
process. Job state is per generation in `chunk_index_states`. R2 is mandatory for production
indexing; there is no local fallback. Qdrant Cloud is used through `QDRANT_URL`/`QDRANT_API_KEY`.

Qdrant versions: `qdrant-client` 1.19.1 (no 1.19.2 client exists) is pinned to `<1.20`; the
compose server is `qdrant/qdrant:v1.19.2`, matching the configured cloud cluster. The client's
compatibility check is enabled, and `CollectionManager.check_server` enforces the same rule
(equal major, minor difference ≤ 1, server ≥ 1.10) before any search or write, failing with
`vector_store_version_incompatible`. `GET /api/v1/index/status` reports both versions. The local
1.19.2 server uses the fresh `qdrant_data_v1_19` volume; the old 1.13.4 `qdrant_data` volume is
left untouched (Qdrant does not migrate storage across non-consecutive minor versions).

### Assessment inputs (local only)

Real bid documents go in the git-ignored `data/assessment/Bid1` and `data/assessment/Bid2`
(mounted read-only at `/data/assessment` in the API container). Extraction, offline
parse/chunk commands (`docker run --network none ...`) and optional local indexing are in
`data/assessment/README.md`. Do not substitute synthetic documents.

Errors use the standard envelope with stable codes, for example `index_not_found`,
`collection_incompatible`, `embedding_model_unavailable` (run `init-models`),
`vector_store_unavailable`, `embedding_cache_unavailable`, `query_too_long`, `top_k_too_large`,
`no_active_chunk_generations` and `search_timeout`. Retryable errors carry `Retry-After`.
CLI exit codes: 0 success, 1 operational failure, 2 invalid request.

Each result contains text, chunk type, citation (file name, relative path, one-based PDF
`page_start`/`page_end` or `null` pages plus HTML locators, section path, table rows, doc type,
addendum, generation/occurrence IDs), additional citations for identical text elsewhere, score
components (dense, sparse, identifier rank, RRF, reranker, identifier bonus, final) and identifier
matches. Responses include normalized/expanded queries, candidate counts, timings, profile and
warnings such as `identifier_evidence_not_found` or `degraded`.

## Phase 5: multi-agent extraction

Extraction calls the Phase 4 search service. It does not ingest or index. The orchestrator is
in-process Python (`src/rfp/agents/`), not a separate agent framework. See
`src/rfp/agents/README.md` and `docs/architecture.md`.

```powershell
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts\extraction
```

The command uses loopback Qdrant only (`--qdrant-url`, default `http://127.0.0.1:6333`) and
refuses a remote host. Live mode reads `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` from the
environment or private `.env` and exits if they are missing. It writes
`artifacts/extraction/Bid1.json` and `artifacts/traces/extraction_Bid1.json`. A field that fails
validation is retried at most twice. Other fields are kept. Exit 0 means the JSON is valid,
including fields left unresolved. Exit 1 means the search target is unsafe, the model is not
configured, or retrieval never succeeded. Exit 2 means the arguments are invalid.

`--llm-mode test` does not call a provider and does not invent values. Use it only in tests.
