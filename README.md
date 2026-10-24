# RFP Intelligence Platform — Phase 2

Phase 2 adds document ingestion, PDF/HTML parsing, native tables and Tesseract OCR to the
Phase 1 infrastructure. Retrieval, embeddings, chunking, agents and the UI remain later phases.
The repository was empty at Phase 1 implementation start.

## Architecture

Python 3.12, FastAPI and Pydantic settings power the API. SQLAlchemy/PostgreSQL holds Bid,
Document and ProcessingJob records; Alembic owns schema changes. Redis is the Celery broker.
Qdrant is provisioned for future indexing. Cloudflare R2 is the sole object-storage adapter;
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
  search/          reserved
  agents/          reserved
  evaluation/      reserved
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
