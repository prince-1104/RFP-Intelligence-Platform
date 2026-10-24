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

# Phase 4 verification — 2026-10-24

The Phase 1–3 baseline (114 tests) was rerun first and stayed green throughout. Phase 4 reuses
Settings, logging, the SQLAlchemy engine, the R2 Storage protocol and retry helpers, the per-bid
advisory lock, `ProcessingJob`, the Celery app and the CLI. New dependencies: `fastembed` 0.9.0 and
`qdrant-client` 1.13.3 (transitively numpy, onnxruntime; Pillow resolved to 12.3.0 because
FastEmbed requires `pillow<13`). Migration `0004_search_index` adds `embedding_cache_entries` and
`chunk_index_states`.

## Commands and results

| Check actually executed | Result |
| --- | --- |
| `.venv/Scripts/pytest -q` | **209 passed, 3 skipped** (live-Qdrant tests without env vars), one upstream TestClient warning, 46.7 s |
| `.venv/Scripts/ruff check src tests examples` | Passed |
| `.venv/Scripts/ruff format --check src tests examples` | 91 files already formatted |
| `.venv/Scripts/mypy src/rfp` | No issues in 61 source files |
| `python -m rfp.cli init-models` (host) | Downloaded/verified bge-small (MIT), Qdrant/bm25 (Apache-2.0), ms-marco-MiniLM-L-6-v2 (Apache-2.0); profile `79fea6c89a6d…` |
| `python -m rfp.cli index-folder --path artifacts/chunks` against compose Qdrant v1.13.4 | 2 generations completed, 6 points, 6 cache misses, 0 truncated |
| Same command repeated | 2 generations `unchanged`, 0 embeddings computed, identical points |
| `search --local --query "PX-420"` (hybrid_rerank) | #1 `specifications.pdf` page 1 prose, #2 same page table rows 1-2, #3 `notice.html` `#items` table; all with exact identifier match |
| `search --local --query "What must accompany delivery?" --top-k 3` | #1 `specifications.pdf` page 2 |
| `python examples/search_smoke.py` (run twice) | All 10 checks passed both runs: all four modes cite PDF page 1 for PX-420; natural-language → page 2; HTML result has null page and `#notice` locator; `ZZ-9999` → no results with `identifier_evidence_not_found` |
| `RFP_QDRANT_INTEGRATION_URL=http://127.0.0.1:6333 RFP_MODEL_SMOKE=1 pytest tests/test_search_integration.py` | 3 passed: live payload indexes and search, app RRF top set equals Qdrant server-side `FusionQuery(RRF)`, real-model end-to-end |
| `docker compose config --quiet` | Passed |
| `docker compose build migrate` | Backend image rebuilt with frozen Phase 4 dependencies |
| `docker compose up -d --no-build api worker` | Migration runner applied 0004; API and worker started |
| Container `alembic current` / `alembic check` | `0004 (head)`; no drift |
| Container `alembic downgrade 0003` → `upgrade head` → `check` | Round trip passed on PostgreSQL; no drift |
| `GET /health/ready` | postgres, redis, qdrant healthy |
| Container API search before models | 503 `embedding_model_unavailable` with `Retry-After: 5` |
| Container `python -m rfp.cli init-models` | Models in `model_cache` volume; same profile fingerprint as Windows host |
| Container API search / `GET /api/v1/index/status` after models | 503 `index_not_found`; status shows `exists: false`, dense+sparse loaded (configured Qdrant from private `.env` has no index yet) |
| `GET /api/v1/search?q=x&top_k=0` | 422 validation envelope with request ID |

## Issues found and fixed during verification

- A transient Qdrant failure stopped the run but omitted not-yet-attempted generations from the
  report; all generations are now pre-registered as `pending`.
- The sparse branch returned zero-score points (no shared weighted term) that still earned RRF
  credit; non-positive sparse hits are now discarded before fusion.
- `localhost` on Windows cost ~2 s per Qdrant request (IPv6 attempt before the IPv4-only compose
  binding); local CLI/smoke defaults now use `http://127.0.0.1:6333` (reindex 30 s → 8.5 s).

## Not verified

- Production `index-bid` / `POST /api/v1/index` through Celery with real R2 embedding artifacts
  and the configured (cloud) Qdrant was **not** executed, to avoid writing to paid production
  resources without explicit approval. That path is covered only by offline tests using SQLite,
  an in-memory storage mock, in-memory Qdrant and fake models.
- No relevance benchmark was run; the only quality evidence is the synthetic sample above.
- Real-model checks used two synthetic documents (6 chunks). Latency figures are single-machine
  CPU observations: rerank ~0.7–0.9 s for 6 candidates including first-call warm-up.

# Phase 4.1 Qdrant compatibility and assessment-input preparation — 2026-10-24

No Qdrant Cloud, R2 or PostgreSQL data was created, modified or deleted, and no production
ingestion/indexing job was enqueued. Cloud access was limited to version/collection-existence reads.

## Versions

| Component | Before | After |
| --- | --- | --- |
| `qdrant-client` (pyproject / uv.lock / image) | 1.13.3, `<1.14` | **1.19.1**, `>=1.19.1,<1.20` (latest 1.19.x; no 1.19.2 client exists) |
| Local compose server | `qdrant/qdrant:v1.13.4` | **`qdrant/qdrant:v1.19.2`** on new volume `qdrant_data_v1_19` |
| Qdrant Cloud server (read-only) | 1.19.2 | 1.19.2 |
| Client compatibility check | disabled | enabled, plus synchronous enforcement in `CollectionManager.check_server` |

The lock change only updated `qdrant-client` and dropped its unused `grpcio-tools`/`setuptools`;
FastEmbed 0.9.0, numpy, grpcio, protobuf, httpx and pydantic are unchanged. Release notes for
1.14–1.19 were reviewed: removed APIs (`search`, `recommend`, `discovery`, `upload_records`,
`add`/`query`, `init_from`, lock methods) are not used. Phase 4 uses `query_batch_points`,
`query_points`, `upsert`, `count`, `scroll`, `set_payload`, `delete`, collection and payload-index
APIs and `info`.

## Commands and results

| Check actually executed | Result |
| --- | --- |
| `uv lock --upgrade-package qdrant-client`, `uv sync --frozen`, `uv lock --check` | Resolved 99 packages; lock consistent |
| `docker compose config --quiet` | Passed |
| `docker compose pull qdrant` / `up -d --no-deps qdrant` | Healthy; `GET /` reports version 1.19.2 |
| `.venv/Scripts/pytest -q` | **214 passed, 5 skipped** (live tests without env vars), 1 upstream warning |
| `ruff check` / `ruff format --check` (`src tests examples`) | Passed / 91 files formatted |
| `mypy src/rfp` | No issues in 61 source files |
| Live `tests/test_search_integration.py` with `RFP_QDRANT_INTEGRATION_URL=http://127.0.0.1:6333 RFP_MODEL_SMOKE=1 RFP_QDRANT_EXPECTED_VERSION=1.19.2` | **5 passed**: collection creation with named dense/sparse vectors and IDF, payload indexes, idempotent reindex, server version 1.19.2 + compatibility, bid/doc-type/addendum filters, identifier branch, retained dense/sparse/identifier ranks and scores with RRF score, PDF page and HTML locator citations, app RRF top set = server `FusionQuery(RRF)`, real-model end-to-end |
| Real client against local 1.19.2 with warnings recorded | client 1.19.1, server 1.19.2, no compatibility warning |
| `examples/search_smoke.py` (existing model cache, no downloads) | All 10 checks passed; fresh 1.19.2 collection rebuilt from embedding cache (6 hits, 0 misses) |
| `docker compose build migrate` | Image contains qdrant-client 1.19.1, fastembed 0.9.0 |
| Offline `docker run --network none` parse-folder + chunk-folder on the existing synthetic fixture (mechanics only, scratch output) | 3 parsed (incl. OCR), 0 failed, 7 chunks; read-only input mount rejected writes |
| `docker compose up -d --no-build api worker` | Migrate no-op at head; API healthy; `/data/assessment` mounted read-only (write rejected) |
| `GET /api/v1/index/status` (container → Qdrant Cloud, read-only) | server 1.19.2, client 1.19.1, enforced check passed; `exists: false` |
| Assessment layout check | `data/assessment/Bid1` and `Bid2` exist and contain **0 files**; git ignores bid files, README trackable |

## Real assessment documents (local only)

All runs were offline (`docker run --network none`, source mounted read-only) or against local
Qdrant 1.19.2 via `index-folder`/`search --local`. Nothing was written to R2, PostgreSQL or
Qdrant Cloud, and no jobs were enqueued.

| | Bid1 | Bid2 |
|---|---|---|
| Bid ID | `5809c088-8514-58c3-a0e5-4d9e72adab0b` | `05876a57-4ff3-52ce-a5fe-9d605ebf94b3` |
| Parsed / partial / failed | 4 / 0 / 0 | 5 / 0 / 0 |
| Chunks (canonical, reused) | 276 (221, 55) | 99 (66, 33) |
| Notable warnings | main RFP: 6 OCR pages, `ocr_table_structure_not_recovered` on 6 pages, 113 ambiguous table headers, `table_prose_overlap_uncertain` | main RFP: 15 ambiguous table headers, `table_prose_overlap_uncertain` |

**Classifier defect found and fixed.** Parser 2.0.0 typed documents by any mention anywhere in
the text, so the Bid1 RFP became `addendum` ("by means of an addendum" in the body), the Bid2
RFP became `affidavit`, `Dell_Laptop_Specs.pdf` became `other` (`_` defeats `\b`) and both
BidNet pages became `rfp`. `classify()` now ranks evidence (filename tokens, HTML listing page,
earliest title term in the first 500 characters, body `rfp` mention) and takes addendum numbers
only from the filename/title of addenda. `PARSER_VERSION` is 2.1.0 so old artifacts re-parse.
After re-parsing, all nine documents have the expected type; bid IDs and chunk counts unchanged.
pytest: 222 passed, 5 skipped; ruff and mypy clean.

**Local index and search (Bid1).** 4/4 generations completed, 276 points, 0 failures,
61 inputs truncated for the dense model (57 in the main RFP), one generation needed a second
attempt. Hybrid-rerank queries returned the cited sources expected: `JA-207652` (identifier
match, BidNet heading then RFP pages), "When are proposals due?" (Addendum 2, page 1, new due
date), "USB 3.1 requirement for display monitor" (Addendum 1, page 1), "warranty period for
devices" (RFP page 4 table rows, then Addendum 1). `--doc-type` filters for addendum, rfp and
bid_page each returned only matching documents. CPU reranking took 4–18 s per query.

## Retrieval-quality hardening (local only)

Local Qdrant 1.19.2 (`127.0.0.1:6333`), local artifacts and `.cache/fastembed` only. Scripts use
`scripts/local_guard.py`, which rejects non-loopback Qdrant and any cloud credentials. Nothing was
written to R2, Qdrant Cloud or PostgreSQL; no jobs were queued; `index-bid` was not run.
Host: 4 logical CPUs, 7.8 GB RAM, shared with other workloads (one parity run coincided with
100% CPU and <600 MB free; latency/memory runs below were taken after load dropped to ~15%).

**Superseded points were not deleted (fixed).** The local collection held 276 inactive points of
the replaced Bid1 chunking-1.0 generation although the index report claimed
`superseded_points_removed: 276`. Qdrant logs for that run show activate/deactivate payload
calls and no `points/delete`; the delete code and its `True` default are unchanged since the
first commit and a scratch-collection reproduction with current code does delete. The exact
reason the delete branch was skipped in that run (most likely `delete_superseded_points`
disabled for that process) cannot be proven from the retained evidence. The defect that hid it
was real: the report counted deactivated points as removed. `_supersede` now reports
`superseded_points_deactivated` and `superseded_points_deleted` (recounted after the delete) and
fails the generation with `superseded_delete_unverified` if stale points remain; searchable state
is already correct at that point and a rerun retries. Regression tests cover a delete that
removes nothing, retention disabled, and a live-server supersession. A local unchanged-path rerun
deleted the 276 points (no re-embedding); the consistency check now reports every source at its
expected active count (Bid1 417, Bid2 116) with zero inactive points.

**Upsert retries.** The earlier "retry" was a reporting artifact (upload batches counted as
attempts), fixed in `c8f2aa8`; both bid runs logged `upsert_retries: 0`. Bounded retry
(`upsert_attempts`, backoff `min(2**attempt, 8)` s, no retry on 4xx) is covered by tests.

**Reranker batching parity.** `rerank_bounded` scores at most `rerank_batch_size` pairs per ONNX
call (length-sorted, input order restored, max over windows for over-limit pairs, failure on
count mismatch or non-finite scores, empty input makes no call). `scripts/rerank_parity.py
parity` ran 52 dataset queries at depth 40 through the real `SearchService`, comparing batch
sizes 8, 32 and 64 against one pair per call: pre-rerank candidate order, final ranking, text,
citations, metadata and warnings identical in 156/156 comparisons; max |score difference|
0.0 (tolerance 1e-4). A spy confirmed the batch size changes the calls (20 / 3 / 1 calls for
batch 1 / 8 / 64) and the scores are still bit-identical, as is FastEmbed's own batching.

**Latency (rerank stage, ms; 13 queries × 3 repeats, warm models).**

| Depth | batch 1 p50/p95 | batch 2 | batch 4 | batch 8 | batch 16 | batch 32 |
|---|---|---|---|---|---|---|
| 10 | 828 / 1250 | | | | 1438 / 1906 | 1422 / 1687 |
| 20 | 1359–1438 / 1906–2953 | 1437 / 2250 | 1687 / 2328 | 1891 / 2359 | 2672 / 5844 | 3016 / 6250 |
| 40 | 2407–2515 / 3750–5032 | 2500 / 3969 | 2703 / 4000 | 2984 / 4860 | 3609 / 4766 | 5328 / 10672 |

Ranges are two separate runs. 0 errors, 0 skipped measurements.

**Memory (peak private commit growth during the workload, fresh process per configuration,
identical warm-up and 13-query workload; base after warm-up ≈ 514 MB).** Batch 1: 11 / 44 / 45 MB
at depth 10 / 20 / 40. Batch 2: 111–113 MB; 4: 242–244 MB; 8: 508–525 MB; 16: 508–838 MB;
32: 524 MB (depth 10, one batch) and 1170–1203 MB. Memory follows batch size, not depth, because
every pair is padded to the longest pair in its batch (windows reach 512 tokens).

**Depth vs relevance (all 52 non-unknown queries; top-5 at depth 10 and 20 differs from depth 40
in 10 and 6 of 13 sampled queries).**

| Mode | Recall@5 | MRR@10 | nDCG@10 | hit@5 | rerank p50 ms |
|---|---|---|---|---|---|
| dense | 0.703 | 0.688 | 0.659 | 0.88 | – |
| sparse (BM25) | 0.786 | 0.781 | 0.749 | 0.88 | – |
| hybrid RRF | 0.736 | 0.795 | 0.741 | 0.88 | – |
| hybrid+rerank depth 10 | 0.742 | 0.751 | 0.725 | 0.88 | 1549 (total, batch 32) |
| hybrid+rerank depth 20 | 0.772 | 0.769 | 0.765 | 0.88 | 4716 (total, batch 32) |
| hybrid+rerank depth 40 | 0.766 | 0.764 | 0.761 | 0.88 | 6881 (total, batch 32) |

Paired bootstrap (5000 resamples): depth 20 − 40 MRR@10 +0.005 [0.000, +0.011] (3 better,
0 worse), nDCG@10 +0.004 [−0.014, +0.022]; depth 10 − 20 nDCG@10 −0.040 [−0.084, −0.005];
hybrid − rerank@40 differences all straddle zero. Every mode: 0 errors, 0 cross-bid leaks,
0 citation errors, 3/3 unknown identifiers returned no evidence with
`identifier_evidence_not_found`.

**Defaults changed:** `rerank_batch_size` 32 → 1 (lowest memory and latency on CPU ONNX) and
`rerank_depth` 40 → 20 (no measurable relevance loss on this dataset, ~45% lower rerank latency;
depth 10 loses nDCG). Both remain configurable. Caveats: two bids, 50 answerable cases labeled
from the documents but not yet human-reviewed; the reranker does not significantly beat hybrid
RRF here; GPU or a different CPU may favour larger batches.

**Known retrieval misses (not tuned):** `Dell_Laptop_Specs.pdf` never ranks for Bid2 RAM,
display and battery questions (PORFP page 3 dominates in all modes); Bid1 award timing and buyer
contact are outside the reranked top 10.

**Local validation of both bids.** Point counts equal active occurrences per source; exact
identifiers (`JA-207652`, `BPM044557`, SKU) rank the right evidence first; doc-type filters:
342 filtered searches (19 dataset queries × every doc type × dense/sparse/hybrid) returned 3087
citations, all of the requested type and bid (types seen: Bid1 addendum/bid_page/rfp, Bid2
affidavit/bid_page/rfp/specs); unknown `JA-999999`, `E20P9999999` and `210-ZZQX` return no results; PDF citations carry
one-based pages, HTML citations carry element locators and null pages; no bid leaks. Two HTML
points have locators with no element ID, heading or non-zero element index (not yet inspected).

**OCR pages without table structure (main RFP pages 54–59).** These pages are an embedded IRS
Form W-9 with no text layer: 0 characters and 0 images, only 375–1247 vector rectangles per page
(glyph outlines), so native text and pdfplumber table extraction see nothing. Tesseract reads them
at 92.6–95.5 mean confidence and two-column prose comes out in reading order. The limitation is
in our pipeline, not OCR quality: `ocr_page` returns only text lines/blocks, there is no OCR
table-structure reconstruction, and `ocr_table_structure_not_recovered` is appended to every OCR
page unconditionally. Only pages 57–58 (exempt-payee and name/TIN two-column charts) and the
page-54 form grid have tabular structure; their column text is interleaved in OCR output. No
cells were invented. Follow-up (separate parser work): build cells from Tesseract word boxes
within the detected ruling lines (pdfplumber finds 16–28 lines on pages 54, 56–58), and emit the
warning only when ruling/column layout is detected.

Verification: `pytest` 269 passed with local Qdrant integration and real-model smoke enabled
(263 passed, 6 skipped by default); `ruff check`, `ruff format --check` and `mypy` clean (this
also fixed B023/E501/format and two optional-int comparisons already present in `c8f2aa8`).

## Not yet verified

Human review of the evaluation labels (`needs_human_labeling`), and the production canary
(R2, PostgreSQL, Qdrant Cloud, Celery).
