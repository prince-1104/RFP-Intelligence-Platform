# Phase 1 verification — 2026-10-24

The workspace was initially empty. All application files are new. No existing user data was
replaced. Python 3.12.15 and uv 0.13.5 were installed into workspace-local tool directories.
The private `.env` was copied from safe example values; no R2 or LLM credentials were supplied.

## Completed checks

| Command | Result |
| --- | --- |
| `uv sync --python 3.12` | Resolved and locked dependencies in `uv.lock`; installed successfully |
| `.venv/Scripts/pytest -q` | 27 passed in 2.62 seconds |
| `.venv/Scripts/ruff check src tests` | Passed |
| `.venv/Scripts/ruff format --check src tests` | All 33 Python files formatted |
| `.venv/Scripts/mypy src/rfp` | No issues in 25 source files |
| `docker compose config --quiet` | Passed |
| `docker compose build` | Shared backend image built successfully without R2 credentials |
| `docker compose up -d postgres redis qdrant` | All three services healthy |
| `alembic upgrade head` with localhost PostgreSQL configuration | Applied revision 0001 |
| `alembic check` against PostgreSQL | No new upgrade operations detected |
| `alembic current` against PostgreSQL | 0001 (head) |
| `alembic upgrade head --sql` | PostgreSQL offline DDL generated successfully |
| `docker compose build migrate` | Final source image built successfully |
| `docker compose up -d --no-build` | Migration service exited successfully; API and worker started |
| `curl.exe --fail --silent http://localhost:8000/health/ready` | Ready; PostgreSQL, Redis and Qdrant healthy |
| Container HTTP request to `/health/live` | `{"status":"alive"}` |
| `docker compose exec worker celery -A rfp.workers.entrypoint:app inspect ping` | One worker online; `pong` |
| `docker compose exec api tesseract --list-langs` | `eng` and `osd` installed |
| `docker compose exec api alembic check` | No new upgrade operations detected |

Tests include settings/environment validation, R2 endpoint derivation, model constraints,
migration upgrade/downgrade and ORM parity on SQLite, healthy/degraded endpoints, error
redaction, correlation IDs, JSON logging, Celery diagnostic execution, and mocked R2 operations
including cleanup after a failed download. No paid service calls were made.

## Limitations and resolved verification issues

- Live Cloudflare R2 access is unverified because credentials were not provided. Use the
  connectivity command in README after configuring a private bucket. Mock tests passed.
- Starlette emits one deprecation warning for its httpx-backed TestClient. Tests pass; the
  warning is visible and is not suppressed.
- The Windows sandbox blocks asyncio's loopback socket pair, hanging TestClient before
  application startup. Tests passed outside that sandbox with approved execution access.
- An initial overly broad lint command touched disposable downloaded runtime/dependency files.
  Python and all packages were reinstalled from fresh downloads, lint exclusions were added,
  and checks were rerun against project source only.
- The initial stream-close test checked the boto3 wrapper's `closed` property. It was corrected
  to verify the underlying stream is closed; both normal and exceptional cleanup now pass.

## Files delivered

- `pyproject.toml`, `uv.lock`: bounded dependencies, locked resolution and development checks.
- `.env.example`, `.gitignore`, `.dockerignore`: configuration guidance and private-file exclusions.
- `Dockerfile`, `compose.yaml`: shared non-root backend image, Tesseract, persistent infrastructure,
  health checks, migration gating and shutdown periods.
- `alembic.ini`, `src/rfp/db/`: models, engine and initial reversible migration.
- `src/rfp/api/`, `src/rfp/core/`: app factory, health probes, configuration and structured logging.
- `src/rfp/storage/`: injectable R2 adapter, storage protocol and bucket connectivity command.
- `src/rfp/workers/`: configured Celery app and diagnostic task.
- `src/rfp/{schemas,ingestion,search,agents,evaluation}/`: documented later-phase placeholders.
- `tests/`: offline verification suite.
- `README.md`, `VERIFICATION.md`: operation instructions and evidence.

No Phase 2 processing, OCR workflow, retrieval, agents, extracted-field logic or frontend was built.

The local stack is left running. From the project directory, start/recreate it with
`docker compose up --build -d`; stop it without removing data with `docker compose down`.

## Cloud configuration follow-up

Private `.env` now supplies Qdrant Cloud authentication and Cloudflare R2 bucket credentials.
No credentials are included in tracked files. Read-only live checks verified Qdrant `/readyz`,
authenticated `/collections` access, and R2 `HeadBucket`. These checks do not verify object
write/delete permissions or create collections. This supersedes the earlier R2-access limitation.

The Qdrant key uses `SecretStr`, requires HTTPS, and is sent in the `api-key` header. Tests
cover authenticated and local probes, rejection of insecure authenticated URLs, and sanitized
readiness responses after authentication failure. The offline suite passes 31 tests (one
existing upstream TestClient deprecation warning); Ruff, mypy and Compose validation pass.

# Phase 2 verification — 2026-10-24

Phase 2 preserves the original settings, engine factory, R2 protocol, Celery factory, logging,
health endpoints and Phase 1 tests. No retrieval, indexing, embeddings, chunking, agents,
comparison or UI was implemented.

## Checks actually executed

| Command / check | Result |
| --- | --- |
| Workspace-local `uv sync` | Updated `uv.lock` and installed bounded PyMuPDF/pdfplumber/BeautifulSoup/lxml/Pillow dependencies |
| `.venv/Scripts/pytest -q` | **81 passed**, one visible upstream TestClient deprecation warning, 16.18 seconds |
| `.venv/Scripts/ruff check src tests examples` | Passed |
| `.venv/Scripts/ruff format --check src tests examples` | All 53 Python files formatted |
| `.venv/Scripts/mypy src/rfp` | No issues in 38 source files |
| `docker compose config --quiet` | Passed |
| `python examples/create_sample.py` | Generated synthetic two-page PDF and HTML |
| `python -m rfp.cli parse-folder --path artifacts/synthetic_bid --output artifacts/parsed` | 2 parsed, 0 partial, 0 failed, 0 ignored; validated JSON written |
| CLI subprocess with invalid private `.env` and no R2 credentials | Passed; offline parsing does not load `.env` |
| `alembic upgrade head` on local PostgreSQL | Applied revision `0002` |
| `alembic check` and `alembic current` on local PostgreSQL | No schema drift; `0002 (head)` |
| Temporary PostgreSQL database: upgrade head → downgrade 0001 → upgrade head | Passed, schema parity checked after upgrades; temporary database removed |
| `docker compose build migrate` | Phase 2 backend image built successfully using frozen dependencies |
| `docker compose up -d --no-build api worker` | Migration completed; rebuilt API and worker started |
| Container offline parse of HTML, native PDF and scanned PDF | 3 parsed, no partial/failures; real Tesseract used on scanned PDF |
| Production CLI: R2 source upload → DB registration → Redis/Celery → parser → R2 JSON | Synthetic job succeeded; all 3 documents succeeded |
| API `GET /api/v1/jobs/{id}` | Returned succeeded and all three parsed artifact object keys |
| Final worker job and R2 JSON download validation | All 3 schema-valid artifacts succeeded; OCR page and completed bid state verified |
| Final container `alembic check` and `docker compose ps` | No drift; API, worker, PostgreSQL, Redis and Qdrant all healthy |

The scanned synthetic page reported Tesseract mean word confidence 94.92 on this environment.
This is recognition diagnostic output, not a probability that business facts are correct.
The two native PDF pages did not trigger OCR; HTML physical page numbers remained null.

Tests cover discovery limits/path validation, symlinks, duplicate-content distinct references,
changed-source detection, real native PDFs/tables and malformed/password-protected PDFs,
per-page partial failures, OCR fallback/force/confidence/failure/image cleanup, parser timeout
termination, HTML visible content/links/locators/no fetching, metadata dates and configurable
classification, schema provenance, deterministic artifacts, mocked R2 persistence/retries,
integrity rejection, job/document/bid state, duplicate processing, task registration/retry/
continuation, API redaction/validation, offline CLI exit codes and missing production R2 config.

## Decisions and files

- `src/rfp/ingestion/{discovery,metadata,options,html,pdf,ocr,parser,runner,service}.py`:
  bounded discovery, reusable deterministic parsing, process isolation and production persistence.
- `src/rfp/schemas/parsed.py`: typed document/page/block/table contracts with provenance checks.
- `src/rfp/cli.py`: separate offline `parse-folder`, production `ingest-folder` and recovery
  `dispatch-job`; no HTTP filesystem-path or ZIP-upload interface is exposed.
- `src/rfp/api/ingestion.py` and existing app factory: versioned job and document inspection.
- Existing models plus `0002_ingestion.py`: nullable provenance additions preserve Phase 1 rows;
  explicit job membership, partial status and persisted non-secret parser configuration.
- Existing R2 adapter: sanitized transient error subtype supports bounded retries without
  retrying authorization/not-found failures. No fallback store was added.
- Existing Celery factory: late-ack ingestion task, per-bid PostgreSQL advisory locking,
  per-document commits and bounded retries/checkpoint continuation.
- `tests/test_{discovery,parsers,ingestion_service,cli,tasks}.py` plus migration test updates:
  offline coverage; original tests remain successful.
- `examples/create_sample.py`: synthetic sources only; generated artifacts stay gitignored.
- `.env.example`, dependency manifest/lockfile, README and `docs/architecture.md`: settings,
  reproducible installation, schema, operator commands, guarantees and limits.

## Resolved issues and remaining limits

- The first PostgreSQL attempt timed out because the local containers were stopped. Services
  were started, and migration/parity checks then passed.
- Pillow's image context manager did not release an in-memory OCR image as expected. Explicit
  `closing(...)` now releases both source and processed images, verified by a test.
- A read-only artifact check overlapped API container replacement and returned “service not
  running”; rerunning after startup validated all three final R2 artifacts successfully.
- OCR does not reconstruct arbitrary scanned tables. Text and word positions remain available
  with explicit warnings. Native table headers can remain ambiguous; rows are retained.
- Ambiguous/unlabeled dates are null. PDF/HTML reading order is best effort.
- Linux parser subprocesses have CPU/address-space limits; Windows has input/image/node/time
  bounds but no OS address-space limit. Container deployment quotas remain operator settings.
- Cross-system R2/DB/broker publication is not atomic. Orphan source objects may remain after
  failed registration; queued jobs can be redispatched after broker or worker outages.
- Terminal failures remain terminal. Operators can correct inputs/configuration for a new job.
  Document records expose latest artifact configuration; historical R2 configuration keys remain.
- One upstream Starlette/httpx deprecation warning remains visible. No test failures remain.
- Real integration exercised only the synthetic verification documents, not unseen assessment
  folders, every possible malformed PDF, OCR language packs beyond English, or arbitrary file sizes.

Exact local sample commands:

```powershell
uv run --frozen python examples/create_sample.py
uv run --frozen python -m rfp.cli parse-folder --path artifacts/synthetic_bid --output artifacts/parsed
```

Stop at Phase 2. Later phases must consume the existing parsed artifacts/provenance rather than
replacing this pipeline or inventing missing source data.

# Phase 3 verification — 2026-10-24

The initial Phase 1/2 baseline was actually rerun: 81 tests passed. Existing ParsedDocument,
Settings, engine/session factory, R2 adapter, retry helpers, bid advisory lock, Celery app and
CLI framework were reused. Missing integration points were canonical content, source occurrences,
revisioned chunk generations and stored parsed-artifact integrity hashes.

## Commands and results

| Check actually executed | Result |
| --- | --- |
| `.venv/Scripts/pytest -q` | **114 passed**, one visible upstream TestClient warning, 21.50 seconds |
| `.venv/Scripts/ruff check src tests examples` | Passed |
| `.venv/Scripts/ruff format --check src tests examples` | All 65 Python files formatted |
| `.venv/Scripts/mypy src/rfp` | No issues in 46 source files |
| Workspace-local `uv sync --frozen` | Checked 75 packages; no dependency or lockfile changes required |
| `python -m rfp.cli chunk-folder --path artifacts/parsed --output artifacts/chunks` | 2 documents, 6 chunks, 6 canonical contents, no failures; parser summary skipped |
| Same local command repeated | Both document artifacts unchanged; deterministic bytes preserved |
| Offline CLI subprocess with invalid private `.env` | Passed without cloud credentials or infrastructure |
| PostgreSQL `alembic upgrade head`, `alembic check`, `alembic current` | Revision 0003 applied; no drift; 0003 (head) |
| Temporary PostgreSQL DB: upgrade head → downgrade 0002 → upgrade head | Passed; no drift after upgrades; temporary database removed |
| Temporary PostgreSQL DB, concurrent same-bid processing | Advisory lock rejected duplicate concurrent execution safely |
| Temporary PostgreSQL DB, concurrent canonical insertion across bids | 2 shared content rows, 4 distinct occurrences, 2 active generations; database removed |
| `docker compose config --quiet` | Passed |
| `docker compose build migrate` | Frozen Phase 3 backend image built successfully |
| `docker compose up -d --no-build api worker` | Migration runner, API and worker started successfully |
| Container `python -m rfp.cli chunk-bid --bid-id ...` | Real Celery/R2/PostgreSQL synthetic job succeeded |
| `GET /api/v1/jobs/{id}` | 3 successful active document generations, 7 occurrences total |
| Download/validate R2 chunk JSON and verify SHA-256 | All 3 artifacts passed schema/hash checks |
| Regenerate chunks from original R2 parsed artifacts | All 3 outputs matched stored chunk JSON byte-for-byte |
| Production `chunk-bid` rerun | Same job/generation identities and successful existing artifacts reused |
| Final rebuilt image: migration parity, R2 validation, deterministic regeneration and readiness | Passed; all 5 services healthy |

The live production check used only the prior synthetic HTML, native PDF and scanned PDF. It
loaded parsed JSON rather than reparsing originals or rerunning OCR. It made no embedding calls,
Qdrant writes, keyword-index updates or paid LLM calls. Bucket access policy was not audited.

## Files and decisions

- `src/rfp/chunking/{normalize,tokens,engine,local,service}.py` and package initializer:
  conservative normalization, replaceable deterministic token estimation, structured chunking,
  offline output and incremental production persistence.
- `src/rfp/schemas/chunks.py`: effective config, canonical text, citation-bearing occurrences,
  table row ranges and validated deterministic result contracts.
- Existing `Settings` and `.env.example`: nested `CHUNKING__...` fields, cross-field token
  validation, supported versions/counter identity, empty behavior and resource bounds.
- Existing models and migration `0003_chunks.py`: ChunkContent, ChunkGeneration, DocumentChunk,
  job-generation membership, parsed JSON SHA-256 and source-hash indexing. Unique constraints
  and a partial unique active-generation index enforce idempotency in the database.
- Existing ingestion service: records parsed-artifact SHA-256 on new parses and delegates chunk
  job inspection without changing the ParsedDocument contract.
- Existing CLI/Celery/API: `chunk-folder`, `chunk-bid`, kind-aware `dispatch-job`, `rfp.chunk`,
  and versioned generation inspection. No second job/storage/configuration framework was added.
- `tests/test_chunk_{engine,persistence,cli,tasks}.py`: 33 added tests; the migration-chain
  test now includes 0003 and its downgrade. The original 81 tests remain successful.
- README and `docs/architecture.md`: tested commands, normalization/table rules, deduplication,
  revision/activation behavior, resource bounds, limitations and Phase 4 handoff.

Default prose budgets are 600 target / 800 maximum / 100 overlap, using the explicitly approximate
`utf8-bytes-div4-v1` counter. Sections and chunk types remain distinct. Table row groups repeat
available headers and retain original cell arrays, row ranges, order, extraction warnings and
locators. Oversized indivisible rows/identifiers are warned rather than sliced. Canonical hashes
reuse computation; every source occurrence, addendum authority and citation remains independent.

Generation identity includes source document revision, exact parsed-artifact SHA-256 and full
effective configuration/version hash. Only a successful generation is atomically activated;
historical occurrences remain. Failed storage/DB operations preserve the old active generation.
Partial output preserves an existing complete generation. Unchanged active generations bypass
processing, artifact reads/writes and activation writes, so short batches cannot starve new work.

## Coverage and resolved issues

Tests exercise Unicode/whitespace and safe hyphen handling, identifier/number/legal-text
preservation, deterministic ordering and overlap, sections and numeric hierarchy, oversized
paragraphs/rows, header repetition, uncertain table/prose duplicates, empty/partial/malformed
inputs, multi-page and HTML locators, OCR diagnostics, canonical reuse with distinct source
files/addendums, configuration/source revisions, idempotent reruns, stale snapshots, atomic
activation rollback, active uniqueness, transient storage recovery, legacy hash backfill,
per-document failure isolation, task registration/retries, CLI exit codes and nested settings.

Final safeguards fixed during implementation:

- Normalized parser blocks are subtracted from normalized page text, preventing artificial
  residual copies after Unicode/soft-hyphen normalization. Identifier boundaries prevent a
  standalone `123` block from deleting the suffix of `PX-123` in residual evidence.
- Unstructured page text remains independent rather than inheriting an unrelated last heading.
- A numbered subsection cannot inherit a parent with a different numeric prefix; unavailable
  hierarchy stays flat with an explicit warning.
- Exact chunk-count limits are checked only when emitting new content. Output expansion from
  repeated large table headers is bounded; limit failures never activate truncated generations.
- Already-active generations are skipped before batch timing/activation, avoiding incremental
  starvation when an operator chooses a short batch window.

## Remaining limits and Phase 4 handoff

- Token counts are estimates. Phase 4 must select the actual embedding tokenizer/model and
  include model version and input transformation in embedding cache identity.
- Unknown HTML/PDF hierarchy cannot be recovered from information Phase 2 did not encode.
  Native table headers may stay unknown; scanned tables remain OCR/text evidence. Ambiguous
  duplicates are retained rather than discarded.
- Source revision selection uses registration timestamp with deterministic UUID tie-breaking.
  Generations and prior source revisions remain historical; deletion/retention policy is an
  operator concern.
- R2 upload and DB activation are not a distributed transaction. An orphan artifact may remain
  after DB failure, but active rows change atomically. Broker recovery uses `dispatch-job`.
- Resource limits, partial states and warnings must be reviewed before model input. In particular,
  a preserved oversized row/identifier must not be blindly sent to a model with a hard context limit.
- The live check covers synthetic documents, not unseen assessment folders or every hostile
  document layout. Production bucket access controls were not independently verified.
- One upstream Starlette/httpx deprecation warning remains visible. No failing checks remain.

Exact offline command in this Windows workspace:

```powershell
.\.venv\Scripts\python -m rfp.cli chunk-folder --path artifacts/parsed --output artifacts/chunks
```

Phase 4 should consume active generation → occurrence → canonical content, preserving metadata
filters and source-specific authority/citations while reusing embedding computation by exact
content/model identity. No Phase 4 implementation is included in this phase.
