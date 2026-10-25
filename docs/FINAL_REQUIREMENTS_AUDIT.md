# Final requirements audit (Phase 12, read-only)

Audit date: 2026-10-25. Repository state: `main` = `origin/main` at `78ff8b0`, working tree clean
before this file was added. No source, label, output or historical artifact was changed.

**Specification.** `Assignment-AI Engineer.pdf`, 7 pages. It is not in the repository. The copy in
the assessment bundle (`Statements (AI Engineer-Emplay Inc)/Assignment-Data-Statements (AI
Engineer-Emplay Inc)/`) and the uploaded `Assignment-AI_Engineer.pdf` have the same SHA-256
(`D656279B…F4424D`). Every page was read. Section numbers below are the PDF's.

**Review boundary.** No human has reviewed any output. "Assistant review" means a model-assisted
comparison with the parsed source text. Retrieval labels and Q&A gold answers are assistant-made
(`evaluation/qa_reviewed.json` says "pending human confirmation"). No real unseen bid was tested.

Statuses: **PASS** (verified against code and evidence), **PARTIAL**, **FAIL**, **UNVERIFIED**,
**OPTIONAL** (bonus).

**Phase A update (2026-10-25).** Rows 2, 35, 47 and 48, the section 5 Q&A matrix, the counts and
sections 11–12 were updated after Phase A added `ask --compare-bids` (`agents/compare.py`) and
fixed the affidavit false abstention (`agents/qa.py` `keep_validated`). The evidence is:

- `tests/test_compare_qa.py` (21 fake-model tests);
- live `gpt-4o-mini` runs in `artifacts/phaseA_qa/`, with masked copies in
  `submission/evidence/phaseA_*.json`;
- a scripted replay of the `qa_v7` failure against the local index.

Every other row is unchanged from the Phase 12 audit.

## 1. Executive summary

The core system is in place and verified. It has:

- ingestion of PDF and HTML;
- a hybrid search engine with filters, reranking, query expansion, citations and a reproducible
  evaluation;
- a bounded multi-agent extraction workflow with shared state, a validator, a critic and traces;
- grounded single-bid Q&A and, since Phase A, cross-bid comparison with per-bid evidence;
- the exact 20-field JSON structure;
- a one-command pipeline;
- 428 offline tests and 6 integration tests, all passing (407 at the Phase 12 audit).

These parts should not be rebuilt.

The gaps that matter for the assignment:

1. **Cross-bid Q&A is supported but incomplete** (7.3, "Compare the warranty requirements of both
   bids"). Phase A added `ask --compare-bids`. In both live runs the findings are cited and true,
   but Bid1 omits the Addendum 1 warranty terms even though that page was retrieved. **PARTIAL**
   (was FAIL).
2. **Addendum reconciliation covers only the Due Date** (7.1 asks to apply "the latest valid change
   to each field"). Conflicting addendum dates are reported rather than resolved to the latest.
   **PARTIAL.**
3. **No demo video** (11). **FAIL** until a video is recorded or a live session is agreed.
4. **Repeated headers and footers are not removed** (5). **FAIL.**
5. **Extraction accuracy is partial** (not a FAIL). By assistant review, 12 of 40 field values are partial,
   including Bid2's Due Date without time or zone (8). The published JSON masks contact emails and
   phones, so `contact_info` cannot be checked from the repository. **PARTIAL.**

Further PARTIAL items:

- observability does not record model-step inputs or accepted outputs;
- confidence is a fixed value per status;
- the affidavits example no longer abstains (Phase A); it names the Mercury Affidavit and leaves
  the Contract Affidavit unaddressed;
- the README has no prompt-design section;
- real unseen-bid behavior is UNVERIFIED.

Requirement-matrix counts (section 2, 70 rows): **PASS 51, PARTIAL 15, FAIL 2, UNVERIFIED 1,
OPTIONAL 1** (OCR, listed in Part A). Section 10 lists the other bonus features separately. The two
FAILs are header and footer removal and the demo video. At the Phase 12 audit the counts were
PASS 50, PARTIAL 15, FAIL 3: row 2 moved from PARTIAL to PASS and row 48 from FAIL to PARTIAL.

## 13. Verification run (2026-10-25)

| Command | Exit | Result |
|---|---|---|
| `pytest -m "not integration" -q` | 0 | 407 passed, 6 deselected |
| `pytest -m integration -q` (with `RFP_QDRANT_INTEGRATION_URL=http://127.0.0.1:6333`, `RFP_MODEL_SMOKE=1`) | 1, then 0 | First run: 6 failed and 5 errors, because local Qdrant was stopped (container exited 10 h earlier). After `docker start rfpintelligenceplatform-qdrant-1`, which only restarts the existing container: 6 passed. Main collection still 533 points; no test collections left |
| `ruff check`, `ruff format --check`, `mypy src/rfp` | 0 | Clean (84 source files) |
| `scripts/build_submission_evidence.py verify` | 0 | 0 problems (10 evidence files) |
| `scripts/build_submission_evidence.py check` | 0 | Copies equal a fresh build; sources unchanged |
| `scripts/audit_submission.py` (stdout only) | 0 | 0 problems: 6 extraction outputs, 18 Q&A examples, README table |
| `scripts/verify_extraction.py --outputs artifacts/extraction_v4` (to a temp file) | 0 | Identical to `artifacts/eval/extraction_v4_check.json` |
| `scripts/evaluate_qa.py verify` | 0 | 14 cases, 0 problems |
| `scripts/evaluate_retrieval.py verify` | 0 | 55 cases, 86 judgments, OK |
| Hardcoding scan of `src/` (bid numbers, agency names, contacts, models, file names, `Bid1`/`Bid2`) | — | 2 matches, both non-logic (section 4) |
| JSON format check of `submission/evidence/Bid*_extraction.json` (scratch script, outside the repo) | — | Section 3 |
| GitHub visibility (Phase 11 manifest check) | — | Public |

No live-model call was made in this audit. Live evidence cited below comes from earlier saved runs
(`gpt-4o-mini`, recorded in each trace). The retrieval rerun that reproduced every metric was done
on 2026-10-24 (`artifacts/eval/retrieval_phase11.json`). It was not repeated here.

## 2. Requirement matrix

"Tests" lists the main test files; all of them passed in the run above.

### Sections 1–4: objective, documents, architecture

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 1 | §1 p1 | Structured JSON per bid, every value backed by a citation | `agents/orchestrator.py`, `agents/schemas.py` `BidExtraction` | `test_extraction.py`, `test_pipeline.py` | `submission/evidence/Bid{1,2}_extraction.json` | Format script: no populated value without sources | PASS | — | — |
| 2 | §1 p1 | Answer free-form questions about **the bids** | `agents/qa.py` `ask`; `agents/compare.py` `compare`; `cli.py` `ask --bid-id` / `--compare-bids` | `test_qa*.py`, `test_compare_qa.py` | `qa_examples.json`, `phaseA_*.json` | Tests; live runs (Phase A) | PASS | Comparison is CLI-only; see row 48 for answer completeness | — |
| 3 | §3 p1 | Each folder processed as one unit | `pipeline.process_bid`, `ingestion/discovery.py` (bid UUID per folder label) | `test_pipeline.py` | `Bid2_process_bid_run_summary.json` | Code and summary | PASS | — | — |
| 4 | §3 p1 | Not hard-coded to the two bids | Generic parsing, retrieval and prompts | `test_pipeline.py` (synthetic bid) | Section 4 | `src/` scan, read in context | PASS | Rules were tuned while looking at these two bids; how well they transfer is unproven (row 66) | — |
| 5 | §4 p2 | Follow or improve on the given flow | `docs/architecture.md` (mermaid diagrams) | — | Same | Read | PASS | — | — |

### Section 5: Part A, ingestion and parsing

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 6 | §5 p2 | Parse HTML bid pages | `ingestion/html.py` (BeautifulSoup) | `test_parsers.py::test_malformed_html_missing_title_and_empty` and others | `artifacts/assessment/*/parsed`; HTML element citations resolve | `audit_submission.py` | PASS | — | — |
| 7 | §5 p2 | Parse PDFs, keeping page structure | `ingestion/pdf.py` (PyMuPDF, pdfplumber) | `test_parsers.py::test_native_pdf_pages_and_tables` | Page citations resolve | Audit | PASS | — | — |
| 8 | §5 p2 | Extract tables correctly (specs and bid info) | `pdf.py` tables, `html.py` tables, `chunking/engine.py` table chunks | `test_chunk_engine.py::test_tables_repeat_headers_preserve_rows…` | Table chunks in parsed artifacts | Tests; code | PARTIAL | Table correctness not reviewed against the documents. OCR pages have no table structure. Bid2's product table is serialized as JSON-like cells, and the model failed to read the quantity from it (`final_accuracy_review.md`) | Review table output |
| 9 | §5 p2 | Remove repeated headers and footers | None (`chunking/normalize.py` has no such step) | None | Bid1 pages repeat the RFP cover header | Code read | **FAIL** | Repeated text enters chunks and embeddings, adds noise and can crowd out retrieval | Phase C |
| 10 | §5 p2 | Fix broken lines and hyphenation | `normalize.py`: joins wrapped lines; removes soft hyphens at line ends | Chunk tests | — | Code read | PARTIAL | Line breaks after an ordinary hyphen (`require-\nment`) are not joined. This is deliberate, to protect part numbers, but it is undocumented as a gap | Phase C (with row 9) |
| 11 | §5 p2 | Normalize whitespace | `normalize.py` | Chunk tests | — | Code | PASS | — | — |
| 12 | §5 p2 | Metadata: bid_id, file_name, doc_type (bid_page, rfp, addendum, specs, affidavit), addendum_number, page_number, document date | `schemas/parsed.py` (`DocumentType` has all five; `document_date`), `ingestion/metadata.py` | `test_parsers.py::test_metadata_classification`, `::test_explicit_dates_and_ambiguity` | Citations in Q&A output carry type and addendum number | Code and tests | PASS | Document date is left empty when ambiguous (by design) | — |
| 13 | §5 p2 | Handle scanned, empty and unreadable files; log them | `pdf.py` page warnings (`ocr_failed…`, `page_has_no_readable_content`, `pdf_page_unreadable`), `parser.py` (timeouts, size limits), `pipeline.py` stage report | `test_parsers.py::test_corrupted_and_encrypted_pdf`, `::test_ocr_failure_partial_and_empty`; `test_pipeline.py` malformed and unsupported files | Live Bid1 runs report 6 OCR page failures, exit 3 | Tests; saved run summaries | PASS | — | — |
| 14 | §5 p2 | OCR support (bonus) | `ingestion/ocr.py` | `test_parsers.py::test_ocr_fallback_and_force` (mocked) | OCR text for Bid1 FINAL pp. 54–59 (container parse) | Saved artifacts | OPTIONAL | Works in Docker; this Windows host has no Tesseract | Optional |

### Section 6: Part B, RAG search engine

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 15 | §6 p2 | Search usable on its own (API or CLI) and as the agents' tool | `cli.py search`; `api/search.py`; `agents/retrieval.py` calls `SearchService.search` | `test_search_cli.py`, `test_search_api.py` | Retrieval steps in traces | Code; traces (36 searches in Bid1 trace) | PASS | — | — |
| 16 | §6.1 p2 | RFP-suited chunking, justified in README | `chunking/engine.py` (heading, list and table aware; 600/800 tokens, 100 overlap) | `test_chunk_engine.py` | README Phase 3 | Read | PASS | Token count is a byte estimate | — |
| 17 | §6.1 p2 | Embeddings | FastEmbed `BAAI/bge-small-en-v1.5` | Integration tests | README §Phase 4 | Integration tests pass | PASS | — | — |
| 18 | §6.1 p2 | Vector store with metadata | Qdrant (`search/collection.py`, `payload.py`) | `test_search_indexing.py`, integration | Local collection, 533 points | Integration tests | PASS | — | — |
| 19 | §6.1 p2 | Incremental indexing | `search/indexing.py` (generations and content hashes) | `test_search_indexing.py::test_incremental_new_generation_supersedes_only_its_source` | `pipeline_check` built by separate per-bid runs | Tests | PASS | — | — |
| 20 | §6.2 p2 | Hybrid dense + BM25, merged (RRF or weighted) | `search/service.py`, weighted RRF k=60 | `test_search_service.py`, integration `test_app_rrf_matches_server_side_rrf_ordering` | `retrieval_evaluation.json` | Tests | PASS | — | — |
| 21 | §6.2 p3 | Metadata filtering (bid, doc type, addendum number) | `SearchFilters` (`bid_ids`, `doc_types`, `addendum_numbers`) | `test_search_service.py::test_metadata_filters` | 0 cross-bid leaks in all 4 modes | Tests; report | PASS | — | — |
| 22 | §6.2 p3 | Re-ranking (cross-encoder or LLM) | Cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` | `test_token_budget.py`, `test_rerank_batching.py`, `test_search_service.py::test_reranker_failure…` | `hybrid_rerank` metrics | Tests; report | PASS | About 1.6 s per query on CPU | — |
| 23 | §6.2 p3 | Query rewriting / expansion (e.g. deadline → due date) | `schemas/search.py` `DEFAULT_SYNONYMS` (`"deadline": ["submission deadline", "due date", "closing date"]`), `plan_query` | `test_search_query.py::test_expansion_is_additive…`, `test_search_service.py::test_synonym_expansion_is_reported` | Query diagnostics in responses | Tests; code | PASS | Rule-based expansion only, with no LLM rewriting; the PDF's "e.g." permits this | — |
| 24 | §6.2 p3 | Citations: source file, page, chunk text | `schemas/search.py` `Citation`, `EvidenceResult.text` | Search and Q&A tests | All citations resolve (audit) | Audit | PASS | — | — |
| 25 | §6.3 p3 | REST API and/or CLI (example endpoints `POST /index`, `GET /search`, `POST /ask`) | CLI: `search`, `index-bid`, `process-bid`, `ask`. API: `GET/POST /search`, `POST /index`, index status | `test_search_api.py` | — | Route scan | PASS | "and/or" is met by the CLI. The API has **no `/ask`**. `POST /index` needs PostgreSQL and Celery and was never run live | Optional: add `POST /ask` |
| 26 | §6.4 p3 | ≥15 question → passage pairs, both bids | `evaluation/assessment_retrieval.json` (tracked) | `test_retrieval_evaluation.py` | 55 cases (50 answerable), 86 judgments | `evaluate_retrieval.py verify` OK | PASS | Labels assistant-made; **not human-reviewed** | Human sample review |
| 27 | §6.4 p3 | Recall@k and MRR or nDCG; ≥2 configurations; table in README | `evaluation/retrieval.py`, `scripts/evaluate_retrieval.py` | Same | `submission/evidence/retrieval_evaluation.json`; README table | Evidence verify: README equals report; 2026-10-24 rerun identical | PASS | Small set; metrics depend on unreviewed labels | — |

### Section 7: Part C, multi-agent system

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 28 | §7 p3 | Agents use the search engine as a tool, not whole documents | `agents/retrieval.py` (bid-scoped search, top passages per field) | `test_extraction.py` | Trace: 36 searches, at most 8 passages shown per field | Trace | PASS | — | — |
| 29 | §7.1 p3 | Orchestrator / planner: plans sub-tasks, routes, decides completion | `agents/orchestrator.py` (field groups, retries, completion) | `test_extraction.py` | Trace `outcome: completed` | Trace | PASS | Fixed plan (field groups), not model-generated. The PDF allows own orchestration | — |
| 30 | §7.1 p3 | Ingestion agent: detect type, parse, clean, metadata, trigger indexing | `pipeline.py` validate, parse, chunk and index stages | `test_pipeline.py` | `Bid2_process_bid_run_summary.json` | Code | PARTIAL | Deterministic stages, not an agent in the orchestrator. The PDF allows merged roles "if you explain why"; the reason (no model needed) appears only in audit docs, not in the README agent section | Phase E (docs) |
| 31 | §7.1 p3 | Retrieval agent: rewrite/expand, search, filters, rerank, cited evidence | `agents/retrieval.py` plus search service | `test_extraction.py` | Trace retrieval steps (queries, modes, doc types, locators) | Trace | PASS | Deterministic, not an LLM agent | — |
| 32 | §7.1 p3 | Extraction agents from retrieved evidence only; specialists allowed | `agents/extractors.py` (identity, logistics, commercial, product, summary) | `test_extraction.py`, `test_semantics.py` | Trace agents | Trace | PASS | — | — |
| 33 | §7.1 p3 | Addendum reconciliation: apply the latest valid change to each field; change log | `agents/reconciliation.py` `reconcile_field` returns nothing unless `name == "Due Date"` | `test_extraction.py::test_addendum_due_date_requires_text…`, `test_pipeline.py` (synthetic addendum) | Bid1 `addendum_changes`: original and revised due date with sources | Code read | PARTIAL | **Due Date only.** Two addenda with different dates yield `conflict=True` and no revised value, rather than the latest valid change | Phase B |
| 34 | §7.1 p4 | Validator / critic: support, format, consistency, hallucination; send failures back; report and confidence | `agents/validator.py`, `semantics.py`, critic step in `orchestrator.py` | `test_extraction.py`, `test_semantics.py`, `test_final_accuracy.py` | Trace: 24 validator and 17 critic steps; retries 0/1/2 | Trace; `verify_extraction.py` | PARTIAL | Confidence is a fixed value per status (0.35, 0.74 or 0.86), not a score. Consistency checks cover a few fields | Phase D (decide) |
| 35 | §7.1 p4 | Q&A / report agent: answers with citations; final JSON and human-readable summary | `agents/qa.py`; JSON from orchestrator; `Bid Summary` field | `test_qa.py`, `test_qa_grounding.py`, `test_ask_collection.py` | `qa_examples.json`, `demo_ask_*.json` | Code; Phase A tests | PARTIAL | Cross-bid questions are now supported (`ask --compare-bids`). Still no human-readable extraction report besides the `Bid Summary` field | Decide whether a report is needed |
| 36 | §7.2 p4 | Framework chosen and explained | Own orchestrator | — | `src/rfp/agents/README.md` | Read | PASS | — | — |
| 37 | §7.2 p4 | Shared state: plan, evidence, drafts, validation, final output | `agents/state.py` `ExtractionState`, `FieldState` | `test_extraction.py` | — | Code read | PARTIAL | Evidence, drafts, validation, retries, changes and tokens are held. **The plan is not stored in state**; field groups live in the orchestrator | Low priority |
| 38 | §7.2 p4 | Feedback loop with a maximum retry count | `orchestrator.py` (`max_retries` default 2, ≤ 5) | `test_extraction.py::test_due_date_retry_uses_the_addendum…` | All traces `max_retry` ≤ 2 | `verify_extraction.py` | PASS | — | — |
| 39 | §7.2 p4 | Parallel agents where possible | `orchestrator.py` `ThreadPoolExecutor` over field groups | Concurrency test in `test_extraction.py` | — | Code and test | PASS | Parallelism was not measured in live traces | — |
| 40 | §7.2 p4 | Structured outputs for agent-to-agent messages | Pydantic models in `agents/schemas.py` | Schema tests | — | Code | PASS | — | — |
| 41 | §7.2 p4 | "Not found in documents"; every value has a citation | Validator; `qa.check_reply` | `test_qa.py::test_unanswerable_question_returns_not_found`, `test_pipeline.py` (invented value stays unknown) | 0 uncited values; live cross-bid abstention | Format script | PASS | Grounding is lexical, not entailment | — |
| 42 | §7.2 p4 | Observability: input, tool calls, output, tokens, latency per step; one example trace | `agents/trace.py` | `test_extraction.py` | `Bid1_extraction_trace.json` (86 steps) | Trace content check | PARTIAL | Recorded: agent, task, field, timestamps, `duration_ms`, search query, mode, top_k, evidence locators, decision, rejection reasons with rejected values, tokens, model. **Not recorded:** the evidence ids given to each model step, accepted draft values (only in the final JSON), cost | Phase E |
| 43 | §7.2 p4 | Handle LLM timeout, bad JSON, empty retrieval without crashing | `agents/llm.py` (`llm_timeout`, `llm_malformed`), orchestrator error paths | `test_extraction.py` (timeout → field note), `test_qa.py::test_malformed_model_output_fails_safely`, `test_pipeline.py::test_invalid_model_output…`, empty search → not found | — | Tests | PASS | — | — |
| 44 | §7.3 p4 | Extraction mode for a bid folder | `extract-bid`, `process-bid` | `test_pipeline.py` | `submission/evidence/` | Saved live runs | PASS | — | — |
| 45 | §7.3 p4 | Q&A mode with citations | `ask`, `ask-samples` | `test_qa*.py` | 13 cited live answers | Audit | PASS | Single bid (row 35) | — |
| 46 | §7.3 p4 | Example: deadline for Bid1 after all addendums | `qa.py` addendum-aware retrieval | `test_qa.py` | `demo_ask_bid1_due_date.json` | Saved live answer | PASS | — | — |
| 47 | §7.3 p4 | Example: affidavits required for the Dell laptop bid | `qa.py` (`keep_validated`), `checklist.py` | `test_qa_grounding.py::test_affidavit_candidates…`; `test_compare_qa.py` affidavit regressions (5 tests) | `submission/evidence/phaseA_ask_bid2_affidavits.json` (live) | Scripted `qa_v7` replay: `not_found` before the fix, answered after; gold score: correct, source hit, item completeness 0.5 | PARTIAL | False abstention fixed. The live answer names the Mercury Affidavit (PORFP p2) and leaves the Contract Affidavit unaddressed (listed in `limitations`) | Item completeness (later phase) |
| 48 | §7.3 p4 | Example: compare warranty requirements of both bids | `agents/compare.py`; `cli.py ask --compare-bids` | `test_compare_qa.py` (21 tests: different, identical, one-sided, both missing, cross-bid and fabricated citations, invalid, duplicate and unknown bids, remote Qdrant, malformed output, timeout) | `phaseA_compare_warranty.json`, `_run1.json`, `_isolation_probe.json`, `_unanswerable.json` (live) | Excerpts checked against local passages; every search carries one bid filter | PARTIAL | Supported and validated. Live findings are true and cited, but Bid1 omits Addendum 1 (one-year Chromebooks, three-year Windows laptops, OEM) although it was retrieved. Run 2 narrows the Bid1 claim to "staff laptops". Relations are lexical | Addendum-aware answers (Phase B) |
| 49 | §7.3 p4 | Example: is a bid bond required, and how much | `qa.py` | — | `qa_v7` (local) | Gold: correct abstention for both bids | PASS | Assistant-made gold | — |
| 50 | §7.3 p4 | Example: what changed in Addendum 2 | `qa.py` addendum lookup | `test_qa.py::test_addendum_number_in_question_scopes_lookup` | `qa_v7` (local) | Gold: correct | PASS | Reports only the due-date change, which gold accepts | — |

### Section 8: Part D, structured extraction

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 51 | §8 p4–5 | The 20 fields, exact names | `agents/schemas.py` `REQUIRED_FIELDS` | `test_pipeline.py` | Both JSONs | Format script: exact names and order | PASS | — | — |
| 52 | §8 p5 | Field accuracy and completeness for both bids | Whole workflow | — | `docs/final_accuracy_review.md` | Assistant review | PARTIAL | Bid1 9 correct, 7 partial; Bid2 11 correct, 5 partial; 0 wrong; 8 nulls judged right (section 7). Not human-reviewed | Phase D |
| 53 | §8 p5 | Due Date includes time and time zone and reflects addenda | Extractor and reconciliation | `test_extraction.py` due-date tests | Bid1 "July 9, 2024 at 2:00 PM CST" with addendum note; Bid2 "06/10/2024" | Format script | PARTIAL | Bid2 lacks the time and zone, which its BidNet page states | Phase D |
| 54 | §8 p5 | Bid Summary of 3–6 sentences, system-generated | Summary extractor | — | Bid1 4 sentences, Bid2 3 | Format script | PASS | Content partial in both (row 52) | — |
| 55 | §8.1 p5 | One JSON per bid | `pipeline.py`, `runner.py` | `test_pipeline.py` | Two files | — | PASS | — | — |
| 56 | §8.1 p5 | Each field: value, sources, confidence (and notes) | `FieldOutput` schema | Schema tests | Field keys = {value, sources, confidence, notes} | Format script | PASS | Sources add `html_element_id` (an extension, not a conflict) | — |
| 57 | §8.1 p5 | Null with a reason when absent | Validator notes | `test_pipeline.py` | 7 nulls with "Not found in documents"; 1 with "Validation failed: …" | Format script | PASS | — | — |
| 58 | §8.1 p5–6 | Confidence score per field | `orchestrator.py` status → confidence | — | Values only 0.35, 0.74, 0.86 | Format script | PARTIAL | Fixed per status; documented as uncalibrated | Phase D (decide) |
| 59 | §8.1 p6 | `addendum_changes` list | `AddendumChange` | Reconciliation tests | Bid1: 1 change with original, revised and sources | Format script | PASS | Only due-date changes ever appear (row 33) | — |
| 60 | §8.1 p6 | `validation` with passed, failed, not_found | `BidExtraction.validation` | — | Bid1 16/1/3 (+ unresolved 1); Bid2 16/0/4 | Recount equals block | PASS | Extra `unresolved` key duplicates `failed` | — |

### Section 9: technical requirements

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 61 | §9 p6 | Python 3.10+, any LLM provider | `pyproject.toml` `requires-python = ">=3.12,<3.13"`; `agents/llm.py` (`openai`, `openai_compatible`) | — | Traces `openai/gpt-4o-mini` | Read | PASS | Evaluators need exactly Python 3.12 | Note in README |
| 62 | §9 p6 | Keys and model names from environment or config; no secrets in code | `LLM_*` from `.env` / environment | Secret scans | No key-shaped strings in `src`, `scripts`, `tests`, `examples` | `rg` scan; `verify_extraction.py` | PASS | — | — |
| 63 | §9 p6 | One command after setup | `python -m rfp.cli process-bid --input <folder> --output <new>` | `test_pipeline.py` | Run summaries (Bid2 exit 0; Bid1 exit 3, host OCR) | Saved live runs | PASS | Setup still needs local Qdrant, `init-models` and keys | — |
| 64 | §9 p6 | Modular structure | `ingestion/`, `chunking/`, `search/`, `agents/`, `api/`, `evaluation/`, `tests/` | — | — | Tree | PASS | — | — |
| 65 | §9 p6 | Unit tests for parsing and search, at minimum | `tests/test_parsers.py`, `test_search_*.py` and others | 428 offline + 6 integration (Phase A) | — | Run | PASS | — | — |
| 66 | §9 p6, §3 p1 | Works on a new, unseen bid folder without code changes | Generic pipeline | `test_pipeline.py` (synthetic, fake model) | No real unseen bid | Section 4 | **UNVERIFIED** | Architecture and synthetic behavior verified; real-bid behavior not | Phase F |

### Section 11: deliverables

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 67 | §11 p7 | README: setup, dependencies, how to run each mode, design decisions (chunking, embedding model, retrieval, agent framework, **prompts**) | `README.md` | — | Phases 3–6, "Final submission", "Submission Evidence…" | Read | PARTIAL | Prompts are in `agents/extractors.py` and `qa.py` but have no design section. The README runs to about 1,250 lines in phase order, which makes the setup and run path hard to find | Phase E |
| 68 | §11 p7 | JSON output per bid with values, citations, confidence | `submission/evidence/Bid{1,2}_extraction.json` (tracked, pushed) | `build_submission_evidence.py verify` | — | Verify; git | PARTIAL | `contact_info` email and phone are masked in the published copy, so this field cannot be evaluated from the repository | Decision (section 9) |
| 69 | §11 p7 | Demo video (5–10 min) or live demo | README script only | — | None | — | **FAIL** | No video; no live session arranged | Record or schedule |
| 70 | §13 p7 | List assumptions and known limitations | README limitation lists; `docs/final_submission_audit.md`; this document | — | Same | Read | PASS | — | — |

Other §11 deliverables passed: source code in Git; architecture diagram; retrieval report with
question set and metrics; Q&A log (≥10); one full trace. They are covered by rows 5, 26–27, 42 and
45, and by section 8.

## 3. JSON-format compliance (§8.1)

Checked on `submission/evidence/Bid1_extraction.json` and `Bid2_extraction.json`. The originals in
`artifacts/extraction_v4/` are identical except for the masked contact details.

| Check | Bid1 | Bid2 |
|---|---|---|
| Top-level keys | `bid_id`, `fields`, `addendum_changes`, `validation` | Same |
| `bid_id` | `Bid1` | `Bid2` |
| 20 field names, exact spelling and order | Yes | Yes |
| Every field has `value`, `sources`, `confidence`, `notes` | Yes | Yes |
| Source keys | `file`, `page`, `html_element_id` (extension) | Same |
| Populated values without a source | 0 | 0 |
| Nulls with a reason | 4 (3 "Not found in documents", 1 "Validation failed: …") | 4 ("Not found in documents") |
| Confidence values | 0.35, 0.74, 0.86 | 0.74, 0.86 |
| Due Date time / zone | Yes / yes (CST); addendum note | **No / no** |
| Due Date in the example's `YYYY-MM-DD HH:MM TZ` shape | No (source wording) | No |
| `validation` | passed 16, failed 1, unresolved 1, not_found 3 (recount matches) | 16, 0, 0, 4 (matches) |
| `addendum_changes` | 1 (Due Date: original, revised, addendum 2, sources) | 0 (no addenda) |
| Bid Summary sentences | 4 | 3 |
| Values over 300 characters | Product (305), Bid Summary (560), Product Specification (1,340) | Payment Terms (306), Bid Summary (406) |
| Citation support on cited pages (`verify_extraction.py`) | 10 verbatim, 4 all value words present, 2 restatements | 13 verbatim, 2 all words, 1 restatement |
| Cited files outside the bid | 0 | 0 |

The PDF says the example's values are illustrative, so source wording for dates does not violate
it. Its field table asks for "None" (Pre Bid Meeting) and "Not required" (Bid Bond) when absent.
The output uses `null` with "Not found in documents", which matches the §8.1 example.
Schema-valid output is not proof of correctness; see section 7.

## 4. Unseen-bid hardcoding and generalization

1. **Generic architecture (code inspection): verified.** `process-bid` takes any folder and an
   optional label (`pipeline.process_bid`, `ingestion/discovery.py`), and derives the bid UUID from
   the label. A scan of `src/` for both bids' identifiers, agency names, contact names, product
   models, file names and the literals `Bid1`/`Bid2` found two matches. Neither is answer logic:
   - `agents/semantics.py:148`: the error message text "A model number needs a code such as 5550 or
     UM-40", an example inside a message;
   - `agents/semantics.py:215`: `porfp` in a list of identifier labels next to rfp, rfq, ifb and
     itb (a generic procurement abbreviation).

   The field-meaning and identifier rules (`semantics.py`) and the Bid Number rule were written
   after observing these two bids. They are generic in form, but their effect on other bids is
   untested. New-bid indexing does not rebuild other bids (`IndexingService`, incremental
   generations). A stale-points check rejects leftover evidence for the same bid. Every search
   carries a bid filter, and `retrieval.py` refuses unscoped searches (`bid_scope`).
2. **Synthetic end-to-end (tests): verified.** `tests/test_pipeline.py` and
   `tests/test_ask_collection.py` run the real parsers, chunker and indexer on a fictional bid
   (Harborview County Water Authority, RFP + addendum + HTML listing), using an in-memory Qdrant,
   fake embeddings and a deterministic fake model that answers only from shown passages. The tests
   cover:
   - schema, values and pages;
   - the addendum due-date change;
   - an invented value staying unknown;
   - another bid in the same collection;
   - a removed document (stale points);
   - unsupported and malformed files;
   - malformed model output;
   - a remote Qdrant;
   - no overwrite.

   All pass. Different file names, missing fields and an addendum are exercised.
3. **Real unseen bid with a live model: UNVERIFIED.** No such run exists. An evaluator can test it
   independently with
   `python -m rfp.cli process-bid --input <new-bid-folder> --output <new-folder> --collection <name>`,
   then `ask --bid-id <label> --collection <name> --question "…"`. Expect partial fields similar to
   Bid1/Bid2, and Due Date reconciliation only.

## 5. Q&A supported-task matrix (§7.3)

| Task | CLI/API support | Correct bid(s) retrieved | Citations valid | Evidence | Outcome |
|---|---|---|---|---|---|
| Submission deadline for Bid1 after all addendums | Yes (`ask`, CLI only) | Yes (bid filter) | Yes, verbatim, Addendum 2 p. 1 | `submission/evidence/demo_ask_bid1_due_date.json`; `qa_examples.json` #1 | Correct |
| Affidavits required for the Dell laptop bid | Yes | Yes (PORFP p2 ranked first) | Yes, verbatim, PORFP p2 | `submission/evidence/phaseA_ask_bid2_affidavits.json` (live, Phase A) | **Correct but incomplete**: Mercury Affidavit; Contract Affidavit listed as unaddressed. Was a false abstention in `qa_v7` |
| Compare warranty requirements of both bids | Yes (`ask --compare-bids`, CLI only; no `/ask`) | Yes: separate bid-filtered searches per bid | Yes: each bid's citations come from its own passages (FINAL p4/p6; PORFP p3) | `submission/evidence/phaseA_compare_warranty.json` and `_run1.json` (live) | **Partial**: true, cited findings; Bid1 omits Addendum 1 warranty terms |
| Bid bond required, and amount | Yes | Yes | n/a (abstained) | `qa_v7` (local) | Correct abstention, both bids (assistant gold) |
| What changed in Addendum 2 | Yes | Yes (addendum scope) | Yes | `qa_v7` (local) | Correct per gold (the due-date change) |

Further checks:

- **At least 10 real cited examples.** `submission/evidence/qa_examples.json` has 18 live
  `gpt-4o-mini` questions: 13 cited answers (20 citations, all resolving) and 5 abstentions. It is
  tracked and pushed.
- **Disclosed outcomes.** Assistant review: 11 correct, 2 partial (#4, #11), 5 correct abstentions.
  The 5 false abstentions on the 14-case gold set (9/14) are disclosed in `docs/qa_examples.md` and
  the README.
- **Cross-bid isolation.** Questions about the other bid abstained (`demo_ask_bid2_abstention.json`;
  `qa_examples.json` #9 and #18). In comparison mode the Mercury Affidavit probe returned "Not found
  in documents" for Bid1 while Bid2 answered from its own files
  (`phaseA_compare_isolation_probe.json`). An unanswerable comparison abstained for both bids
  (`phaseA_compare_unanswerable.json`).
- **Masking.** Examples 2 and 11 have contact emails masked, so their excerpts are no longer
  verbatim.
- **Published log and section 7.3.** The published evidence contains three of the five examples:
  the deadline, affidavits and warranty comparison (Phase A). The bid bond and Addendum 2 runs exist
  only in local, ignored artifacts.

## 6. Addendum reconciliation coverage

| Capability | Supported | Tested | Evidence |
|---|---|---|---|
| Detect a due-date change stated in addendum text (cue phrases) | Yes | Yes (`test_extraction.py`, synthetic addendum in `test_pipeline.py`) | Bid1: 27-JUN-2024 14:00:00 → July 9, 2024 at 2:00 PM CST, Addendum 2 |
| Ignore an addendum number without change text | Yes | Yes (`test_addendum_due_date_requires_text_and_ignores_number_alone`) | — |
| Record original value, revised value, addendum number and sources | Yes (Due Date) | Yes | Bid1 `addendum_changes` |
| Several addenda with different dates → latest wins | **No**: recorded as a conflict with no revised value | Conflict path in code | — |
| Changes to any other field (scope, quantities, specs, warranty, documents, dates other than due) | **No** (`reconcile_field` returns early) | No | Bid1 Addendum 1 contains specification and warranty content (Q&A #7, `qa_v7` warranty case), which the extraction does not reconcile |
| Q&A answers about addenda | Yes (addendum-scoped retrieval) | Yes | `qa_examples.json` #7; `qa_v7` |

## 7. Retrieval and extraction evaluation evidence

**Retrieval.** 55 cases (50 scored), 86 judgments, labels assistant-made. Results from
`retrieval_evaluation.json`, which equals the README table; the 2026-10-24 rerun was identical:

| Mode | Recall@5 | MRR@10 | nDCG@10 | hit@5 |
|---|---|---|---|---|
| dense | 0.703 | 0.688 | 0.659 | 0.88 |
| sparse | 0.786 | 0.781 | 0.749 | 0.88 |
| hybrid | 0.736 | 0.795 | 0.741 | 0.88 |
| hybrid + rerank | 0.772 | 0.769 | 0.765 | 0.88 |

**Extraction** (assistant review of `extraction_v4`; C = correct, P = partial, N = null judged
right):

| Field | Bid1 | Bid2 | | Field | Bid1 | Bid2 |
|---|---|---|---|---|---|---|
| Bid Number | C | C (alias interpretation) | | MFG for Registration | P | C |
| Title | C | C | | Contract or Cooperative | C | C |
| Due Date | C | **P** (no time or zone) | | Model_no | N | C |
| Bid Submission Type | C | C | | Part_no | N | C |
| Term of Bid | C | N | | Product | C | **P** (no quantity) |
| Pre Bid Meeting | P | N | | contact_info | P | C |
| Installation | C | N | | company_name | C | C |
| Bid Bond Requirement | N | N | | Bid Summary | P | P |
| Delivery Date | P | C | | Product Specification | P | P |
| Payment Terms | N (unresolved) | P | | Additional Documentation | P | C |

Totals: Bid1 9 C, 7 P, 4 N; Bid2 11 C, 5 P, 4 N; 0 wrong. `extraction_v4` is the chosen run after
several attempts, and a same-code repeat differed in some fields (`docs/live_validation.md`).
Later `process-bid` runs scored Bid1 8/8 and Bid2 10/6 (correct/partial).

## 8. Deliverable availability and Git tracking

| Deliverable | Local | Tracked | After clone | Required content | Sensitive content | Approval |
|---|---|---|---|---|---|---|
| Source code | Yes | Yes | Yes | Yes | No secrets (scans) | — |
| README, setup, design decisions | Yes | Yes | Yes | Partial (no prompt section) | Quotes contact details in places | Owner decision |
| Architecture diagram (`docs/architecture.md`) | Yes | Yes | Yes | Yes | No | — |
| JSON per bid (`submission/evidence/Bid*_extraction.json`) | Yes | Yes | Yes | Yes, except masked `contact_info` | Bid excerpts (public solicitations) | Masking decision |
| Retrieval report, question set, metrics | Yes | Yes (`retrieval_evaluation.json`, `evaluation/assessment_retrieval.json`, README) | Yes | Yes | Labels quote contact details | Owner decision |
| ≥10 cited Q&A examples (`qa_examples.json`) | Yes | Yes | Yes | Yes | 2 examples masked | — |
| One full extraction trace | Yes | Yes | Yes | Yes (gaps in row 42) | No passage text | — |
| Demo video or live demo | **No** | — | — | — | — | Record |

`git check-ignore` confirms that `artifacts/`, `data/assessment/*`, `.env`, `.cache/` and `.venv/`
remain ignored. `git status` was clean before this file was added.

## 9. Security and privacy risks

- **The repository is public.** Pushed commits contain `evaluation/review_packet/packet.json`
  (3.4 MB) and `evidence.csv` (1.1 MB). Together they hold the full text of retrieved bid chunks and
  several email addresses, including vendor addresses from the documents. Several docs and label
  files also quote the procurement contacts' emails and phones. This was disclosed in
  `docs/submission_artifacts_manifest.md`; no action has been taken. Removing it from history would
  need a force-push.
- **Masked copy versus public originals.** The published evidence masks contact details that are
  already public elsewhere in the same repository. That inconsistency reduces evaluability
  (row 68) without fully protecting the data.
- **No secrets** in tracked files or outputs (scans; `.env` ignored). Local-only Qdrant guards
  (`agents/safety.assert_local_qdrant`) refuse remote URLs and URLs with credentials.
- **Raw bid documents** are not tracked.

## 10. Optional features (§12)

| Feature | Status |
|---|---|
| Web UI | OPTIONAL: not implemented |
| OCR | OPTIONAL: implemented; verified in Docker artifacts; host lacks Tesseract |
| Dedicated bid-comparison agent | OPTIONAL: partly covered. `ask --compare-bids` (Phase A) compares bids per question as fixed pipeline steps, not as an autonomous agent; there is no field-by-field comparison of the extraction JSON. The **required** cross-bid example question is row 48 (PARTIAL) |
| Go / no-go recommendation | OPTIONAL: not implemented |
| Semantic caching; cost and latency tracking | OPTIONAL: tokens and per-step latency in traces; no query cache; no currency cost |
| Docker deployment and CI | OPTIONAL: `Dockerfile`, `compose.yaml` (only Qdrant used in validation); no CI pipeline |

## 11. Prioritized gaps

1. **Cross-bid Q&A completeness** (row 48, PARTIAL since Phase A). `agents/compare.py` is in
   place and tested. In the live warranty comparison, Bid1's Addendum 1 page was retrieved but not
   used, so answers need addendum awareness, which is the next item.
2. **Addendum reconciliation scope** (row 33). `agents/reconciliation.py` `reconcile_field` (early
   return for fields other than Due Date) and `reconcile_due_date` (conflict path). This affects
   extraction accuracy (20% weight) and the §7.1 agent role.
3. **Demo video** (row 69). No code change.
4. **Header and footer removal** (row 9). `chunking/normalize.py` `normalize`, or a page-level step
   in `ingestion/pdf.py`. Requires re-chunking and re-indexing, so plan it in its own phase with a
   retrieval regression check.
5. **Extraction partials** (rows 52–53). Bid2 Due Date time and zone (`extractors.py` logistics
   prompt, `semantics.py` Due Date rule); Product quantity from serialized tables; documentation
   lists; summaries.
6. **Published `contact_info` masking** (row 68). Owner decision: publish the originals (the same
   data is already public in tracked files) or accept that the field is unverifiable.
7. **Observability** (row 42). `agents/trace.py` and `orchestrator.py`: record the evidence ids
   given to each model step and the accepted draft values.
8. **Affidavit list completeness** (row 47). The false abstention is fixed (Phase A,
   `agents/qa.py` `keep_validated`). The Contract Affidavit is still left unaddressed by the
   model; `checklist.py` reports it in `limitations`.
9. **README** (rows 30, 67). Prompt design section, merged-role rationale, a short quick-start at the
   top, and the Python 3.12 pin.
10. **Confidence semantics** (rows 34, 58). Decide whether to keep fixed status values (documented)
    or derive a documented score.
11. **Shared state plan** (row 37), **`POST /ask`** (row 25), **hyphenated line breaks** (row 10):
    low priority.
12. **Real unseen bid** (row 66). Obtain one and run it live; record the results as evidence.

## 12. Recommended fix order

1. **Phase A (done 2026-10-25):** cross-bid Q&A and the affidavit fix. The comparison, affidavit,
   isolation and unanswerable live runs are published. Live bid bond and Addendum 2 examples are
   still only in local artifacts.
2. **Phase B:** addendum reconciliation beyond the Due Date, with "latest valid change wins" and a
   change log per field. Tests on the synthetic bid, plus a live rerun into a new output folder.
3. **Phase C:** repeated header and footer removal, with a retrieval regression on a separate
   collection before touching the main index.
4. **Phase D:** extraction partials (Due Date time and zone, product quantity, lists), and a
   decision on confidence semantics.
5. **Phase E:** observability additions and README restructuring (quick start, prompts, merged
   roles).
6. **Phase F:** real unseen-bid validation, the demo video, and the owner's privacy decisions.

Already satisfied, do not rebuild: ingestion and parsing core; search engine (hybrid, filters,
reranking, expansion, citations, incremental indexing); retrieval evaluation and its README table;
orchestrator, specialists, validator, critic and bounded retries; error handling; the 20-field JSON
structure; `process-bid`; local-only safety and bid isolation; the test suite; cross-bid
comparison (`agents/compare.py`, Phase A).
