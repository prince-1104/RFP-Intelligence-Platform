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
