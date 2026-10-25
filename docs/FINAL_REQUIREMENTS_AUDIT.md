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

**Phase B update (2026-10-25).** Rows 33, 48, 59 and 65, gaps 1–2, the counts, sections 4–6 and
11–12, and a Phase B verification table (end of section 13) were updated after Phase B added
requirement-level addendum reconciliation (`agents/revisions.py`). It is used by extraction
(`reconciliation.reconcile_field`, orchestrator addendum check), single-bid Q&A and comparison.
The evidence is:

- `tests/test_revisions.py` (24 fake-model tests);
- the deterministic report `scripts/report_addenda.py`;
- live `gpt-4o-mini` runs in `artifacts/phaseB_addenda/`, with masked copies in
  `submission/evidence/phaseB_*.json`.

The Bid1/Bid2 addenda contain clarifications and one due-date replacement only. The conflict and
supersession paths are therefore tested offline only.

**Phase C update (2026-10-25).** Rows 9, 10 and 65, gap 4, the counts, sections 1, 7, 11 and 12,
and a Phase C verification table (end of section 13) were updated after Phase C added repeated
PDF header and footer removal (`chunking/furniture.py`, called from `chunking/engine.py`). The
evidence is:

- `tests/test_page_furniture.py` (20 tests: synthetic PDFs plus checks on the real Bid1/Bid2
  parsed artifacts);
- the deterministic report `scripts/report_page_furniture.py`;
- a separate local collection `phasec_clean`, with a retrieval regression and the Phase B
  reconciliation report run against it, in `artifacts/phaseC_pdf_cleanup/`.

The main collection `bid_documents` was not modified and still holds the pre-cleanup chunks.

## 1. Executive summary

The core system is in place and verified. It has:

- ingestion of PDF and HTML;
- a hybrid search engine with filters, reranking, query expansion, citations and a reproducible
  evaluation;
- a bounded multi-agent extraction workflow with shared state, a validator, a critic and traces;
- grounded single-bid Q&A and, since Phase A, cross-bid comparison with per-bid evidence;
- the exact 20-field JSON structure;
- a one-command pipeline;
- 472 offline tests and 6 integration tests, all passing (452 after Phase B, 428 after Phase A,
  407 at the Phase 12 audit).

These parts should not be rebuilt.

The gaps that matter for the assignment:

1. **Cross-bid Q&A** (7.3, "Compare the warranty requirements of both bids"). Phase A added
   `ask --compare-bids`; Phase B made answers addendum-aware. In the Phase B live comparison, Bid1
   cites Addendum 1 for the one-year Chromebook and three-year Windows laptop minimums and the OEM
   requirement, and Bid2 cites its PORFP. **PASS** (was PARTIAL). Aspect relations remain lexical.
2. **Addendum reconciliation** (7.1, "the latest valid change to each field"). Phase B covers the
   Due Date and 12 other fields, with these rules:
   - change types: replace, add, remove, clarify and unknown;
   - a change wins only when chronology is explicit;
   - competing changes that cannot be ordered stay unresolved.

   Seven fields are not reconciled: Bid Number, Title, Model_no, Part_no, contact_info,
   company_name and Bid Summary. The real addenda have no replacement other than the due date, so
   ordering and conflicts are tested offline only. **PARTIAL** (narrower than before).
3. **No demo video** (11). **FAIL** until a video is recorded or a live session is agreed.
4. **Repeated headers and footers** (5). Phase C removes repeated PDF page furniture before
   chunking, keeps one copy per document, and never removes text with requirement wording. On the
   real bids it removed 109 blocks in 9 groups and kept 7 groups. In a separate collection, every
   retrieval metric improved except sparse MRR@10 (−0.007), and the Phase B reconciliation
   decisions were unchanged. **PASS** (was FAIL). The main collection was not rebuilt.
5. **Extraction accuracy is partial** (not a FAIL). The Phase D live rerun (one run per bid,
   agent-verified against the source pages, not human-reviewed) has Bid1 at 10 complete, 5
   incomplete and 5 correctly not found. Bid2 has 14 complete, 1 incomplete, 4 correctly not found
   and 1 incorrect (the summary). Bid2's Due Date is still date-only, because the listing's time
   and zone lose their label in HTML parsing. The published JSON predates Phase D and masks contact
   emails and phones. **PARTIAL.**
6. **Confidence** (58). Since Phase D, it is a documented evidence-quality heuristic
   (`agents/confidence.py`), not a fixed value per status. It is not calibrated. **PASS** (was
   PARTIAL).

Further PARTIAL items:

- observability does not record model-step inputs or accepted outputs;
- the confidence heuristic cannot see errors the validator misses (row 34);
- the affidavits example no longer abstains (Phase A); it names the Mercury Affidavit and leaves
  the Contract Affidavit unaddressed;
- the README has no prompt-design section;
- real unseen-bid behavior is UNVERIFIED.

Requirement-matrix counts (section 2, 70 rows): **PASS 54, PARTIAL 13, FAIL 1, UNVERIFIED 1,
OPTIONAL 1** (OCR, listed in Part A). Section 10 lists the other bonus features separately. The
remaining FAIL is the demo video.

How the counts changed:

- **Phase 12 audit:** PASS 50, PARTIAL 15, FAIL 3.
- **Phase A:** row 2 moved from PARTIAL to PASS, and row 48 from FAIL to PARTIAL.
- **Phase B:** row 48 moved from PARTIAL to PASS. Row 33 stays PARTIAL with a narrower gap.
- **Phase C:** row 9 moved from FAIL to PASS.
- **Phase D:** row 58 moved from PARTIAL to PASS. Rows 34, 52 and 53 stay PARTIAL with narrower
  gaps.

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

**Phase B verification (2026-10-25).** Local Qdrant was used read-only and no index was rebuilt.

| Command | Exit | Result |
|---|---|---|
| `pytest -q` (full suite) | 0 | 458 passed: 452 non-integration (24 new in `test_revisions.py`) and the 6 integration tests |
| `pytest -m integration -q` (local Qdrant, `RFP_MODEL_SMOKE=1`) | 0 | 6 passed |
| `ruff check`, `ruff format --check`, `mypy src` | 0 | Clean (86 source files) |
| `scripts/report_addenda.py --bid-id Bid1 --bid-id Bid2 …` | 0 | Bid1: 6 fields amended, 6 unchanged, Due Date replaced by Addendum 2; Bid2: all unchanged |
| Quote check of both reports against parsed pages (scratch script) | — | 103 of 103 quotes verbatim on the cited page |
| `extract-bid --bid-id Bid1/Bid2 --llm-mode live` into `artifacts/phaseB_addenda/extraction` | 0 | Bid1 16/1/3 and Bid2 16/0/4, the same as `extraction_v4`; Bid1 has 10 change records, all quotes verified |
| `scripts/verify_extraction.py --outputs artifacts/phaseB_addenda/extraction` | 0 | No problems; no secret leaks |
| Live `ask` / `ask --compare-bids` (6 runs) | 0 | See `artifacts/phaseB_addenda/README.md`; gold scorer: `bid1-warranty`, `bid1-final-deadline`, `bid2-warranty` correct |
| `evaluate_retrieval.py verify`, `evaluate_qa.py verify` | 0 | OK; retrieval code and labels unchanged |
| `build_submission_evidence.py build`, `verify`, `check` | 0 | 19 files, 0 problems; earlier copies unchanged |
| `scripts/audit_submission.py` | 0 | 0 problems |

**Phase C verification (2026-10-25).** The main collection was read only. Re-indexing went to a new
local collection, `phasec_clean__79fea6c89a6d`, through the loopback-guarded `index-folder
--collection`. Outputs are in `artifacts/phaseC_pdf_cleanup/` (git-ignored).

| Command | Exit | Result |
|---|---|---|
| `pytest -m "not integration" -q` | 0 | 472 passed (20 new in `test_page_furniture.py`) |
| `pytest -m integration -q` (local Qdrant, `RFP_MODEL_SMOKE=1`) | 0 | 6 passed; no test collection left |
| `ruff check`, `ruff format --check`, `mypy src` | 0 | Clean (87 source files) |
| `scripts/report_page_furniture.py` (v2 parsed artifacts) | 0 | 7 PDFs: 16 candidates, 9 removed (109 blocks), 7 retained |
| `chunk-folder` with `page_furniture=repeated_v1` | 0 | 501 chunks (Bid1 386, Bid2 115) versus 533 (417, 116). Only FINAL (331 → 300) and PORFP (29 → 28) lost chunks |
| Control re-chunk with `page_furniture=keep` | 0 | Same generation ids and chunk hashes as `artifacts/assessment/v3` |
| Removed-occurrence check (scratch script) | — | All 109 removed occurrences are present on their page in the old chunks and absent in the new ones |
| `evaluate_retrieval.py verify --chunks artifacts/phaseC_pdf_cleanup/chunks` | 0 | 55 cases, 86 judgments still on their declared pages; labels unchanged |
| `index-folder --collection phasec_clean` (run twice) | 0 | 9 generations, 501 points; second run 9 unchanged, still 501 points |
| Collection contents check (scratch script) | — | Only the 9 Bid1/Bid2 documents; every PDF point has a page |
| `evaluate_retrieval.py run` (original, then `--collection phasec_clean`) | 0 | See section 7; 0 citation errors and 0 cross-bid leaks in both |
| `report_addenda.py … --collection phasec_clean` (Phase B questions) | 0 | Field statuses and the Due Date decision identical to Phase B; 104 of 104 PDF quotes found on the cited original page (5 table rows matched cell by cell) |
| Live `ask` / `ask --compare-bids --collection phasec_clean` (4 runs) | 0 | Bid1 warranty, Bid1 final deadline (July 9, 2024, 2:00 PM CST), Bid2 warranty and the warranty comparison answered; 9 of 9 PDF citations on the original page |
| `evaluate_retrieval.py verify`, `evaluate_qa.py verify` | 0 | OK |
| `build_submission_evidence.py verify`, `check`; `audit_submission.py` | 0 | 19 files, 0 problems; published evidence unchanged |
| Qdrant fingerprint before and after (scratch script) | — | `bid_documents` (533), `pipeline_check` (503) and `rfp_smoke` (6): point ids and payload hashes unchanged |

**Phase D verification (2026-10-25).** Extraction read the main local collection only; no index
was built or changed. Outputs are in `artifacts/phaseD_extraction/`.

| Command | Exit | Result |
|---|---|---|
| `extract-bid --llm-mode live` (Bid1, Bid2) on the Phase C code, into `baseline_rerun/` | 0 | Field baseline; classified in `baseline_labels.json` and `baseline_classification.json` |
| `extract-bid --llm-mode live` after the changes (iterations `after` to `after_r4`; `after_r4` is the final run) | 0 | Classified in `after_labels.json`; field-by-field comparison in `comparison.json` |
| Citation check of both runs (scratch script; `verify_baseline_rerun.json`, `verify_after_r4.json`) | — | No cited file outside the bid; every source contains value tokens; no secret leaks |
| `pytest -q` (full suite, integration URL set) | 1 | 503 passed, 1 failed: `test_app_rrf_matches_server_side_rrf_ordering`. It passed on its own and with its file (6 passed). It compares RRF orderings on a live server, and no search code changed in Phase D |
| `pytest -m integration -q` (local Qdrant, `RFP_MODEL_SMOKE=1`) | 0 | 6 passed; 498 offline tests deselected (25 new in `test_extraction_completeness.py`) |
| `ruff check`, `ruff format --check`, `mypy src` | 0 | Clean (88 source files) |
| `evaluate_retrieval.py verify`, `evaluate_qa.py verify` | 0 | OK; labels and gold answers unchanged |
| `build_submission_evidence.py verify`, `check`; `audit_submission.py` | 0 | 19 files, 0 problems; published evidence unchanged |
| Qdrant fingerprint against the Phase C snapshots (scratch script) | — | All four local collections: point counts and payload hashes unchanged |

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
| 9 | §5 p2 | Remove repeated headers and footers | `chunking/furniture.py` (detection), `chunking/engine.py` (skips detected blocks); on by default via `Settings.chunking.page_furniture="repeated_v1"` | `test_page_furniture.py` (20 tests) | `artifacts/phaseC_pdf_cleanup/` (report, cleaned chunks, `phasec_clean` collection, retrieval comparison) | Removed occurrences checked against old and new chunks; labels and citations checked against original pages | PASS | PDF only, from native text blocks; OCR pages are not processed. One copy per document is kept, except bare page counters. Running headers with requirement wording are kept (Bid1 "FEDERAL REQUIREMENTS (EDGAR)"). Page height is not stored, so the edge band comes from the text extent. The main collection was not rebuilt | Rebuild the main index when the owner agrees |
| 10 | §5 p2 | Fix broken lines and hyphenation | `normalize.py`: joins wrapped lines; removes soft hyphens at line ends | Chunk tests | — | Code read | PARTIAL | Line breaks after an ordinary hyphen (`require-\nment`) are not joined. This is deliberate, to protect part numbers, but it is undocumented as a gap | Later phase (not part of Phase C) |
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
| 33 | §7.1 p3 | Addendum reconciliation: apply the latest valid change to each field; change log | `agents/revisions.py` (requirement histories: replace/add/remove/clarify/unknown; applied, superseded, conflict or not_applied). `reconciliation.reconcile_field`: Due Date rule plus `FIELD_TERMS` for 12 fields. Orchestrator addendum check search per accepted field | `test_revisions.py` (24), `test_extraction.py` due-date tests, `test_pipeline.py` (synthetic addendum) | `phaseB_Bid1_extraction.json`: 10 change records (Due Date replace from Addendum 2; 9 Addendum 1 clarifications) with verified quotes; `phaseB_reconciliation_Bid1.json` | Tests; quotes checked on parsed pages; live run | PARTIAL | Seven fields are not reconciled (identifiers, contact, company, summary). A change wins only on explicit supersession wording or distinct issue dates, and the index stores no issue dates, so unordered changes stay a conflict. Ordering and conflicts are tested offline only; the real addenda have none | Reconcile identity fields if needed; store document dates at the next re-index |
| 34 | §7.1 p4 | Validator / critic: support, format, consistency, hallucination; send failures back; report and confidence | `agents/validator.py`, `semantics.py`, critic step in `orchestrator.py` | `test_extraction.py`, `test_semantics.py`, `test_final_accuracy.py` | Trace: 24 validator and 17 critic steps; retries 0/1/2 | Trace; `verify_extraction.py` | PARTIAL | Phase D added checks: due-date time, zone and question-deadline; delivery versus ordering dates; documents (questions, examples, conditions, non-documents); warranty terms; heading-only values; whole identifiers. Confidence is now derived from these checks (row 58). The validator still cannot detect a wrong relation between individually cited facts (the Phase D Bid2 summary) or an omission it has no rule for (Bid2 Payment Terms) | Later phase |
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
| 48 | §7.3 p4 | Example: compare warranty requirements of both bids | `agents/compare.py`; `cli.py ask --compare-bids` | `test_compare_qa.py` (21 tests: different, identical, one-sided, both missing, cross-bid and fabricated citations, invalid, duplicate and unknown bids, remote Qdrant, malformed output, timeout) | `phaseA_compare_warranty.json`, `_run1.json`, `_isolation_probe.json`, `_unanswerable.json` (live) | Excerpts checked against local passages; every search carries one bid filter | PASS | Phase B: `phaseB_compare_warranty.json` (live). Bid1 cites Addendum 1 p1–p2: one-year Chromebooks, three-year Windows laptops, OEM. The first attempt omitted OEM; the revision check named it and the correction cited it. Bid2 cites PORFP p3. The Phase A runs omitted Addendum 1. Relations remain lexical, and Bid1's original tablet and monitor tiers are not mentioned | — |
| 49 | §7.3 p4 | Example: is a bid bond required, and how much | `qa.py` | — | `qa_v7` (local) | Gold: correct abstention for both bids | PASS | Assistant-made gold | — |
| 50 | §7.3 p4 | Example: what changed in Addendum 2 | `qa.py` addendum lookup | `test_qa.py::test_addendum_number_in_question_scopes_lookup` | `qa_v7` (local) | Gold: correct | PASS | Reports only the due-date change, which gold accepts | — |

### Section 8: Part D, structured extraction

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 51 | §8 p4–5 | The 20 fields, exact names | `agents/schemas.py` `REQUIRED_FIELDS` | `test_pipeline.py` | Both JSONs | Format script: exact names and order | PASS | — | — |
| 52 | §8 p5 | Field accuracy and completeness for both bids | Whole workflow | — | `docs/final_accuracy_review.md` | Assistant review; Phase D live rerun checked against source pages (`artifacts/phaseD_extraction/comparison.json`) | PARTIAL | Phase D (one live run per bid, agent-verified, not human-reviewed): Bid1 10 complete, 5 incomplete, 5 correctly not found (baseline rerun 8/7/4, 1 incorrect). Bid2 14 complete, 1 incomplete, 4 correctly not found, 1 incorrect (baseline 12/3/4, 1 incorrect). The incorrect field is Bid2's summary. Live output varies between runs. Published JSON is still `extraction_v4` | Later phase |
| 53 | §8 p5 | Due Date includes time and time zone and reflects addenda | Extractor and reconciliation | `test_extraction.py` due-date tests | Bid1 "July 9, 2024 at 2:00 PM CST" with addendum note; Bid2 "06/10/2024" | Format script | PARTIAL | Phase D: the validator rejects a due date that drops a time or zone printed with it, one that adds a zone not printed with it, and the questions deadline (tests in `test_extraction_completeness.py`). Bid2 is still "06/10/2024": its PDF prints the date only, and the listing's time and zone become an unlabelled paragraph in HTML parsing, so attaching them would be inference. A parser fix is still needed | Later phase (HTML label parsing) |
| 54 | §8 p5 | Bid Summary of 3–6 sentences, system-generated | Summary extractor | — | Bid1 4 sentences, Bid2 3 | Format script | PASS | Content partial in both (row 52) | — |
| 55 | §8.1 p5 | One JSON per bid | `pipeline.py`, `runner.py` | `test_pipeline.py` | Two files | — | PASS | — | — |
| 56 | §8.1 p5 | Each field: value, sources, confidence (and notes) | `FieldOutput` schema | Schema tests | Field keys = {value, sources, confidence, notes} | Format script | PASS | Sources add `html_element_id` (an extension, not a conflict) | — |
| 57 | §8.1 p5 | Null with a reason when absent | Validator notes | `test_pipeline.py` | 7 nulls with "Not found in documents"; 1 with "Validation failed: …" | Format script | PASS | — | — |
| 58 | §8.1 p5–6 | Confidence score per field | `agents/confidence.py` (support kind, agreeing sources, retries, incomplete, conflict); signals in the trace | `test_extraction_completeness.py` (score responds to evidence; unresolved and incomplete never high) | Phase D live: accepted 0.5–0.9, not found 0.4–0.5, unresolved 0.15 | Tests; `comparison.json` | PASS | A heuristic, documented in `agents/README.md`; not calibrated. It scores evidence quality, so errors the validator misses still score medium (Bid2 summary 0.6). Published JSON predates it and still shows 0.35/0.74/0.86 | — |
| 59 | §8.1 p6 | `addendum_changes` list | `AddendumChange`; Phase B added defaulted keys `change_type`, `status`, `addendum_date`, `orderable` and `quote` (old records still load) | Reconciliation tests, `test_revisions.py` | Published v4 Bid1: 1 change. Phase B Bid1: 10 changes, each with sources | Format script; quotes verified on pages | PASS | The published `Bid1_extraction.json` is still the v4 run; the Phase B run is published separately | — |
| 60 | §8.1 p6 | `validation` with passed, failed, not_found | `BidExtraction.validation` | — | Bid1 16/1/3 (+ unresolved 1); Bid2 16/0/4 | Recount equals block | PASS | Extra `unresolved` key duplicates `failed` | — |

### Section 9: technical requirements

| # | § / page | Requirement | Implementation | Tests | Evidence | Verification | Status | Gap and impact | Next action |
|---|---|---|---|---|---|---|---|---|---|
| 61 | §9 p6 | Python 3.10+, any LLM provider | `pyproject.toml` `requires-python = ">=3.12,<3.13"`; `agents/llm.py` (`openai`, `openai_compatible`) | — | Traces `openai/gpt-4o-mini` | Read | PASS | Evaluators need exactly Python 3.12 | Note in README |
| 62 | §9 p6 | Keys and model names from environment or config; no secrets in code | `LLM_*` from `.env` / environment | Secret scans | No key-shaped strings in `src`, `scripts`, `tests`, `examples` | `rg` scan; `verify_extraction.py` | PASS | — | — |
| 63 | §9 p6 | One command after setup | `python -m rfp.cli process-bid --input <folder> --output <new>` | `test_pipeline.py` | Run summaries (Bid2 exit 0; Bid1 exit 3, host OCR) | Saved live runs | PASS | Setup still needs local Qdrant, `init-models` and keys | — |
| 64 | §9 p6 | Modular structure | `ingestion/`, `chunking/`, `search/`, `agents/`, `api/`, `evaluation/`, `tests/` | — | — | Tree | PASS | — | — |
| 65 | §9 p6 | Unit tests for parsing and search, at minimum | `tests/test_parsers.py`, `test_search_*.py` and others | 498 offline + 6 integration (Phase D) | — | Run | PASS | — | — |
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
   Bid1/Bid2. Expect addendum reconciliation for the Due Date and the 12 fields in `FIELD_TERMS`
   (Phase B). The subject terms are generic procurement vocabulary, but no real unseen bid has
   tested them.

## 5. Q&A supported-task matrix (§7.3)

| Task | CLI/API support | Correct bid(s) retrieved | Citations valid | Evidence | Outcome |
|---|---|---|---|---|---|
| Submission deadline for Bid1 after all addendums | Yes (`ask`, CLI only) | Yes (bid filter) | Yes, verbatim, Addendum 2 p. 1 | `submission/evidence/demo_ask_bid1_due_date.json`; `qa_examples.json` #1 | Correct |
| Affidavits required for the Dell laptop bid | Yes | Yes (PORFP p2 ranked first) | Yes, verbatim, PORFP p2 | `submission/evidence/phaseA_ask_bid2_affidavits.json` (live, Phase A) | **Correct but incomplete**: Mercury Affidavit; Contract Affidavit listed as unaddressed. Was a false abstention in `qa_v7` |
| Compare warranty requirements of both bids | Yes (`ask --compare-bids`, CLI only; no `/ask`) | Yes: separate bid-filtered searches per bid | Yes: each bid's citations come from its own passages (Phase B: Addendum 1 p1–p2 for Bid1; PORFP p3 for Bid2) | `submission/evidence/phaseB_compare_warranty.json` (live, Phase B); Phase A runs `phaseA_compare_warranty*.json` | **Correct**: Bid1 cites the Addendum 1 minimums and OEM terms. The Phase A runs omitted them |
| Warranty for Bid1 (single bid, addendum-aware) | Yes | Yes | Yes, Addendum 1 p1 | `submission/evidence/phaseB_ask_bid1_warranty.json` (live) | Correct per gold `bid1-warranty`: one-year Chromebooks, three-year Windows laptops, OEM. Original tablet and desktop/monitor tiers not mentioned |
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
| Record original value, revised value, addendum number and sources | Yes (Due Date and 12 other fields; plus change type, status, `orderable`, verbatim quote) | Yes (`test_revisions.py`) | `phaseB_Bid1_extraction.json` (10 records) |
| Several addenda → latest wins | Yes, only when an addendum names the one it supersedes, or both carry distinct issue dates. Otherwise every competing change is kept as `conflict` and the field is rejected, leaving it unresolved | Yes (ordered, metadata-ordered, unordered, missing metadata) | Offline only: no such case in Bid1/Bid2 |
| Changes to other fields (submission type, term, pre-bid, installation, bond, delivery, payment, documents, registration, cooperative, product quantity, specifications/warranty) | Yes. Replace and remove reject a value that ignores them; add and clarify are recorded and noted on the field | Yes | Bid1: warranty minimums, OEM, USB 3.1, etching, autopilot, delivery scheduling, Form 1295, samples, PDF version (all Addendum 1 clarifications) |
| Not reconciled | Bid Number, Title, Model_no, Part_no, contact_info, company_name, Bid Summary | — | — |
| Q&A answers about addenda | Yes. Addendum-scoped retrieval adds passages with material revisions beyond the evidence cap. Revisions are listed in the prompt, and an answer that ignores one is corrected once and then carries a limitation. A claim that names an addendum must cite it | Yes | `phaseB_ask_bid1_warranty.json`, `phaseB_compare_warranty.json`; `qa_examples.json` #7 |

## 7. Retrieval and extraction evaluation evidence

**Retrieval.** 55 cases (50 scored), 86 judgments, labels assistant-made. Results from
`retrieval_evaluation.json`, which equals the README table; the 2026-10-24 rerun was identical:

| Mode | Recall@5 | MRR@10 | nDCG@10 | hit@5 |
|---|---|---|---|---|
| dense | 0.703 | 0.688 | 0.659 | 0.88 |
| sparse | 0.786 | 0.781 | 0.749 | 0.88 |
| hybrid | 0.736 | 0.795 | 0.741 | 0.88 |
| hybrid + rerank | 0.772 | 0.769 | 0.765 | 0.88 |

**Phase C regression** (same dataset, code, labels and metric definitions; original collection
versus `phasec_clean`, both run on 2026-10-25; 50 scored cases each, 0 errors, 0 citation errors,
0 cross-bid leaks, 3/3 unknown identifiers handled):

| Mode | Recall@5 | MRR@10 | nDCG@10 | hit@5 |
|---|---|---|---|---|
| dense | 0.703 → 0.712 | 0.688 → 0.705 | 0.659 → 0.662 | 0.88 → 0.90 |
| sparse | 0.786 → 0.812 | 0.781 → 0.775 | 0.749 → 0.760 | 0.88 → 0.88 |
| hybrid | 0.736 → 0.763 | 0.775 → 0.792 | 0.730 → 0.748 | 0.88 → 0.88 |
| hybrid + rerank | 0.772 → 0.810 | 0.769 → 0.772 | 0.765 → 0.793 | 0.88 → 0.88 |

The original-collection rerun reproduced the published table except plain hybrid MRR@10 and
nDCG@10 (0.795 and 0.741 published). Four cases swap near-tied results; the same run repeated gives
the same values, and the collection is unchanged. Per case, 6 to 12 cases change in each mode,
mostly upwards (identifier, addendum_change and specification). The regressions were inspected.
Removing Addendum 1's header and footer moves its chunk boundaries, so the chunk holding a judged
passage starts at a different question:

- `b1-warranty-minimum` Recall@5 is 0.75 → 0.5 in dense and hybrid, but 0.25 → 0.5 with reranking;
- `b1-etching` sparse Recall@5 is 1.0 → 0.67.

Elsewhere, a changed PORFP chunk enters the dense top 10 for `b2-drug-free`, and the
`b1-buyer-contact` cover chunk moves from rank 10 to 11 in hybrid. No judged passage was removed:
all 86 judgments are still in the cleaned chunks. Warranty and due-date cases keep their first
relevant rank in every mode except `b1-deadline-extension` dense (3 → 4) and
`b1-questions-deadline` sparse (2 → 3). The gains are measured on 50 assistant-labelled cases and
are small; they are evidence of no harm rather than a proven improvement.

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

**Phase D extraction rerun.** One live run per bid before the changes (`baseline_rerun`) and one
after (`after_r4`). Both were classified by the agent against the cited source pages, not by a
human. Classes: complete, incomplete, incorrect, and correctly not found.

| Bid | Baseline | After |
|---|---|---|
| Bid1 | 8 complete, 7 incomplete, 4 not found, 1 incorrect (Delivery Date) | 10 complete, 5 incomplete, 5 not found |
| Bid2 | 12 complete, 3 incomplete, 4 not found, 1 incorrect (Documents) | 14 complete, 1 incomplete, 4 not found, 1 incorrect (Bid Summary) |

- **Improved:** Bid1 Delivery Date (a quote-due sentence is now rejected, so the field is null),
  MFG, Product (all tiers with quantities) and Summary. Bid2 Documents (now only the required
  affidavit and certificate) and Product (both items with quantities).
- **Regressed:** Bid1 Installation became a less specific restatement. The Bid2 summary links two
  supported facts wrongly: it says invoices include the Mercury Affidavit and the warranty
  certificate.
- **Cost:** Bid1 input tokens rose from about 39k to 55k.
- **Variation:** earlier iterations of the same code gave different values for some fields.

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
| Dedicated bid-comparison agent | OPTIONAL: partly covered. `ask --compare-bids` (Phase A) compares bids per question as fixed pipeline steps, not as an autonomous agent; there is no field-by-field comparison of the extraction JSON. The **required** cross-bid example question is row 48 (PASS since Phase B) |
| Go / no-go recommendation | OPTIONAL: not implemented |
| Semantic caching; cost and latency tracking | OPTIONAL: tokens and per-step latency in traces; no query cache; no currency cost |
| Docker deployment and CI | OPTIONAL: `Dockerfile`, `compose.yaml` (only Qdrant used in validation); no CI pipeline |

## 11. Prioritized gaps

1. **Cross-bid Q&A completeness** (row 48): resolved in Phase B. Bid1's comparison finding now
   cites Addendum 1. Aspect relations are still lexical.
2. **Addendum reconciliation scope** (row 33, PARTIAL). Phase B generalized it. Remaining:
   - identity fields are not reconciled;
   - issue dates are not stored in the index, so ordering relies on explicit wording;
   - ordering and conflicts are tested offline only.
3. **Demo video** (row 69). No code change.
4. **Header and footer removal** (row 9): resolved in Phase C for new chunking. Remaining:
   - the main collection still holds the pre-cleanup chunks; rebuilding it is the owner's call;
   - OCR pages are not processed;
   - a `CHUNKING__*` environment override builds a `ChunkConfig` with the legacy default `keep`.
5. **Extraction partials** (rows 52–53). Phase D fixed Product quantities, documentation lists,
   delivery dates and summary coverage in the live rerun. Remaining:
   - Bid2 Due Date time and zone (HTML label parsing);
   - Bid1 Pre Bid zone, Installation detail, reseller certifications, and the EPCNT cooperative;
   - Bid1 Product Specification (7 of 10 device tiers);
   - Bid2 summary relation error;
   - run-to-run variation.
6. **Published `contact_info` masking** (row 68). Owner decision: publish the originals (the same
   data is already public in tracked files) or accept that the field is unverifiable.
7. **Observability** (row 42). `agents/trace.py` and `orchestrator.py`: record the evidence ids
   given to each model step and the accepted draft values.
8. **Affidavit list completeness** (row 47). The false abstention is fixed (Phase A,
   `agents/qa.py` `keep_validated`). The Contract Affidavit is still left unaddressed by the
   model; `checklist.py` reports it in `limitations`.
9. **README** (rows 30, 67). Prompt design section, merged-role rationale, a short quick-start at the
   top, and the Python 3.12 pin.
10. **Confidence semantics** (rows 34, 58): resolved in Phase D with a documented,
    uncalibrated evidence heuristic. Calibration would need human-reviewed labels.
11. **Shared state plan** (row 37), **`POST /ask`** (row 25), **hyphenated line breaks** (row 10):
    low priority.
12. **Real unseen bid** (row 66). Obtain one and run it live; record the results as evidence.

## 12. Recommended fix order

1. **Phase A (done 2026-10-25):** cross-bid Q&A and the affidavit fix. The comparison, affidavit,
   isolation and unanswerable live runs are published. Live bid bond and Addendum 2 examples are
   still only in local artifacts.
2. **Phase B (done 2026-10-25):** requirement-level addendum reconciliation in extraction,
   single-bid Q&A and comparison (`agents/revisions.py`). It comes with 24 offline tests, a
   deterministic report and live runs in `artifacts/phaseB_addenda/`. The warranty answer,
   comparison, extraction and report are published as `submission/evidence/phaseB_*.json`.
3. **Phase C (done 2026-10-25):** repeated PDF header and footer removal
   (`chunking/furniture.py`), on by default for new chunking. It comes with 20 tests, a
   deterministic report, a separate `phasec_clean` collection and a retrieval regression. The main
   index was not touched, and no new evidence was published.
4. **Phase D (done 2026-10-25):** extraction completeness checks (`agents/semantics.py`,
   `validator.py`), list-field prompts and citation completion (`extractors.py`,
   `orchestrator.py`), and summaries built from validated fields. It also added the evidence-based
   confidence heuristic (`agents/confidence.py`) and 25 tests. Live reruns are in
   `artifacts/phaseD_extraction/`. No evidence was published.
5. **Phase E:** observability additions and README restructuring (quick start, prompts, merged
   roles).
6. **Phase F:** real unseen-bid validation, the demo video, and the owner's privacy decisions.

Already satisfied, do not rebuild: ingestion and parsing core; search engine (hybrid, filters,
reranking, expansion, citations, incremental indexing); retrieval evaluation and its README table;
orchestrator, specialists, validator, critic and bounded retries; error handling; the 20-field JSON
structure; `process-bid`; local-only safety and bid isolation; the test suite; cross-bid
comparison (`agents/compare.py`, Phase A); addendum reconciliation (`agents/revisions.py`,
Phase B); page furniture removal (`chunking/furniture.py`, Phase C).
