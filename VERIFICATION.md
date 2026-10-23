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
