# Assignment requirement checklist

Updated 2026-10-24 after Phase 10 (end-to-end `process-bid` command and unseen-bid tests). This
file supersedes the status columns of `ASSIGNMENT_COMPLIANCE_AUDIT.md`, which records the state
before Phases 5–10.
Evidence paths are relative to the repository root.

Live evidence is in `docs/live_validation.md`. The per-field review is in
`docs/final_accuracy_review.md`. Accuracy verdicts are model-assisted or automated; no human
review has taken place.

Statuses:

- **Verified:** complete and verified, by a live run on Bid1 and Bid2 or by offline tests where
  noted.
- **Not verified:** implemented but not verified.
- **Missing.**
- **Blocked:** blocked by configuration or data.

| Requirement | Status | Evidence / gap |
|---|---|---|
| A. Parse HTML and PDF with page and structure | Verified | `artifacts/assessment/*/parsed`; `tests/test_parsers.py` |
| A. Extract tables | Verified | Native PDF and HTML tables. Scanned pages (Bid1 FINAL pp. 54–59) are OCR text without table structure |
| A. Clean text (whitespace, wrapped lines, soft hyphens) | Verified | `chunking/normalize.py` and chunk tests |
| A. Remove repeated page headers and footers | Missing | Headers such as "Request For Proposal 168884 JA-207652 …" stay in the text |
| A. Metadata (bid, file, doc type, addendum, page, date) | Verified | Citations in every live output carry them |
| A. Graceful failure for scanned, empty or corrupt files | Verified (offline) | Parser tests |
| B. RFP-aware chunking with overlap | Verified | Chunk tests; README Phase 3 |
| B. Embeddings + vector store + metadata | Verified | Local collection `bid_documents__79fea6c89a6d`, 533 points, used by every live run |
| B. Incremental indexing | Verified (offline) | `tests/test_search_indexing.py`; not re-run this phase (no re-indexing) |
| B. Hybrid search, filtering, reranking, query expansion | Verified | `artifacts/eval/retrieval_phase8.json`: identical to Phase 7, 0 cross-bid leaks in 4 modes |
| B. Citations (file, page or element, text) | Verified | Every Q&A citation is a verbatim span with a locator (`artifacts/qa_examples.json`). Chunks that cross pages now cite the page holding the value (Phase 8) |
| B. Search CLI / API | CLI verified; API not verified | CLI used live. API covered by offline tests only |
| B. ≥15 query-to-passage pairs, both bids | Verified | 55 cases (`evaluation/assessment_retrieval.json`); `verify` OK |
| B. Metrics for ≥2 configurations, table in the README | Verified | 4 modes; table in the README retrieval section |
| B. Human approval of retrieval labels | Not verified | Automated grounding check, plus a model-assisted spot check of 16 cases (two per kind) that found no incorrect labels. `needs_human_labeling` lists open items |
| C. Orchestrator, specialists, retrieval tool use, shared state | Verified | `artifacts/extraction_v4/traces/*.json` (agents: retrieval, identity, logistics, commercial, product, summary, validator, critic) |
| C. Addendum reconciliation | Verified | Bid1 due date: 27-JUN-2024 14:00:00 → July 9, 2024 2:00 PM CST (Addendum 2); the note is written from the change record. Bid2 has no addenda |
| C. Validator with bounded re-extraction | Verified, with gaps | Retries ≤ 2 in every trace. Field-meaning rules (`agents/semantics.py`) reject values of the wrong kind. Phase 9 adds omission checks (Term renewals, Product quantity) and the Bid Number rule. Completeness is only checked for these cases |
| C. Parallel agent execution | Verified (offline) | Group concurrency test in `tests/test_extraction.py` |
| C. "Not found in documents" and citation-only values | Verified | Extraction null notes; 5 correct Q&A abstentions |
| C. Observability: steps, tool calls, tokens, latency in one trace | Verified | Extraction and Q&A traces; question text is hashed |
| C. Error handling (timeout, bad JSON, empty retrieval) | Verified | Offline tests. A malformed live reply was handled without a crash |
| C. Extraction mode and Q&A mode | Verified | `extract-bid`, `ask`, `ask-samples` run live |
| D. 20 fields per bid, one JSON per bid, null with reason, `addendum_changes`, `validation` | Verified (structure) | `artifacts/extraction_v4/Bid1.json`, `Bid2.json`; `artifacts/eval/extraction_v4_check.json` |
| D. Extraction accuracy | Not verified (issues recorded) | Model-assisted review of `extraction_v4` in `docs/final_accuracy_review.md`: Bid1 9 correct / 7 partial / 0 wrong / 0 missed / 4 null ok; Bid2 11 / 5 / 0 / 0 / 4. Phase 8 re-scored with the same criteria: 7/8/1/1/3 and 10/5/0/1/4. Phase 10 `process-bid` runs: Bid1 9/7/1/0/3, then 8/8/0/0/4 after the Model_no fix; Bid2 10/6/0/0/4. No improvement over Phase 9; partial fields and run-to-run variation remain. Pending human confirmation |
| D. Per-field confidence | Not verified | Fixed value per status (0.86 / 0.74 / 0.35); documented as not calibrated |
| Tech. Secrets from the environment, never logged | Verified | `scripts/verify_extraction.py` secret scan; README configuration section |
| Tech. One command after setup | Verified | `python -m rfp.cli process-bid --input <folder> --output <new folder>` runs validate, parse, chunk, index (loopback Qdrant), extract and verify, writes `run_summary.json`, and exits 0 / 3 (partial) / 1 (stage failed) / 2 (refused). Live on Bid1 (exit 3: no host Tesseract for 6 scanned pages) and Bid2 (exit 0): `artifacts/pipeline_v1/`, `artifacts/pipeline_v2/`. Setup still needs local Qdrant, `init-models` and LLM keys |
| Tech. Unit tests for parsing and search | Verified | 402 offline tests pass (`pytest -m "not integration"`) |
| Tech. Unseen bid folder without code changes | Verified offline only | `tests/test_pipeline.py` runs `process-bid` on a synthetic bid (fictional water authority, RFP + addendum + listing page) with real parsers, chunker and indexer and a deterministic evidence-bound fake model. No real unseen bid was available, so live accuracy on an unseen bid is not measured. No bid-specific literals in `src/` |
| Deliverable. README, architecture diagram | Verified | README; `docs/architecture.md` (end-to-end, search, extraction, Q&A diagrams) |
| Deliverable. JSON output per bid | Verified | `artifacts/extraction_v4/` (Phase 7 output kept in `artifacts/extraction_v2/`, Phase 8 in `artifacts/extraction_v3/`) |
| Deliverable. Retrieval evaluation report | Verified | README table; `artifacts/eval/retrieval_phase8.json` |
| Deliverable. ≥10 cited Q&A examples | Verified | `artifacts/qa_examples.json`: 11 correct, 2 partial, 5 correct abstentions (live) |
| Deliverable. One full agent trace | Verified | `artifacts/extraction_v4/traces/extraction_Bid1.json` |
| Deliverable. Demo video | Missing | Not recorded. A reproducible demo sequence is in the README |
| Bonus. OCR | Verified | Bid1 FINAL pp. 54–59 |
| Bonus. Token usage per run | Verified | Trace `token_usage`; no currency cost is computed |
| Bonus. Docker | Not verified | `compose.yaml`; only the Qdrant service was used this phase |
| Bonus. Web UI, bid comparison, go/no-go, semantic cache, CI | Missing | Optional |
