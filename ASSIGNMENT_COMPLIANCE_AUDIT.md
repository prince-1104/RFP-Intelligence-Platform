# RFP Intelligence Platform — Assignment Compliance Audit

> Historical audit from before Phases 5–7. The current status of each requirement is in
> `ASSIGNMENT_CHECKLIST.md`; live results are in `docs/live_validation.md`.

Audit date: 2026-10-24. Source of truth: `Assignment-AI Engineer.pdf` (7 pages; copy at `Statements (AI Engineer-Emplay Inc)/Assignment-Data-Statements (AI Engineer-Emplay Inc)/Assignment-AI Engineer.pdf`). This audit inspected the repository only. It did not change application code, tests, evaluation labels, or generated review artifacts, and it did not run `index-bid`, pytest, or `scripts/evaluate_retrieval.py`.

Statuses:

- **PASS** — implemented, integrated into the workflow that exists, and supported by code plus tests or recorded execution.
- **PARTIAL** — real implementation exists, and an important part of the requirement is still incomplete.
- **MISSING** — no adequate implementation.
- **UNVERIFIED** — code may exist, and this audit does not have enough execution evidence.

Historical numbers below are cited from `VERIFICATION.md` or from the user-reported later run. They are not a fresh validation from this audit.

## Overall assessment

Parts A and B are largely built. Ingestion, structure-aware chunking, hybrid retrieval, reranking, citations, a search API/CLI, and a 55-case evaluation set are executable and tested. Parts C and D are not started: `src/rfp/agents/` is a reserved package, `pyproject.toml` has no agent or LLM client dependency, and the repository contains no per-bid extraction JSON, cited Q&A log, or agent trace.

Against the scoring weights, the search engine (25%) is the part that can already be demonstrated. The multi-agent system (25%) and extraction accuracy (20%) cannot. Robustness (10%) is real for parsing and search and unproven for extraction on an unseen folder. Code quality (10%) is strong for the phases that exist. Documentation and demo (10%) cover Phases 1–4 and omit the agent design, the required README results table, the JSON outputs, the Q&A sample, the trace, and the demo.

## Compliance matrix

| Assignment requirement | Status | Evidence in repository | Test or execution evidence | Remaining gap |
|---|---|---|---|---|
| Part A. Parse HTML bid pages and keep structure | **PASS** | `src/rfp/ingestion/html.py` (BeautifulSoup/lxml: title, headings, paragraphs, lists, tables, links, ids). Wired by `src/rfp/ingestion/parser.py` and `parse-folder` in `src/rfp/cli.py`. | `tests/test_parsers.py::test_html_provenance_and_safe_content`, `test_malformed_html_missing_title_and_empty`. Assessment HTML is present under `data/assessment/`. | HTML keeps header/navigation text (`html.py` comment near the visible-text pass). That is intentional and conflicts with the cleaning bullet below. |
| Part A. Parse PDFs and keep page structure | **PASS** | `src/rfp/ingestion/pdf.py` (PyMuPDF blocks, one-based pages, bounding boxes; pdfplumber tables). | `tests/test_parsers.py::test_native_pdf_pages_and_tables`, `test_page_failure_and_limit`, `test_subprocess_parser_integration`. | None for native text PDFs. |
| Part A. Extract specification and bid-information tables | **PASS** | PDF: pdfplumber rows, first row kept when headers are ambiguous. HTML: header-row detection. Chunks: `table_rows` in `src/rfp/chunking/engine.py` (`_table_text`, row groups, repeated headers). | Parser tests above; chunk tests in `tests/test_chunk_engine.py`. | Scanned pages do not become table cells. `ocr_page` returns lines only. Recorded in `VERIFICATION.md` for Bid1 RFP pages 54–59 (IRS W-9). OCR itself is optional. |
| Part A. Clean text (headers/footers, line breaks, hyphenation, whitespace) | **PARTIAL** | `src/rfp/chunking/normalize.py::normalize`: NFC, whitespace, wrapped-line joining, soft-hyphen removal only. ASCII hyphens kept. | Chunk-engine and CLI tests exercise normalization. README “Phase 3” documents the rules. | Repeated PDF headers/footers are not stripped. HTML nav is preserved. The dataset note on `b1-id-solicitation` says the bid number is in every page header. |
| Part A. Metadata: bid_id, file_name, doc_type, addendum_number, page_number, document date | **PASS** | `src/rfp/ingestion/metadata.py::classify` sets `doc_type` (`bid_page`, `rfp`, `addendum`, `specs`, `affidavit`, else `other`), `addendum_number`, and `document_date` only for an explicit label. `parser.py` copies them onto the document. Pages carry `page_number`. Bid id comes from `--bid-id` or the folder name (`data/assessment/README.md`). | `tests/test_parsers.py::test_metadata_classification`, `test_metadata_classification_ranks_evidence`, `test_explicit_dates_and_ambiguity`, `test_configurable_metadata_patterns`. | Dates without a supported publication label stay null. Ambiguous type or date becomes `other` / null plus a warning. |
| Part A. Graceful failure for scanned, empty, and unreadable files, with logs | **PASS** | Subprocess parser, partial/failed status, warning codes, `summary.json`. `src/rfp/core/logging.py::log_event` emits JSON events without document text. | `tests/test_parsers.py::test_corrupted_and_encrypted_pdf`, `test_ocr_failure_partial_and_empty`, `test_missing_tesseract_is_safe`; `tests/test_cli.py` exit codes. | Logs omit exception contents by design. Operators use warning codes, not stack traces. |
| Part B. RFP-suited chunking with overlap, justified in the README | **PASS** | `src/rfp/chunking/engine.py`: section/heading paths, sentence boundaries, table row groups, ~100-token overlap inside one section. Defaults in README: target 600, max 800, overlap 100 estimated tokens (`utf8_bytes_v1`, and a model tokenizer path in `src/rfp/chunking/tokens.py`). | `tests/test_chunk_engine.py`, `tests/test_token_budget.py` (real BGE tokenizer test skips if `.cache/fastembed` is absent). | Overlap and budgets are estimates unless the model counter is selected. |
| Part B. Embeddings | **PASS** | `src/rfp/search/models.py` FastEmbed `BAAI/bge-small-en-v1.5`, 384-dim, query instruction. Profile hash in `docs/architecture.md`. | Unit tests mock the runtime (`tests/search_fakes.py`). `tests/test_token_budget.py::test_real_bge_chunks_fit_the_budget_and_are_never_truncated` uses the local cache or skips. `tests/test_search_integration.py::test_real_models_end_to_end` runs only when `RFP_MODEL_SMOKE=1`. Recorded local indexing is in `VERIFICATION.md`. This audit did not re-run it. | None for the indexing requirement. No API embedding provider is wired; local ONNX is the implementation. |
| Part B. Vector store with chunk metadata | **PASS** | Qdrant named vectors `dense` and `bm25` via `src/rfp/search/collection.py`. One point per occurrence in `src/rfp/search/indexing.py::IndexingService.index`. Payload includes bid, document, type, addendum, pages, locators (`src/rfp/search/payload.py`). | `tests/test_search_indexing.py`, `tests/test_search_collection.py`. Live server tests in `tests/test_search_integration.py` skip unless `RFP_QDRANT_INTEGRATION_URL` is set (5 tests). | Production path writes Qdrant through Celery. This audit did not inspect a live collection. |
| Part B. Incremental indexing | **PASS** | Unchanged generations skip recomputation. A new generation supersedes only that bid/path. `IndexingService` plus `index-folder` / `index-bid`. | `tests/test_search_indexing.py::test_reindex_is_idempotent_and_unchanged`, `test_incremental_new_generation_supersedes_only_its_source`. | Adding a folder still requires an operator command. It does not re-embed other bids. |
| Part B. Hybrid search (dense + keyword, fused) | **PASS** | `SearchService.search` in `src/rfp/search/service.py`. Modes `dense`, `sparse`, `hybrid`, `hybrid_rerank`. BM25 via FastEmbed `Qdrant/bm25`. Fusion: `src/rfp/search/fusion.py::reciprocal_rank_fusion` (k=60). Exact identifiers are a third branch (`src/rfp/search/identifiers.py`) because BM25 splits codes. | `tests/test_search_query.py::test_rrf_scores_ties_and_weights`; `tests/test_search_service.py::test_all_modes_find_exact_identifier_with_pdf_citation` (fake vectors). Historical four-mode metrics in `VERIFICATION.md`. | Default fusion weights are equal. Hybrid RRF did not beat sparse Recall@5 in the recorded table (0.736 vs 0.786). |
| Part B. Metadata filtering (bid, doc type, addendum, …) | **PASS** | `SearchFilters` in `src/rfp/schemas/search.py`: bid, document, doc type, addendum number, chunk type, relative path. Active generation and embedding profile are always applied (`service.py`). | `tests/test_search_service.py::test_bid_filter_isolates_bids`, `test_metadata_filters`; `tests/test_search_api.py::test_get_search_with_filters_and_citations`, `test_post_search_with_multiple_filters`. `VERIFICATION.md` records 342 filtered searches with no cross-type citations. | The evaluation set does not set `doc_types` on any case (`evaluation/review_packet/SUMMARY.md`). Filter quality is tested, not scored in the metric table. |
| Part B. Re-ranking | **PASS** | `rerank_bounded` and `FastEmbedReranker` in `src/rfp/search/models.py`. Model `Xenova/ms-marco-MiniLM-L-6-v2`. Default depth 20, batch size 1. Identifier bonus after the cross-encoder. | Fake-scorer tests in `tests/test_search_service.py` and `tests/test_rerank_batching.py`. `test_real_cross_encoder_batches_match_one_pair_per_call` uses the local cache or skips. `scripts/rerank_parity.py` is the recorded real-model parity check (`VERIFICATION.md`: 156/156 identical rankings). | Recorded metrics: hybrid+rerank depth 20 does not clearly beat hybrid RRF (MRR@10 0.769 vs 0.795). The reranker is integrated; the relevance gain on this set is small. |
| Part B. Query rewriting / expansion | **PASS** | `src/rfp/search/query.py::plan_query` and `expand`. `DEFAULT_SYNONYMS` in `src/rfp/schemas/search.py` maps `deadline` to `submission deadline`, `due date`, `closing date`, plus bid number, manufacturer, part number, and pre-bid meeting. Expansion is added only to the sparse query. Dense keeps the original wording. No LLM rewrite. | `tests/test_search_query.py::test_expansion_is_additive_bounded_and_word_bounded`, `test_plan_query_branches`; `tests/test_search_service.py::test_synonym_expansion_is_reported`. | The assignment’s example is covered. There is no learned or LLM query rewrite. Synonyms are a fixed list. |
| Part B. Citations (file, page, chunk text) | **PASS** | `Citation` and `EvidenceResult` in `src/rfp/schemas/search.py`: `original_file_name`, `relative_path`, `page_start`/`page_end`, HTML locators, section path, table rows, chunk text on the result. Duplicates collapse and keep `additional_citations`. | `tests/test_search_service.py::test_one_based_page_citation_for_later_page`, `test_html_citation_has_locator_and_null_page`, `test_duplicate_text_collapsed_with_all_citations`. `citation_problems` in `src/rfp/evaluation/retrieval.py`. | Two HTML points in the review packet have locators with no element id, heading, or non-zero index (`SUMMARY.md`). |
| Part B. Search usable as API and/or CLI | **PASS** | `GET/POST /api/v1/search`, `POST /api/v1/index` in `src/rfp/api/search.py`. CLI `search`, `index-folder`, `init-models` in `src/rfp/cli.py`. Library entry: `SearchService.search`. | `tests/test_search_api.py`, `tests/test_search_cli.py::test_index_folder_then_cited_search` (fakes). | `POST /ask` is not implemented. That endpoint is the Q&A mode, audited under Part C. |
| Part B. ≥15 question → expected source-passage pairs, both bids | **PASS** | `evaluation/assessment_retrieval.json` version 1.0. This audit counted 55 cases (Bid1 27, Bid2 28), 50 answerable, 86 judgments, 3 unknown identifiers, 2 unanswerable. Kinds: identifier 5, specification 11, deadline 6, addendum_change 8, affidavit 6, requirement 14. | `tests/test_retrieval_evaluation.py::test_committed_dataset_meets_coverage_requirements` requires ≥30 answerable cases, both bids, and those kinds. Loader: `EvalDataset` / `Judgment` in `src/rfp/evaluation/retrieval.py`. | Labels are machine-checkable passages, not human-approved answers. See the retrieval section. |
| Part B. Recall@k, MRR or nDCG, at least two configurations, table in the README | **PARTIAL** | Metrics: `recall_at`, `reciprocal_rank_at`, `ndcg_at`, `summarize` in `src/rfp/evaluation/retrieval.py`. Runner compares `dense`, `sparse`, `hybrid`, `hybrid_rerank` in `scripts/evaluate_retrieval.py::run`. | Unit tests use hand-built results, not live retrieval: `test_metrics_for_relevant_result_at_rank_two`, `test_duplicate_hits_are_credited_once_and_grades_weight_ndcg`. The four-mode table is in `VERIFICATION.md` (about lines 474–481), not in `README.md`. | README has no results table. This audit did not re-run the evaluator, so the published numbers are historical. |
| Part C. Orchestrator / planner | **MISSING** | `src/rfp/agents/__init__.py` is a package docstring. `src/rfp/agents/README.md` says the package is reserved and that no agent behavior is implemented. README line 7: agents remain a later phase. `docs/architecture.md` says Phase 4 returns evidence only. | No orchestrator tests. | No plan, routing, or “job done” decision. |
| Part C. Ingestion agent | **PARTIAL** | The ingestion responsibility is a deterministic pipeline (`discovery.py`, parsers, `ingest-folder`, Celery `rfp.ingest`), not an agent. The assignment allows merging roles if explained. | Ingestion tests listed under Part A. | Nothing in `agents/` calls ingestion. The README does not explain this as an agent-design choice; it defers agents. |
| Part C. Retrieval agent that calls the search engine as a tool | **PARTIAL** | `SearchService.search` is the tool surface. Architecture text says future LangGraph tools would call it. No agent imports `rfp.search`. | Search tests only. | No agent rewrites a question, calls search, and returns evidence to another agent. |
| Part C. Extraction agent(s) for the structured fields | **MISSING** | No field schema, prompt, or extractor. README: “No extracted-field schema is invented.” | None. | All 20 fields are unextracted. |
| Part C. Addendum reconciliation agent | **MISSING** | Addendum number and doc type are metadata and search filters. No component compares a base RFP with later addenda or applies the latest change to a field. | Dataset cases of kind `addendum_change` test retrieval of those passages, not reconciliation. | No change log and no updated-field output. |
| Part C. Validator / critic and bounded re-extraction | **MISSING** | Celery tasks retry transient index/ingest/chunk failures (`src/rfp/workers/celery_app.py`, max 3). That is job retry, not field validation. | `tests/test_tasks.py`, `tests/test_search_tasks.py::test_index_task_retries_and_disposes`. | No per-field evidence check, format check, hallucination check, confidence, or re-extraction loop. |
| Part C. Q&A / report agent | **MISSING** | Search returns passages. It does not answer. | None. | No cited answers and no human-readable bid summary. |
| Part C. Shared state (plan, evidence, drafts, validation, final output) | **MISSING** | Search and chunk models are Pydantic. They are not a shared agent state object. | None. | No state object is passed between agents. |
| Part C. Parallel agent execution where practical | **MISSING** | Per-bid PostgreSQL advisory lock serializes ingest, chunk, and index. Search branch queries are one request, not parallel agents. | Concurrency tests cover the embedding semaphore (`tests/test_search_collection.py::test_runtime_bounds_concurrent_inference`). | Field groups are not extracted concurrently. |
| Part C. Structured agent-to-agent outputs | **MISSING** | `SearchRequest` / `SearchResponse` are structured. No agent message schema. | Search schema tests indirectly via API tests. | No Pydantic models for plans, field drafts, or validation reports. |
| Part C. Guardrail: “Not found in documents”, every value cited | **PARTIAL** | Unknown identifiers return no hits plus `identifier_evidence_not_found` (`service.py`, policy `require_evidence`). Empty search adds `no_results`. | `tests/test_search_service.py::test_unknown_identifier_requires_evidence`. | Nothing produces the sentence “Not found in documents” or refuses an uncited field value, because nothing emits field values. |
| Part C. Observability: each agent step, tool calls, tokens, latency; one trace | **PARTIAL** | `search_completed` logs mode, query hash, expansion count, filters, candidate counts, `timings_ms` (`service.py`). Query text and chunk text are not logged. | `tests/test_search_service.py::test_search_log_contains_metadata_not_query_text`. | No agent trace file. No token or LLM-cost log. Search latency is the only timing record. |
| Part C. Errors (timeout, bad JSON, empty retrieval) must not crash the pipeline | **PARTIAL** | Search: `SearchServiceError`, timeout, degraded mode (`allow_degraded`). Eval: `evaluate_case` records one case error and continues. Ingest: per-document partial failure. | `tests/test_search_api.py::test_search_timeout`, `test_service_errors_are_redacted_and_retryable`; `tests/test_retrieval_evaluation.py::test_search_errors_are_recorded_per_case_and_summarized`. | No agent pipeline exists to survive a bad model JSON payload. |
| Part C. Extraction mode and question-answering mode | **MISSING** | CLI commands are `parse-folder`, `ingest-folder`, `chunk-folder`, `chunk-bid`, `index-folder`, `index-bid`, `search`, `init-models`, `dispatch-job`. No extract or ask command. No `POST /ask`. | None. | The five sample questions in section 7.3 are not implemented. |
| Part D. Extract the 20 specified fields per bid | **MISSING** | Field names from the PDF (Bid Number, Title, Due Date, Bid Submission Type, Term of Bid, Pre Bid Meeting, Installation, Bid Bond Requirement, Delivery Date, Payment Terms, Any Additional Documentation Required, MFG for Registration, Contract or Cooperative to use, Model_no, Part_no, Product, contact_info, company_name, Bid Summary, Product Specification) do not appear as an extraction schema. | None. | Entire section 8. |
| Part D. One JSON file per bid: value, sources, confidence; null plus a reason; `addendum_changes`; `validation` | **MISSING** | No bid output JSON in the repo. The only evaluation JSON files are `evaluation/assessment_retrieval.json` and `evaluation/review_packet/packet.json`. | None. | No `Bid1` or `Bid2` extraction file. |
| Technical. Python 3.10+; secrets and model names from the environment | **PASS** | `requires-python = ">=3.12,<3.13"` in `pyproject.toml`. `src/rfp/core/config.py` loads settings from the environment. README: provider/model/API-key settings are reserved and are not used to call an LLM. `.env` is gitignored. | `tests/test_foundation.py::test_environment_loading`, `test_settings`. | No LLM provider is configured because no LLM is called. |
| Technical. Runnable with one command after setup | **PARTIAL** | `docker compose up` starts API, worker, Postgres, Redis, Qdrant (`compose.yaml`, `Dockerfile`). Local search is several commands: parse, chunk, `init-models`, `index-folder`, `search`. | README and `data/assessment/README.md` document those commands. This audit did not start the stack. | There is no `python main.py --bid ./Bid1` and no single command that emits the structured JSON. |
| Technical. Modular layout (ingestion, search, agents, api, eval, tests) | **PASS** | `src/rfp/ingestion`, `chunking`, `search`, `api`, `evaluation`; `evaluation/`; `tests/`. `agents/` exists and is empty of behavior. | Layout matches the README tree. | The agents package does not yet contain the system. |
| Technical. Unit tests for parsing and search at minimum | **PASS** | Parser, discovery, chunk, search service/query/indexing/API/CLI, and retrieval-metric tests. Default search tests use `tests/search_fakes.py`. | `VERIFICATION.md` records pytest 269 passed (263 passed and 6 skipped without live Qdrant and `RFP_MODEL_SMOKE`). A later report of 276 passed and 6 skipped, after the review package, was not re-run here. `tests/test_review_packet.py` is additional offline coverage. | Passing counts are historical. |
| Technical. Unseen bid folder without code changes | **PARTIAL** | `src/` contains no `Bid1`, `Bid2`, or `JA-207652` literals (repo search). Discovery accepts any folder of PDF/HTML. Bid id is the folder name or `--bid-id`. | Assessment docs show Bid1 and Bid2 only. | Extraction on a third folder cannot be shown. Search/indexing generality is implemented and not demonstrated on an unseen folder in this audit. |
| Deliverable. Source for ingestion, RAG, agents, and API/CLI | **PARTIAL** | Ingestion, RAG, and search API/CLI are present. | Tests above. | Multi-agent source is absent. |
| Deliverable. README: setup, how to run each mode, design decisions (chunking, embeddings, retrieval, agent framework, prompts) | **PARTIAL** | `README.md` covers setup, chunking, models, RRF, reranking, and search CLI/API. `docs/architecture.md` has mermaid diagrams for parse, chunk, and search. | N/A (documentation). | No agent framework, prompts, extraction mode, or Q&A mode. No retrieval results table. |
| Deliverable. Architecture diagram of the search engine, the agents, and how they communicate | **PARTIAL** | Three mermaid diagrams in `docs/architecture.md` (ingestion, chunking, hybrid search). | N/A. | No agent diagram. The Phase 4 diagram ends at cited evidence. |
| Deliverable. JSON output, one file per bid | **MISSING** | Not in the tree. | None. | Required by section 11. |
| Deliverable. Retrieval evaluation report (question set and metrics per configuration) | **PARTIAL** | Question set: `evaluation/assessment_retrieval.json`. Metrics write-up: `VERIFICATION.md`. Machine packet: `evaluation/review_packet/`. Reproduction command: `scripts/evaluate_retrieval.py run`. | Metric math is unit-tested. The published table was not regenerated in this audit. | The assignment asks for the table in the README. Labels are not human-approved. |
| Deliverable. ≥10 cited Q&A examples | **MISSING** | No Q&A log. | None. | Required by section 11. |
| Deliverable. One full extraction agent trace | **MISSING** | No trace artifact. Search logs are not an extraction trace. | None. | Required by section 7.2 and section 11. |
| Deliverable. 5–10 minute demo video or a live demo | **MISSING** | No video in the repository. | None. | A live demo can still be given later. Nothing in-repo supports it yet. |
| Bonus. Web UI | **MISSING** | No Streamlit, Gradio, or frontend app. README defers the UI. | None. | Optional. Do not build this before Parts C and D. |
| Bonus. OCR for scanned PDFs | **PASS** | `src/rfp/ingestion/ocr.py`, Tesseract in the image, `--force-ocr`. | `tests/test_parsers.py::test_ocr_fallback_and_force` mocks recognition. `VERIFICATION.md` records real Tesseract output on Bid1 pages 54–59 (mean confidence about 92.6–95.5) and states table structure was not recovered. | Optional, and already present. Table reconstruction is a follow-up, not a mandatory gap. |
| Bonus. Bid comparison agent | **MISSING** | Not present. | None. | Optional. |
| Bonus. Go / no-go recommendation | **MISSING** | Not present. | None. | Optional. |
| Bonus. Semantic cache of repeated queries, plus cost and latency per run | **PARTIAL** | Embedding cache by content hash and profile: `src/rfp/search/cache.py`. Search responses include `timings_ms`. | `tests/test_search_cache.py`. | This is an embedding cache, not a semantic cache of repeated queries. No per-run LLM cost. |
| Bonus. Docker and a CI pipeline that runs tests and retrieval evaluation | **PARTIAL** | `Dockerfile`, `compose.yaml`. | README compose health checks. This audit did not run compose. | No `.github/workflows` (or other CI config) was found. Retrieval evaluation is not in CI. |

## Retrieval evaluation

### Structural minimum

The assignment asks for at least 15 question-to-expected-source-passage pairs covering both bids. The committed set exceeds that:

- 55 cases, both bids (27 and 28).
- 50 answerable cases, each with one or more judgments (86 judgments total).
- Each judgment is a document plus a one-based PDF page or an HTML element id, plus text that must appear in the chunk, plus grade 2 (direct) or 1 (partial).
- 5 cases are deliberately non-answerable (3 unknown identifiers, 2 unanswerable natural-language questions) and have no judgments. That is consistent and extra, not a substitute for the 15.

`tests/test_retrieval_evaluation.py::test_committed_dataset_meets_coverage_requirements` locks the coverage floor. A full human approval of all 55 cases is not required to meet the written minimum.

### Are the labels trustworthy enough for the reported metrics?

They are trustworthy as an automated smoke benchmark and not yet trustworthy as a human-confirmed gold set.

What is in place:

- `scripts/evaluate_retrieval.py verify` checks that every judgment’s text occurs on the declared parsed page (or HTML element) and in at least one chunk occurrence, and that unknown identifiers occur in none. Labels are not read by `SearchService`.
- The dataset description says judgments were written from parsed text and are verified that way. This audit did not re-run `verify`, so that grounding is designed and previously claimed, not freshly confirmed here.
- `evaluation/review_packet/cases.csv` has 55 rows and a blank `review_status` on every row. `current_expected_answer` is blank on every row because the dataset stores passages, not answers.

What is explicitly unresolved (`needs_human_labeling`, four notes):

1. Bid1 main RFP page 35: prose and table quantities disagree (example given: Tier 3 Student Windows Laptop 10,000 vs 5,000). No quantity question past line 15.01 was labeled.
2. Bid1 pages 54–59 OCR W-9: no questions labeled; OCR text was not human-verified.
3. Bid2 PORFP page 1: prose order of `06/10/2024` vs the table pairing with PROPOSAL DUE. The proposal-due judgment should be confirmed on the PDF.
4. `b1-unanswerable-bid-bond` and `b2-unanswerable-liquidated-damages` were checked by text search only.

Those notes do not invalidate the other judgments. They do mean the headline metrics should be presented as machine-checked, pending a spot review.

### README comparison table

`README.md` describes the four modes and the evaluator command. It does not contain Recall@k / MRR / nDCG numbers. The comparison table is in `VERIFICATION.md`:

| Mode | Recall@5 | MRR@10 | nDCG@10 | hit@5 |
|---|---|---|---|---|
| dense | 0.703 | 0.688 | 0.659 | 0.88 |
| sparse (BM25) | 0.786 | 0.781 | 0.749 | 0.88 |
| hybrid RRF | 0.736 | 0.795 | 0.741 | 0.88 |
| hybrid+rerank depth 10 | 0.742 | 0.751 | 0.725 | 0.88 |
| hybrid+rerank depth 20 | 0.772 | 0.769 | 0.765 | 0.88 |
| hybrid+rerank depth 40 | 0.766 | 0.764 | 0.761 | 0.88 |

That table meets the “at least two configurations” rule in substance (four modes, three rerank depths) and fails the “include the results table in the README” instruction until it is copied there with its caveats. Copying it is documentation, not a retrieval change. This audit did not recompute the cells.

### Reproducibility

`scripts/evaluate_retrieval.py` is the reproduction path. `scripts/local_guard.py::local_only_settings` refuses a non-loopback Qdrant host and refuses cloud credentials, and it does not load `.env`. `run` searches each case with a bid filter and writes Recall@5, MRR@10, and nDCG@10. `evaluation/review_packet/SUMMARY.md` records an earlier local read-only `SearchService` run (loopback `127.0.0.1:6333`, collection `bid_documents__79fea6c89a6d`, rerank depth 20, batch size 1). This audit did not repeat that run and did not confirm the collection is still present.

### Do the known misses change the demonstration?

They change a few questions. They do not erase the search demonstration.

From `evaluation/review_packet/SUMMARY.md`, on the recorded `hybrid_rerank` top 10:

- `b1-award-timing`: no matching candidate.
- `b1-buyer-contact`: first match at rank 10.
- `b2-memory`, `b2-display`, `b2-battery`: first match at ranks 4, 7, and 10. `Dell_Laptop_Specs.pdf` is outranked by the PORFP. A top-5 extraction lookup can miss the spec sheet on display and battery.

`VERIFICATION.md` already states these misses were left untuned. Historical hit@5 is 0.88 across modes, with 0 cross-bid leaks and 0 citation errors on that run, and 3/3 unknown identifiers returned no evidence. For a search demo, disclose the misses. For a later extractor, retrieval depth and query wording have to cover spec-sheet and award-timing fields; retuning the ranker is not the next task.

### Practical way to establish benchmark reliability

Do not block submission on approving all 55 cases. Do this instead, and do not edit `evaluation/assessment_retrieval.json` until a separate, deliberate label change:

1. Run `python scripts/evaluate_retrieval.py verify` against the existing parsed and chunk artifacts. That is the automated evidence check (text on the cited page and in a chunk). It does not write Qdrant.
2. Spot-check about a dozen cases in the source PDFs, not the whole set: the two named unanswerable cases, the Bid2 proposal-due date (note 3), `b1-award-timing`, `b1-buyer-contact`, `b2-memory`, `b2-display`, `b2-battery`, plus one identifier, one affidavit, and one addendum-change case that already matches at rank 1. Page 35 quantities and the W-9 OCR pages have no labeled questions; leave them unlabeled.
3. Use the existing review-packet columns only for those rows. `scripts/build_retrieval_review_packet.py merge` already refuses to overwrite the canonical dataset.
4. Put the historical comparison table into the README, labeled as machine-checked and not human-certified, after verify still passes. Recompute metrics only if a spot check actually changes a label.

## Multi-agent compliance

| Behavior | What actually runs |
|---|---|
| Orchestration and task planning | Not implemented. `src/rfp/agents/` has no planner. |
| Retrieval through the search engine as a tool | The engine is callable (`SearchService.search`, HTTP, CLI). No agent calls it. |
| Specialized extraction agents | Not implemented. No dates, commercial, or product specialists. |
| Addendum reconciliation | Classification and filters only. No “latest addendum wins” logic and no change log. |
| Independent evidence validation | Not implemented. |
| Re-extraction after failed validation, bounded retries | Not implemented. Celery’s `max_retries = 3` covers ingest, chunk, and index jobs only. |
| Parallel execution where appropriate | Not implemented for agents. |
| Structured shared state | Not implemented. |
| Citations and confidence on extracted values | Citations exist on search hits. Confidence scores on bid fields do not. |
| Extraction mode and Q&A mode | Not implemented. |
| Agent traces and operational observability | Search logs timings and a query hash. There is no extraction trace and no token accounting. |

No agent is a connected stub. The package is a placeholder. Treating ingestion or search as “the agent system” would not match section 7.

## Final recommendation

### 1. Overall compliance

The repository is a strong Part A and Part B submission and an incomplete assignment. Roughly the 25% search criterion is in place, with a README-table gap and unreviewed labels. The 25% multi-agent criterion and the 20% extraction criterion are open. The system cannot yet emit a cited JSON record per bid or answer a free-form question.

### 2. Mandatory requirements already complete

- HTML and PDF ingestion, native tables, metadata, and failure handling.
- Structure-aware chunking, local embeddings, Qdrant, incremental indexing.
- Hybrid retrieval, identifier matching, metadata filters, cross-encoder reranking, synonym expansion, and citations.
- Search REST API and CLI.
- A 55-case, both-bid evaluation set and metric functions, with a recorded multi-configuration table outside the README.
- Unit tests for parsing and search. Secrets stay in the environment. The layout is modular. An unseen folder can be parsed and indexed without bid-specific code.
- Optional OCR is already implemented.

### 3. Mandatory requirements still incomplete

- Orchestrator, shared state, extraction agents, addendum reconciliation, validator feedback loop, parallel field extraction, and structured agent messages.
- Extraction mode and Q&A mode.
- The 20 fields, with citations, confidence, null reasons, `addendum_changes`, and `validation`, as one JSON file per bid.
- At least 10 cited Q&A answers and one full extraction trace.
- README results table, agent-framework and prompt documentation, and an architecture diagram that includes the agents.
- A single command that runs extraction on a bid folder.
- Demo video or an equivalent live demo.
- Human confirmation of the evaluation labels (the 15-pair minimum is already met structurally).

### 4. Three highest-priority gaps

1. **Multi-agent orchestration (25%).** No planner, shared state, tool-using retrieval agent, validator retry, parallel specialists, or trace. This is the largest empty scoring block, and section 7 requires extraction to go through it.
2. **Structured extraction of the 20 fields, including addendum updates (20%).** No schema, no JSON, no confidence, no change log. Search quality cannot score this criterion.
3. **The two modes plus the demonstration deliverables (Part C modes, and most of the 10% documentation/demo score).** Q&A mode, 10 cited answers, one extraction trace, the README metric table, and the agent diagram are what a reviewer will look for after the code exists. A web UI is not in this list.

### 5. Single best next implementation task

Build the extraction workflow on top of `SearchService.search`: a shared state object, an orchestrator, a retrieval step, specialist extractors, an addendum-reconciliation step, and a validator that re-runs retrieval and extraction for failed fields with a hard retry cap. Persist one JSON file per bid in the section 8.1 shape, and persist one full trace of that run.

Do not start with more review-packet tooling, reranker tuning, OCR table reconstruction, or a web UI.

### 6. Implementation sequence to a demonstrable submission

1. Add the 20-field Pydantic models (value, sources, confidence, notes, null reason, `addendum_changes`, `validation`) and the shared run state. No model call yet.
2. Call `SearchService` as the only evidence tool. Map each field group to queries and filters, including `doc_types` and `addendum_numbers`. Keep top-k high enough that the known spec-sheet and award-timing misses are visible to the extractor.
3. Add the orchestrator with two entry points, extraction and question answering. Run independent field groups concurrently. Cap validator retries.
4. On a failed field, retrieve again and re-extract only that field. On empty evidence, emit null, confidence, and “Not found in documents”.
5. Log each step (input summary, tool query, output, token counts, latency) and save one extraction trace.
6. Run extraction locally on `data/assessment/Bid1` and `Bid2` and write the two JSON files. Then answer at least 10 questions, including the five in section 7.3, and save the cited log.
7. Document the agent framework choice, prompts, both run commands, the architecture diagram, and the existing retrieval table in the README. State the known retrieval misses and that labels are machine-checked.
8. In parallel, and not as a blocker: `evaluate_retrieval.py verify`, then the small human spot check in the retrieval section. Leave canonical labels unchanged unless a spot check finds a real error.
9. Record the demo last, after extraction and Q&A both run on the two provided folders and the commands are the ones in the README.

### 7. Claims this audit could not verify

- That pytest currently passes (historical: 269 passed in `VERIFICATION.md`; a later 276 passed and 6 skipped was reported and not repeated).
- That `scripts/evaluate_retrieval.py verify` and `run` still succeed, and that the Recall/MRR/nDCG table still matches `VERIFICATION.md`.
- That local Qdrant still holds collection `bid_documents__79fea6c89a6d` with the point counts in `VERIFICATION.md` (Bid1 417, Bid2 116).
- Human correctness of any of the 55 labels. All `review_status` values are blank.
- The production path (R2, PostgreSQL, Qdrant Cloud, Celery). `VERIFICATION.md` already lists that canary as not verified.
- Behavior on an unseen third bid folder.
- End-to-end OCR on a host without Tesseract. The W-9 note in `VERIFICATION.md` is prior evidence, not a new run.

Suggested commit message if this report is added to version control: `docs: audit RFP assignment compliance`.
