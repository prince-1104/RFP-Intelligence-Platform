# RFP Intelligence Platform

Phase 2 adds document ingestion, PDF/HTML parsing, native tables and Tesseract OCR to the
Phase 1 infrastructure. Phase 3 adds normalization, structure-aware chunks, canonical content,
source occurrences and incremental generations. Phase 4 adds a hybrid RAG evidence search engine
(dense + BM25 + exact identifiers, RRF, cross-encoder reranking, citations) with incremental
Qdrant indexing, a REST API and CLI. Phase 5 adds a typed multi-agent extraction workflow
on that search service. Phase 6 adds citation-grounded Q&A over one bid. Phase 7 validates
extraction, Q&A and retrieval live on both assessment bids (see
[Live results and demo](#phase-7-live-results-and-demo)). Phase 10 adds `process-bid`, one command
that takes a local bid folder through every stage (see
[End-to-end command](#phase-10-end-to-end-command-process-bid)). The requirement-by-requirement
status, known gaps and the owner review checklist are in `docs/final_submission_audit.md` (see
[Final submission](#final-submission-status-setup-notes-and-demo)). A web UI remains optional and is
not implemented.
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
Direct SQL writers must update that field themselves. The 20 extracted fields are defined in
`src/rfp/agents/schemas.py` (`REQUIRED_FIELDS`, `BidExtraction`).

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
The adapter rejects incomplete configuration when instantiated. `extract-bid`, `ask` and
`ask-samples` call an LLM using `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` (optional
`LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS`). Put them only in the private
`.env`. Missing values stop those commands with `llm_not_configured`, and no value is ever
printed or written to outputs or traces. `TESSERACT_CMD` identifies the executable for OCR;
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

Results from
`evaluate_retrieval.py run --modes dense,sparse,hybrid,hybrid_rerank --out artifacts\eval\retrieval_phase7.json`
(2026-10-24, 55 cases of which 50 are scored, default rerank depth 20). They match the earlier
run in `VERIFICATION.md`:

| Configuration | Recall@5 | MRR@10 | nDCG@10 | hit@5 | p50 latency (ms) |
|---|---|---|---|---|---|
| dense (BGE-small) | 0.703 | 0.688 | 0.659 | 0.88 | 58 |
| sparse (BM25) | 0.786 | 0.781 | 0.749 | 0.88 | 27 |
| hybrid (RRF) | 0.736 | 0.795 | 0.741 | 0.88 | 44 |
| hybrid + rerank | 0.772 | 0.769 | 0.765 | 0.88 | 1,675 |

All modes had 0 errors, 0 cross-bid leaks and 0 citation errors, and all 3 unknown identifiers
returned no evidence.

- Sparse BM25 has the best Recall@5 on this small, identifier-heavy set.
- Hybrid RRF has the best MRR@10.
- Reranking gives the best nDCG@10, at about 1.7 s per query on CPU.

`verify` is the automated label check: the required text must be present at each judged page or
element. Human approval of the labels is still pending.

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

## Phase 6: citation-grounded Q&A

```powershell
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid1 --question "What is the final proposal deadline after all addenda?"
```

`ask` searches loopback Qdrant only, always filtered to the one bid, and sends the retrieved
passages to the configured model (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`). It prints one JSON
object: `bid_id`, `question`, `answer`, `status` (`answered`, `not_found` or `conflict`),
`citations` (document, PDF page or HTML element id, document type, addendum number and the
supporting excerpt), `validation`, `trace_id`, `model`, `evidence_count` and `trace_path`.
`confidence` is always `null`: there is no calibrated method for answer confidence, so no score
is reported.

The model returns claims, each linked to quotes. A reply is accepted only if:

- every quote is a contiguous span of a retrieved passage of this bid (`agents/quotes.py`; case,
  whitespace, line breaks, typographic quotes and punctuation between words are tolerated, while
  paraphrases and quotes stitched across separate text are rejected). The displayed `excerpt` is
  the source text, not the model's wording;
- every claim is lexically supported by its own quotes (`agents/grounding.py`): numbers, dates,
  times, time zones, platforms, names, negations, scope words (only, unchanged) and requirement
  words (must, shall, required) must appear in the linked quotes, not just elsewhere in the
  passage. Only leading zeros are tolerated as a formatting difference;
- deadline answers respect explicit addendum revisions;
- for list questions (which affidavits, forms, ...), every item named in the retrieved evidence is
  answered or listed under `completeness.unresolved` (`agents/checklist.py`). Lists are never
  marked exhaustive.

A rejected reply gets one correction request (`max_corrections=1`) that lists the reasons. On the
final attempt, unsupported claims are dropped and reported in `validation.reasons` and
`limitations` if at least one claim survives; otherwise the answer is `Not found in documents`.
A non-verbatim quote is discarded rather than sinking its claim, but only when another quote in
that claim is valid and supports it. Claims that passed every check on an earlier attempt are kept
when the correction attempt abstains or fails; `validation.reasons` then starts with "Kept claims
validated on attempt N" and the dropped claims and unaddressed items stay listed.
Malformed model output is handled the same way. The grounding check is lexical, not semantic
entailment: a claim made of supported words can still misread its quote. Provider errors (`llm_timeout`, `llm_provider_error`) and
missing configuration (`llm_not_configured`) exit 1 without an answer.

The trace (`artifacts/traces/qa_<trace_id>.json`, override with `--trace-dir`) records search
modes, evidence locators, model token counts and validation decisions. It stores a hash and the
length of the question, not the question or passage text. `--output FILE` also writes the
response. Writing under `evaluation/` or `data/assessment/` is refused. Exit codes: 0 response
returned (including `not_found`), 1 unsafe target, configuration or provider failure, 2 invalid
arguments. `--llm-mode test` with `--fixture` replays scripted replies for tests. It is labelled
`provider: fake` and is not a real answer.

Cited examples are generated only with a live model:

```powershell
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_questions.json --output artifacts\qa
```

This writes `artifacts/qa/qa_samples.json` (a list of Q&A responses), `qa_samples.md` and one trace
per question under `artifacts/qa/traces/`. It has no test mode.

Offline Q&A evaluation (no model or Qdrant calls) uses the reviewed set
`evaluation/qa_reviewed.json`:

```powershell
.\.venv\Scripts\python scripts\evaluate_qa.py verify
.\.venv\Scripts\python scripts\evaluate_qa.py score --samples artifacts\qa\qa_samples.json --out artifacts\eval\qa.json
```

See `docs/qa_examples.md` for the format, review status and latest results.

## Phase 7: live results and demo

All numbers below come from live `gpt-4o-mini` calls against local Qdrant on 2026-10-24.
`docs/live_validation.md` has the per-field and per-question review, the defects found and fixed,
and the commands. Verdicts are an assistant review against the parsed source text and are pending
human confirmation. `ASSIGNMENT_CHECKLIST.md` maps each assignment requirement to its status.

| Bid | Fields accepted / not found / unresolved | Reviewed correct / partial / wrong / missed / null ok |
|---|---|---|
| Bid1 | 12 / 8 / 0 | 6 / 6 / 0 / 5 / 3 |
| Bid2 | 12 / 7 / 1 | 5 / 5 / 2 / 4 / 4 |

Extraction JSON is in `artifacts/extraction_v2/Bid1.json` and `Bid2.json`. Traces are in
`artifacts/extraction_v2/traces/extraction_<Bid>.json`. The automated check is
`scripts/verify_extraction.py`. Its report, `artifacts/eval/extraction_v2_check.json`, shows:

- schema valid;
- 20 fields in each file;
- no cross-bid sources;
- retries at most 2;
- no secrets in outputs or traces.

Known extraction errors in Phase 7 (addressed in Phase 8, below):

- Bid2 Delivery Date repeats the due date.
- Bid2 MFG for Registration contains form checkbox text.
- Several product and contact fields are null although the source states them.

Q&A: 18 live questions across both bids (`artifacts/qa_examples.json`, raw run in
`artifacts/qa_phase7_final/`) produced:

- 11 correct cited answers;
- 2 partially correct answers;
- 5 correct abstentions, including both cross-bid probes;
- 0 wrong answers.

Every citation names the document plus a PDF page or an HTML element id, with the verbatim
excerpt. On the 14-case gold set, answer correctness is 9/14 with 3/3 correct abstentions
(`artifacts/eval/qa_v6.json`).

Offline checks on the final code:

- `pytest -m "not integration"`: 341 passed, 6 integration tests deselected;
- `ruff check` and `ruff format --check`: clean;
- `mypy src/rfp`: no issues.

These tests use fake models and do not measure live accuracy.

### Reproducible demo sequence

No demo video has been recorded. With local Qdrant running (`docker compose up -d qdrant`),
the assessment bids already indexed, and the LLM keys in `.env`:

```powershell
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify
.\.venv\Scripts\python -m rfp.cli search --local --bid-id 5809c088-8514-58c3-a0e5-4d9e72adab0b --query "new due date" --top-k 3
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts\demo
.\.venv\Scripts\python scripts\verify_extraction.py --outputs artifacts\demo
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --question "How soon after award must the laptops be delivered?"
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --question "What is Jasmine Alzate's email address?"
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --modes hybrid,hybrid_rerank
```

`search` takes the Bid1 UUID. `extract-bid` and `ask` take the folder labels `Bid1` and `Bid2`
and derive the same UUIDs. The last `ask` shows bid isolation: Jasmine Alzate is the Bid1 buyer,
so Bid2 answers `Not found in documents`.

### Limitations

- Model output varies between runs.
- Grounding is lexical, not entailment.
- Field-meaning rules (Phase 8) are keyword cues. They reject values of the wrong kind, but they
  cannot judge completeness.
- Extraction `confidence` is a fixed value per status (0.86 / 0.74 / 0.35), not a calibrated
  probability.
- Scanned pages are OCR text without tables.
- Retrieval labels and all accuracy verdicts are not yet human-approved.

## Phase 8: field-meaning validation

`src/rfp/agents/semantics.py` adds deterministic rules that run inside the existing validator. A
value must be supported by its cited passage and must also be the kind of value the field asks
for. For example, a delivery date needs a delivery cue near it, MFG may not be checkbox text, a
submission type must name a channel, and contact info needs an email or phone. A failed rule
uses the existing bounded retry (at most 2). If the retries fail too, the field is left
unresolved with no value. No per-field model call was added.

Other fixes, each with a regression test in `tests/test_semantics.py`:

- Catalog queries no longer contain identifiers such as "W-9". The search engine returns nothing
  for an identifier absent from the bid.
- A field may cite any passage shown in the same prompt.
- Integer or string id lists and object values in model replies are read, not dropped.
- Long passages are shown to the model and the critic as the window that best matches the field,
  instead of the first 700 characters.
- Each field has a one-line definition in the prompt. A product identifier that repeats the
  Bid Number is rejected.
- Notes on accepted values drop unsupported claims (for example an invented original due date).
- Sources for chunks that cross pages name the page that holds the value. The page map is built
  from the existing segment offsets.

| Bid | Accepted / not found / unresolved | Reviewed correct / partial / wrong / missed / null ok |
|---|---|---|
| Bid1 Phase 7 → Phase 8 | 12/8/0 → 16/4/0 | 6/6/0/5/3 → 8/8/0/1/3 |
| Bid2 Phase 7 → Phase 8 | 12/7/1 → 15/5/0 | 5/5/2/4/4 → 11/4/0/1/4 |

Output is in `artifacts/extraction_v3/`, and the automated check is
`artifacts/eval/extraction_v3_check.json`. Q&A and retrieval results are unchanged from Phase 7:

- 18-question set (`artifacts/qa_phase8`): 11 correct, 2 partial, 5 correct abstentions;
- gold set (`artifacts/eval/qa_v7.json`): 9/14;
- retrieval (`artifacts/eval/retrieval_phase8.json`): identical metrics in all four modes.

Offline: 365 passed with `pytest -m "not integration"`; ruff and mypy are clean. The review is an
assistant review and needs human confirmation. Two Bid1 fields are worse than in Phase 7 (Term
of Bid, MFG for Registration). Bid2's affidavits are still missed in the final run. Details are
in `docs/live_validation.md`.

## Phase 9: evidence-based review and final corrections

`docs/final_accuracy_review.md` gives, for every field of both bids:
- the extracted and expected values;
- the source page and passage;
- whether the citation is valid;
- a verdict and the reason.

Verdicts were re-checked against the parsed source text by the assistant. They are labelled as
model-assisted or automated, and no human review has taken place.

General fixes, with regression tests in `tests/test_final_accuracy.py`:

- **Bid Number rule.** A listing "Reference Number" is never the Bid Number. If the evidence
  labels a solicitation, bid, RFP, RFQ, IFB or ITB number, the value must be one of those.
  Otherwise it must be an identifier that appears in both the bid listing page and the documents.
  Other labelled identifiers are kept as a cited alias note. For Bid2, BPM044557 is the Bid Number
  and the documents' "Fixed Price #E20P4600040" is recorded as an alias.
- **Restatements.** Term, Delivery, Installation, Product, Product Specification and Summary may
  restate the source. Every number must appear in the cited text, and at least 80% of the words
  must too. A summary that copies 25 or more words from one passage is rejected.
- **Omissions.** Term must keep the renewal options that the source mentions next to it. Product
  must keep a quantity when the cited table has a Qty column. A value rejected only for an
  omission is kept with a "Possibly incomplete" note if the retries do not improve it.
- **Wrong-kind values.** The following are rejected:
  - document-type headings as Title;
  - checkbox instructions as required documents;
  - subcontractor payment clauses as Payment Terms.
  A reseller qualification is accepted as MFG when no manufacturer is named.
- **Grounding.**
  - Email addresses split by PDF line breaks are joined for matching. The source text and its
    locator are unchanged.
  - Verbatim citations to a passage in the same prompt are repaired.
  - Placeholder values ("N/A", "not specified") are dropped.
- **Stability.**
  - Retry prompts and traces show the rejected value.
  - OpenAI extraction and critic calls use JSON mode and a fixed seed.
  - Retries stay bounded at 2.

| Bid | Accepted / not found / unresolved | Model-assisted correct / partial / wrong / missed / null ok |
|---|---|---|
| Bid1 Phase 8 → Phase 9 | 16/4/0 → 16/3/1 | 7/8/1/1/3 → 9/7/0/0/4 |
| Bid2 Phase 8 → Phase 9 | 15/5/0 → 16/4/0 | 10/5/0/1/4 → 11/5/0/0/4 |

The Phase 8 counts are re-scored with the Phase 9 criteria, so they differ slightly from the
table above. Bid1's unresolved field is Payment Terms: the documents state no buyer payment
terms, and the only candidate (a subcontractor clause) is rejected.

Output is in `artifacts/extraction_v4/` and a same-code repeat is in
`artifacts/extraction_v4_repeat/`. The automated check is `artifacts/eval/extraction_v4_check.json`.

Variation: the first-round searches were identical in both runs (31 of 31 per bid), so the
differences come from model generation. The final run and its repeat gave identical values for
15 of 20 Bid1 fields and 19 of 20 Bid2 fields. The two earlier Phase 9 runs agreed on 16 of 20
fields for each bid. Across the five mixed-code Phase 8 runs, only 7 fields matched.

Q&A and retrieval were spot-checked, and no label errors were found. They were not rerun.

Offline checks: 388 passed (6 integration tests skipped). `ruff check`, `ruff format --check`
and `mypy src/rfp` are clean, and retrieval `verify` is OK. A secret scan of all 331 artifact and
doc files found no leaks.

Remaining partial fields include:
- Bid1 Documents, MFG and Product Specification;
- Bid2 Product (flagged incomplete) and Product Specification;
- both summaries.

The full list and root causes are in the review. These fields, every verdict and the retrieval
labels still need human review.

## Phase 10: end-to-end command (`process-bid`)

```powershell
.\.venv\Scripts\python -m rfp.cli process-bid --input <bid-folder> --output <new-output-folder> [--bid-id <label>]
```

`process-bid` runs the existing services in order and stops at the first stage that fails:

| Stage | Existing code it calls | What it checks |
|---|---|---|
| validate | `ingestion.discovery.discover` | The folder exists and holds PDF or HTML files. Links, oversized files and other types are refused and listed |
| parse | `cli.parse_folder` (PyMuPDF, pdfplumber, BeautifulSoup, Tesseract) | Every artifact carries the requested bid UUID. Failed or partial documents are listed with their page warnings |
| chunk | `chunking.local.chunk_folder` | At least one chunk was produced |
| index | `search.indexing.load_local_artifacts` and `IndexingService.index` | Only this bid's generations go to loopback Qdrant. The run fails if the collection already holds active evidence for the same bid id from documents that are not in the folder |
| extract | `agents.runner.execute` (the Phase 5–9 orchestrator) | The workflow completed and at least one field passed validation |
| verify | `evaluation.extraction_check.check_bid` (shared with `scripts/verify_extraction.py`) | The output has the 20 fields in order; no citation names a file outside the bid; every accepted value is on its cited page; retries are bounded; no secret appears in the outputs |

**Input.** One folder per bid, with subfolders allowed. Supported types are `.pdf`, `.html` and
`.htm`. Hidden, temporary and linked files are skipped. Unsupported files are listed in the
summary and make the run `partial`. The bid label defaults to the folder name and must be a short
label (letters, digits, `.`, `_`, `-`). The bid UUID is derived from the label, exactly as
`parse-folder` and `extract-bid` derive it.

**Configuration.**
- Local Qdrant must be running (`docker compose up -d qdrant`).
- The embedding models must be cached (`python -m rfp.cli init-models`).
- Live extraction needs `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` in `.env`.
- `--llm-mode test` runs offline. It never invents a value: every field is "Not found", or it
  replays `--fixture` responses.
- `--collection NAME` writes to `NAME__<profile>` instead of `bid_documents__<profile>`. Use it
  to keep a trial run out of the main index.
- `--tesseract-cmd` points to Tesseract for scanned pages.
- Settings come from the command line, not `.env`, for the same reason as `parse-folder`.

**Outputs** (all inside `--output`, which must be new or empty):

```
<output>/
  run_summary.json            status, exit code, per-stage counts and errors, relative output paths
  <label>.json                the 20-field extraction (BidExtraction schema)
  traces/extraction_<label>.json
  verification.json           per-field citation support, problems and notes
  index.json                  indexing report (collection, generations, points)
  parsed/  chunks/            Phase 2 and Phase 3 artifacts for this run
```

**Exit codes:**
- 0: every stage succeeded.
- 3: completed, but partial. A document failed or parsed partially, a file was refused, or a
  field was left unresolved.
- 1: a stage failed. Later stages are skipped and `run_summary.json` names the stage and the error
  code.
- 2: the request was refused before anything was written. This covers a missing folder, no
  PDF/HTML, a non-empty output folder, output inside the input or `evaluation/`, a remote Qdrant
  URL, or a UUID given as the label.

**Safety.**
- Only a loopback Qdrant URL is accepted.
- Nothing is written to R2, PostgreSQL or Qdrant Cloud, and nothing is queued.
- Existing output folders are never overwritten.
- Error messages are codes or fixed text, never document content.
- `.env` secret values are checked against the outputs.
- No bid identifiers, file names or answers appear in the command's code.

**Unseen-bid tests** (synthetic, offline):

```powershell
.\.venv\Scripts\python -m pytest tests\test_pipeline.py -q
```

`tests/unseen_bid.py` builds a fictional bid: Harborview County Water Authority, RFP
HC-2026-118. It has two PDFs (an RFP and Addendum No. 1, which moves the due date) and one HTML
listing page. The tests run the real parsers, chunker and indexer against an in-memory Qdrant with
fake embeddings. The model is a deterministic fake that answers only from the passages it is
shown.

They cover:
- schema validity, values, citations and pages;
- the addendum's due-date change;
- an invented value staying unknown;
- isolation from another bid in the same collection;
- stale documents after a file is removed;
- no overwrite;
- unsupported, malformed and missing inputs;
- malformed model output, an index failure and remote Qdrant.

These tests show the pipeline works on a bid it was not built for. They are not evidence of
live-model accuracy on a real unseen bid.

**Live regression on the assessment bids** (2026-10-24, `gpt-4o-mini`, separate local collection
`pipeline_check`). The main `bid_documents` collection was not touched. Output is in
`artifacts/pipeline_v1/` and, for the Bid1 rerun after the Model_no fix, in
`artifacts/pipeline_v2/`.

| Run | Exit | Accepted / not found / unresolved | Model-assisted correct / partial / wrong / missed / null ok |
|---|---|---|---|
| Bid1 `extraction_v4` (Phase 9) | — | 16 / 3 / 1 | 9 / 7 / 0 / 0 / 4 |
| Bid1 `pipeline_v1` | 3 (partial parse, no host OCR) | 17 / 3 / 0 | 9 / 7 / 1 / 0 / 3 |
| Bid1 `pipeline_v2` (after the Model_no fix) | 3 (partial parse, no host OCR) | 16 / 4 / 0 | 8 / 8 / 0 / 0 / 4 |
| Bid2 `extraction_v4` (Phase 9) | — | 16 / 4 / 0 | 11 / 5 / 0 / 0 / 4 |
| Bid2 `pipeline_v1` | 0 | 16 / 4 / 0 | 10 / 6 / 0 / 0 / 4 |

Both bids pass verification: schema valid, no foreign citations, every accepted value on its
cited page. The Bid1 addendum change (27-JUN-2024 14:00:00 → July 9, 2024 at 2:00 PM CST) is
reconciled as in v4. Accuracy did not improve over Phase 9.

The run-to-run changes are model variation:
- Bid1 Installation was cut before "deployment services".
- Bid1 MFG gave the requirement sentence in `pipeline_v1` and the heading in `pipeline_v2`.
- Bid2 Documents listed one of the two affidavits.

`pipeline_v1` also exposed a general defect: Bid1 Model_no was "Tier 1 Small Student
Chromebook Laptop (Touch)", because the rule only required a digit. Model_no now needs a code,
such as a run of two or more digits or a token that mixes letters and digits. `pipeline_v2`
returns null for it.

The synthetic fixture exposed another: an optional time zone in `DATE_RE` matched lowercase
words, so "3:00 PM Proposals" became "3:00 PM Propo". The time zone must now be an uppercase word.

The host has no Tesseract, so Bid1 FINAL pages 54–59 failed OCR and the run is reported as
partial. No extracted field cites those pages.

Q&A and retrieval: the code paths are unchanged. Offline checks show no regression:
- `evaluate_qa.py score` reproduces `artifacts/eval/qa_v7.json` exactly;
- gold `verify` and retrieval `verify` pass;
- `verify_extraction.py` reproduces `extraction_v4_check.json` exactly after the helpers moved
  into `rfp.evaluation.extraction_check`.

**Demo (Windows PowerShell, from the repository root):**

```powershell
docker compose up -d qdrant
.\.venv\Scripts\python -m rfp.cli init-models
.\.venv\Scripts\python -m pytest tests\test_pipeline.py -q
.\.venv\Scripts\python -m rfp.cli process-bid --input data\assessment\Bid2 --output artifacts\demo_process\Bid2 --collection pipeline_check
Get-Content artifacts\demo_process\Bid2\run_summary.json
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --question "How soon after award must the laptops be delivered?"
```

`ask` searches the main `bid_documents` collection by default, which already holds Bid2. To ask
about a bid that `process-bid` indexed into a separate collection, pass the same name:
`ask --bid-id <label> --collection <name> ...` (added in the final submission phase, below). Each
`process-bid` run needs a new `--output` folder.

**Limitations:**
- Live values vary between runs, so partial fields remain.
- A single synthetic fixture does not prove generalization.
- No real unseen bid was available to test.
- Only the due date is reconciled from addenda.
- OCR needs Tesseract on the machine that runs the command.

## Final submission: status, setup notes and demo

`docs/final_submission_audit.md` maps every requirement of the assignment PDF to its code,
evidence, verification method, status and remaining limitation. `ASSIGNMENT_CHECKLIST.md` is the
short form. Nothing in this repository has been reviewed by a human: all accuracy verdicts, Q&A
gold answers and retrieval labels are automated or assistant-assisted and are pending owner
review (checklist in the audit document).

### Re-checking the artifacts

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python scripts\audit_submission.py --out artifacts\eval\submission_audit_<date>.json
```

The script is read-only (no model, Qdrant or network). For every extraction JSON in
`artifacts/extraction_v4`, `pipeline_v1`, `pipeline_v2` and `demo_process` it confirms the 20
fields, that every citation names a file of that bid and a page or HTML element that exists in the
parsed artifacts, and that the trace comes from a live model and matches the output. For
`artifacts/qa_examples.json` it confirms each trace exists and each excerpt is verbatim on the
cited pages. It also compares the README retrieval table with `artifacts/eval/retrieval_phase7.json`.
It refuses to overwrite an existing report and does not judge whether a value is correct.

### `ask --collection`

```powershell
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --collection pipeline_check --question "How soon after award must the laptops be delivered?"
```

The name uses the same rule as `process-bid --collection` (3–41 lowercase letters, digits or
underscores). The loopback-only Qdrant check, the bid filter on every search and citation
validation are unchanged. A collection that does not exist fails with `index_not_found` (exit 1),
an invalid name is rejected by the argument parser, and a remote URL is refused. Without the option,
`ask` uses the configured default collection as before (`tests/test_ask_collection.py`).

### OCR on a Windows host

Scanned pages need Tesseract on the machine that runs the parser. The Docker image already has it.
On the Windows host used for these runs it was not installed, so Bid1 FINAL pages 54–59 failed OCR
and every live Bid1 `process-bid` run reports `partial` (exit 3). To enable it (not tested on this
machine):

```powershell
winget install --id UB-Mannheim.TesseractOCR
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
.\.venv\Scripts\python -m rfp.cli process-bid --input data\assessment\Bid1 --output artifacts\pipeline_ocr\Bid1 --collection pipeline_check --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Alternatively set `$env:TESSERACT_CMD` for the session. The local commands ignore `.env`, so its
container path `/usr/bin/tesseract` does not apply on the host. OCR failure stays visible: the parse
stage lists every failed page in `run_summary.json` (`page_warnings`) and the run exits 3.

### Known scope limits

- **Addendum reconciliation covers the due date only.** `agents/reconciliation.py` records a change
  when addendum text states a new date for the due date. Other addendum changes are not applied to
  fields. They can appear in a field only if the extractor cites the addendum directly, and the
  `addendum_changes` list contains only due-date changes.
- **Confidence is not calibrated.** Each field's `confidence` is a fixed value per validation
  status: 0.86 accepted, 0.74 not found, 0.35 unresolved. It is a status code, not a probability.
  Q&A answers report `confidence: null`.
- **Cross-bid questions use `ask --compare-bids`** (Phase A, below). Aspect relations are lexical,
  and only the CLI supports comparison.
- **Repeated page headers and footers are not removed.**
- **The REST API has no `/ask` endpoint.** Q&A is CLI-only; search and indexing have API routes.

### Demo sequence (Windows PowerShell, repository root)

No demo video has been recorded; it is still outstanding. Steps 5 and 7 were run on 2026-10-24
(outputs in `artifacts/final_demo/`). Step 3 was run in Phase 10 as `artifacts/demo_process/Bid2`;
`artifacts\demo_final` is a fresh folder for the recording. Offline steps need no keys, network model
or Qdrant. Live steps need local Qdrant
(`docker compose up -d qdrant`), cached models (`python -m rfp.cli init-models`) and `LLM_PROVIDER`,
`LLM_MODEL` and `LLM_API_KEY` in the private `.env`.

**1. Environment (no keys shown).**

```powershell
uv sync --frozen
Test-Path .env                                   # True; do not print the file
Select-String -Path .env -Pattern '^LLM_(PROVIDER|MODEL|API_KEY)=.+' | ForEach-Object { $_.Line.Split('=')[0] }
docker compose up -d qdrant
```

**2. Tests (offline, fake models).**

```powershell
.\.venv\Scripts\python -m pytest -m "not integration" -q
.\.venv\Scripts\python -m pytest tests\test_pipeline.py tests\test_ask_collection.py -q
```

`tests/test_pipeline.py` runs `process-bid` on a synthetic bid (the fictional Harborview County
Water Authority). It shows the pipeline runs on a folder it was not written for. It does not
measure live accuracy on a real unseen bid.

**3. One command (live).** Use a new output folder each time; a separate collection keeps the main
index untouched.

```powershell
.\.venv\Scripts\python -m rfp.cli process-bid --input data\assessment\Bid2 --output artifacts\demo_final\Bid2 --collection pipeline_check
```

**4. Inspect the outputs.**

```powershell
Get-Content artifacts\demo_final\Bid2\run_summary.json
Get-Content artifacts\demo_final\Bid2\Bid2.json                 # 20 fields, sources, notes
Get-Content artifacts\demo_final\Bid2\verification.json         # per-field citation support
Get-Content artifacts\demo_final\Bid2\traces\extraction_Bid2.json  # agent steps, tokens, latency
```

Saved runs that need no keys: `artifacts/extraction_v4/` (submitted JSON per bid and traces) and
`artifacts/pipeline_v1/`.

**5. Q&A with citations (live).**

```powershell
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid1 --question "What is the final proposal due date after all addenda?"
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --collection pipeline_check --question "How soon after award must the laptops be delivered?"
```

Saved: `artifacts/qa_examples.json` (18 live answers with review notes) and `docs/qa_examples.md`.

**6. Retrieval evaluation (local Qdrant, no LLM).**

```powershell
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --modes dense,sparse,hybrid,hybrid_rerank --out artifacts\eval\retrieval_demo.json
```

Compare with the table in the retrieval section above (`artifacts/eval/retrieval_phase7.json`).

**7. Honest abstention and a refused request.**

```powershell
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --question "What is Jasmine Alzate's email address?"   # live: Not found in documents (she is the Bid1 buyer)
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --question "Bid number?" --collection missing_run --llm-mode test   # index_not_found, exit 1
.\.venv\Scripts\python -m rfp.cli process-bid --input data\assessment\Bid2 --output artifacts\demo_process\Bid2   # output_exists, exit 2; nothing written
```

On 2026-10-24 the first command returned `not_found` with model `gpt-4o-mini`, and
`ask --collection pipeline_check` (step 5) answered "within 45 days of Award" citing
`PORFP_-_Dell_Laptop_Final.pdf` page 2.

**Video script (5–10 minutes, to be recorded):**

| Time | Show | Say |
|---|---|---|
| 0:00–0:45 | Assignment goals, `docs/architecture.md` diagram | Search engine plus agents; every value cited |
| 0:45–2:00 | Retrieval section table, step 6 | Hybrid, reranking, filters; 55 cases; labels not human-approved |
| 2:00–4:00 | Step 3 run and step 4 files | Stages, exit codes, 20 fields, sources, validator, trace |
| 4:00–5:00 | Bid1 `extraction_v4` Due Date and `addendum_changes` | Addendum 2 moves the due date; only the due date is reconciled |
| 5:00–6:30 | Step 5 and step 7 | Verbatim citations; "Not found in documents"; bid isolation |
| 6:30–7:30 | Step 2, `tests/test_pipeline.py` | Synthetic unseen bid, offline; not live accuracy |
| 7:30–9:00 | `docs/final_submission_audit.md` | Partial fields, OCR on host, confidence, human review still needed |

## Phase A: cross-bid comparison and the affidavit fix

```powershell
.\.venv\Scripts\python -m rfp.cli ask --compare-bids Bid1 Bid2 --question "Compare the warranty requirements of both bids."
```

`--compare-bids` takes 2–4 bid labels or UUIDs instead of `--bid-id`; `ask --bid-id` is unchanged.
An empty or single-bid list, a duplicate (by label or resolved UUID), an invalid label or more than
four bids exits 2 (`invalid_comparison`, `duplicate_bid`, `invalid_bid_id`). A bid with no indexed
passages in the local collection exits 2 with `unknown_bid`, found by one bid-scoped dense
probe before any model call. `--collection`, `--qdrant-url` (loopback only), `--top-k`,
`--trace-dir`, `--output` and `--llm-mode` behave as for single-bid `ask`.

How it works (`agents/compare.py`):

- Comparison wording ("compare", "both bids") is removed from the retrieval query. Each bid then
  runs the single-bid retrieval plan (`gather_evidence`) separately, so every search carries exactly
  one bid filter. There is no unscoped search.
- The model sees one section per bid. Evidence ids name the section (A1, A2 for the first bid, B1
  for the second). It returns claims per bid, each with an aspect label and verbatim quotes.
- Each bid's claims are checked by the single-bid validator (`check_reply`: verbatim quotes,
  lexical claim support, list completeness) against that bid's passages only. A claim citing
  another bid's passage is dropped (`cross_bid`), and a quote that is not in the cited passage is
  rejected. One correction request is allowed (`max_corrections`), and claims already validated
  are kept as in single-bid `ask`.
- A bid without validated claims is reported as `Not found in documents`. It is never compared
  from the other bid's evidence.
- Aspect relations are computed from the validated claims, not by the model. `identical` means the
  same normalized claim text or the same quotes, `different` means anything else, and `one_sided`
  means only one bid has a validated statement. This is a lexical comparison, not a judgement of
  equivalence: "3-year minimum" and "3 Years" are reported as `different`.

Response (`ComparisonResponse`):

| Field | Meaning |
|---|---|
| `mode` | `"comparison"` |
| `question`, `bid_ids` | The question and the selected bids, in order |
| `status` | `answered` (every bid has a finding), `partial` (some), `not_found` (none) |
| `answer` | One line per bid, then one line per aspect; exactly `Not found in documents` when no bid has a finding |
| `findings[]` | Per bid: `bid_id`, `status` (`answered` or `not_found`), `answer`, `claims` (text plus citation ids), `citations` (document, page or HTML element id, document type, addendum number, excerpt), `completeness`, `limitations`, `validation`, `evidence_count` |
| `aspects[]` | `aspect`, `relation`, `claims` (bid label to claim texts) |
| `missing_evidence` | Bids reported as not found |
| `limitations`, `validation` | Overall notes; per-bid reasons prefixed with the bid label |
| `trace_id`, `model`, `evidence_count` | Trace reference, provider and model, passages retrieved per bid |

The trace (`qa_compare_<trace_id>.json`) records each bid's probe and searches (`field` is the bid
label), the model attempts with per-bid decisions and quote checks, and token counts.

**Affidavit false abstention.** The live `qa_v7` run asked Bid2 "Which affidavits must vendors
submit with their proposal?". It retrieved PORFP page 2 ("The Master Contractor must provide a
Mercury Affidavit") as the top passage. The first reply's Mercury claim passed validation, but two
other claims ("Vendors must submit a Proposal / Contract Affidavit") had no requirement in their
quotes. The correction prompt allows `not_found`, the model took it, and `ask` returned that
abstention, discarding the validated Mercury claim. Retrieval, passage truncation, parsing and
quote validation were not at fault. `ask` now keeps the latest validated claims when a later
attempt abstains or fails (`usable`, `keep_validated` in `agents/qa.py`). Replaying the `qa_v7`
reply sequence against the local index returns `not_found` with the committed code and the cited
Mercury claim with the fix.

**Live runs** (`gpt-4o-mini`, 2026-10-25, `artifacts/phaseA_qa/`; masked copies in
`submission/evidence/phaseA_*.json`):

| Run | Result |
|---|---|
| Bid2 affidavits | Answered: Mercury Affidavit, PORFP p2. Contract and Proposal Affidavit claims dropped (no requirement quote) and listed as unaddressed. Kept from attempt 1 after attempt 2 failed |
| Warranty comparison (run 1) | Bid1 3-year minimum (FINAL p6) and 5-business-day repairs (p4); Bid2 3 years (PORFP p3). The Bid2 claim adds "following the date of delivery", which is on p3 but not in its quote |
| Warranty comparison (run 2, after a reason-text fix) | Same findings; the Bid2 claim now quotes "3 years following the date of delivery". The Bid1 claim says "for staff laptops", narrower than the source, which lists the 3-year minimum under several device tiers |
| Isolation probe (Mercury Affidavit in both bids) | Bid1 not found; Bid2 answered from its own PORFP and Mercury_Affidavit.pdf |
| Unanswerable comparison (vendor parking) | `not_found` for both bids |

Fake-model tests: `tests/test_compare_qa.py` (21 tests: selection errors, unknown bid, remote
Qdrant, CLI routing, different, identical and one-sided comparisons, both missing, cross-bid and
fabricated citations, malformed output, timeout, retry limit, and the affidavit regressions).

## Submission Evidence and Reproducibility

Generated outputs are written to `artifacts/`, which is git-ignored. The files the assignment asks
for are copied to `submission/evidence/` by `scripts/build_submission_evidence.py`. The copies mask
email addresses and phone numbers and change nothing else. `submission/evidence/README.md`
describes each file, and `docs/submission_artifacts_manifest.md` records the privacy review and
what was left out. No output has been reviewed by a human.

| Evidence file | Demonstrates | Produced by |
|---|---|---|
| `Bid1_extraction.json`, `Bid2_extraction.json` | 20 cited fields per bid, addendum change, validation counts | `extract-bid` (live) |
| `Bid1_extraction_trace.json` | One full multi-agent run with tokens and latency | `extract-bid` (live) |
| `qa_examples.json` | 18 live Q&A results (13 cited answers, 5 abstentions) | `ask-samples` (live), plus assistant review notes |
| `retrieval_evaluation.json` | Four retrieval configurations on 55 labeled cases | `evaluate_retrieval.py run` (local search, no LLM) |
| `submission_audit.json` | Automated artifact checks | `audit_submission.py` |
| `Bid2_process_bid_run_summary.json` | One-command pipeline stages and exit code | `process-bid` (live) |
| `demo_ask_*.json` | Cited answer, separate collection, abstention | `ask` (live) |
| `phaseA_ask_bid2_affidavits.json` | Affidavit answer after the false-abstention fix | `ask` (live) |
| `phaseA_compare_*.json` | Warranty comparison (two runs), isolation probe, unanswerable comparison | `ask --compare-bids` (live) |

**Commands that produced the originals** (Windows PowerShell; live steps need local Qdrant with the
assessment bids indexed, cached models, and the assessment bid folders in `data/assessment/`):

```powershell
# Extraction JSON and traces (Phase 9 code)
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts\extraction_v4 --trace-dir artifacts\extraction_v4\traces
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid2 --output artifacts\extraction_v4 --trace-dir artifacts\extraction_v4\traces
.\.venv\Scripts\python scripts\verify_extraction.py --outputs artifacts\extraction_v4 --traces artifacts\extraction_v4\traces --out artifacts\eval\extraction_v4_check.json

# Q&A log (raw run; qa_examples.json adds an assistant review note per answer)
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_phase7_questions.json --output artifacts\qa_phase7_final

# Retrieval evaluation (no LLM)
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --modes dense,sparse,hybrid,hybrid_rerank --out artifacts\eval\retrieval_phase7.json

# One-command pipeline and demo answers
.\.venv\Scripts\python -m rfp.cli process-bid --input data\assessment\Bid2 --output artifacts\pipeline_v1\Bid2 --collection pipeline_check
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid1 --question "What is the final proposal due date after all addenda?" --trace-dir artifacts\final_demo\traces --output artifacts\final_demo\ask_bid1_due_date.json

# Phase A (affidavit question and comparisons)
.\.venv\Scripts\python -m rfp.cli ask --bid-id Bid2 --question "Which affidavits must vendors submit with their proposal?" --trace-dir artifacts\phaseA_qa\traces --output artifacts\phaseA_qa\01_affidavit_bid2.json
.\.venv\Scripts\python -m rfp.cli ask --compare-bids Bid1 Bid2 --question "Compare the warranty requirements of both bids." --trace-dir artifacts\phaseA_qa\traces --output artifacts\phaseA_qa\05_warranty_compare_after_reason_fix.json

# Artifact audit and evidence copies
.\.venv\Scripts\python scripts\audit_submission.py --out artifacts\eval\submission_audit.json
.\.venv\Scripts\python scripts\build_submission_evidence.py build
.\.venv\Scripts\python scripts\build_submission_evidence.py check
```

**Verifying from a clone** (no artifacts, models, Qdrant or keys):

```powershell
.\.venv\Scripts\python scripts\build_submission_evidence.py verify
.\.venv\Scripts\python -m pytest -m "not integration" -q
```

`evaluate_qa.py verify`, `evaluate_retrieval.py verify` and `audit_submission.py` check labels and
citations against the parsed bid artifacts, so they need the local assessment data.

**LLM configuration.** Live commands read `LLM_PROVIDER` (`openai` or `openai_compatible`),
`LLM_MODEL` and `LLM_API_KEY`, plus optional `LLM_BASE_URL`, from the private `.env`. All live
evidence used `openai` / `gpt-4o-mini`, as recorded in each trace. Keys are never written to outputs;
`verify_extraction.py` and `audit_submission.py` scan for them.

**Live versus offline.** Every file in `submission/evidence/` came from live runs on the two
assessment bids, or from local search with no LLM (retrieval). The test suite (407 offline tests,
6 integration tests) uses fake models and constructed or synthetic documents. `tests/test_pipeline.py`
runs `process-bid` on a fictional bid, and it is not evidence of live accuracy.

**Retrieval results** (55 cases, 50 scored, default rerank depth 20; rerun on 2026-10-24 with identical
metrics): dense 0.703 Recall@5 / 0.688 MRR@10; sparse 0.786 / 0.781; hybrid 0.736 / 0.795; hybrid +
rerank 0.772 / 0.769. The full table is in the retrieval section above.

**Known limitations of the evidence.**
- Extraction (model-assisted review): Bid1 9 correct and 7 partial; Bid2 11 correct and 5 partial;
  no wrong values. Partial fields include Bid2 Due Date without time or zone, and incomplete
  documentation, product and specification fields.
- `extraction_v4` is the chosen Phase 9 run. Earlier attempts ran on changing code, and a same-code
  repeat differed in some fields (`docs/live_validation.md`). Values vary between runs.
- Q&A: 5 false abstentions on the 14-case gold set (9/14 correct) in the `qa_v7` run, including the
  Bid2 affidavits question. The affidavit cause is fixed and the live re-run answered it (Mercury
  Affidavit only, item completeness 1/2). The 14-case set has not been re-run as a whole.
- Cross-bid comparison is CLI-only, and its aspect relations are lexical.
- No real unseen bid was tested; only a synthetic fixture.
- Retrieval labels and Q&A gold answers are assistant-made and not human-approved.
- Confidence values are fixed per status, not calibrated. Only the due date is reconciled from addenda.
- Masked excerpts in `qa_examples.json` (examples 2 and 11) are no longer verbatim.
