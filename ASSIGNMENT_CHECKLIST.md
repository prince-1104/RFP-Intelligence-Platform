# Assignment requirement checklist

Updated 2026-10-24 after the final submission audit (Phase 11); Q&A rows updated 2026-10-25
after Phase A (cross-bid comparison, affidavit fix), and addendum rows after Phase B (addendum
reconciliation). This is the short form of
`docs/final_submission_audit.md`, which gives the implementation, evidence, verification method and
remaining limitation for each requirement of `Assignment-AI Engineer.pdf`. It supersedes the status
columns of `ASSIGNMENT_COMPLIANCE_AUDIT.md` (state before Phases 5–11).

**No human review has taken place.** Accuracy verdicts, Q&A gold answers and retrieval labels are
automated or assistant-assisted. Passing tests do not establish extraction accuracy, and the
synthetic unseen bid does not establish generalization to real unseen bids.

Statuses: **Verified** (met and checked in this audit or by a re-checked saved report), **Partial**
(gap named), **Blocked** (cannot run or ship as is), **Not implemented**.

| Requirement | Status | Evidence / gap |
|---|---|---|
| A. Parse HTML and PDF with page structure | Verified | `artifacts/assessment/*/parsed`; all citations resolve (`scripts/audit_submission.py`) |
| A. Extract tables | Partial | Native PDF and HTML tables; scanned pages have no table structure; not human-checked |
| A. Clean text | Partial | Wrapped lines, soft hyphens and whitespace are handled. Repeated PDF headers, footers and page numbers are removed before chunking (Phase C, `chunking/furniture.py`; 20 tests, retrieval regression on a separate collection). Still open: ordinary-hyphen line breaks are not joined, and OCR pages get no furniture removal |
| A. Metadata (bid, file, doc type, addendum, page, date) | Verified | Citation fields; `document_date` where detected |
| A. Graceful failure for scanned, empty or corrupt files | Verified | `tests/test_pipeline.py`; live Bid1 OCR failures reported (exit 3) |
| B. RFP-aware chunking with overlap, justified | Verified | README Phase 3 |
| B. Embeddings, vector store, metadata | Verified | Local Qdrant `bid_documents__79fea6c89a6d` (533 points, untouched) |
| B. Incremental indexing | Verified | `tests/test_search_indexing.py`; per-bid `process-bid` runs into `pipeline_check` |
| B. Hybrid search, metadata filters, reranking, query expansion | Verified | Retrieval rerun `artifacts/eval/retrieval_phase11.json` equals `retrieval_phase7.json`; 0 cross-bid leaks |
| B. Citations (file, page or element, text) | Verified | Audit: every extraction and Q&A citation resolves. Q&A example 2 lists page 1 first; the text is on page 2 |
| B. Search CLI / API | Verified (CLI); API partial | CLI live, including `ask --collection` (new). API offline tests only; no `POST /ask` |
| B. ≥15 query-to-passage pairs, both bids | Verified | 55 cases; `evaluate_retrieval.py verify` OK. Labels not human-approved |
| B. Metrics for ≥2 configurations, table in README | Verified | 4 modes; README table matches the saved report (audit) |
| C. Orchestrator, retrieval, extraction specialists, shared state | Verified | 6 live traces (8 agents each), e.g. `artifacts/extraction_v4/traces/extraction_Bid1.json` |
| C. Ingestion agent | Partial | Deterministic `process-bid` stages rather than an agent |
| C. Addendum reconciliation | Partial | Phase B: Due Date plus 12 requirement fields, in extraction and Q&A (`agents/revisions.py`; 24 fake-model tests). Bid1: Due Date replaced by Addendum 2 (27-JUN-2024 14:00:00 to July 9, 2024 2:00 PM CST), and nine Addendum 1 clarifications recorded with quotes (`submission/evidence/phaseB_Bid1_extraction.json`). Gap: supersession and conflicts tested offline only (no such case in the bids); issue dates not in the index; identity fields not reconciled |
| C. Validator, bounded feedback loop, confidence | Partial | Retries ≤ 2 everywhere. Confidence is fixed per status (0.86 / 0.74 / 0.35), not calibrated |
| C. Parallel execution | Verified (offline) | Concurrency test |
| C. Structured outputs, guardrails, observability, error handling | Verified | Pydantic messages; no uncited value; traces with tokens and latency; malformed-output tests |
| C. Extraction mode | Verified | `extract-bid`, `process-bid` live |
| C. Q&A mode and section 7.3 examples | Partial | Deadline, bid bond and Addendum 2 examples correct. Bid2 affidavits: false abstention fixed (Phase A); live answer names the Mercury Affidavit (PORFP p2) and leaves the Contract Affidavit unaddressed. "Compare the warranty requirements of both bids": `ask --compare-bids` (Phase A; 21 fake-model tests, live runs in `submission/evidence/phaseA_compare_*.json`). Phase B: Bid1 warranty and the warranty comparison now cite Addendum 1 (`phaseB_ask_bid1_warranty.json`, `phaseB_compare_warranty.json`). Still partial: Bid1 etching and Bid2 due date abstain after non-verbatim quotes |
| D. 20 fields, JSON per bid, null with reason, `addendum_changes`, `validation` | Verified (structure) | Audit of 6 outputs |
| D. Due Date with time and zone after addenda | Partial | Bid1 correct; Bid2 lacks time and zone |
| D. Extraction accuracy | Partial | Model-assisted: Bid1 9/7/0/0/4, Bid2 11/5/0/0/4 (correct/partial/wrong/missed/null ok) in `extraction_v4`; not human-reviewed |
| Tech. Python 3.10+, keys from environment | Verified | Python 3.12; secret scans clean |
| Tech. One command after setup | Verified | `process-bid` (Bid1 exit 3 for host OCR, Bid2 exit 0) |
| Tech. Modular structure, unit tests | Verified | 472 offline + 6 integration tests pass (Phase C); ruff, format, mypy clean |
| Tech. Unseen bid without code changes | Partial | Synthetic fixture with a fake model only; no real unseen bid tested |
| Deliverable. Source in Git | Partial | Remote `origin` exists; Phase 11 changes not committed |
| Deliverable. README with design decisions; architecture diagram | Verified | `README.md`; `docs/architecture.md` |
| Deliverable. JSON per bid; Q&A log; full trace; retrieval report file | Blocked (pending approval) | Originals are in git-ignored `artifacts/`. Contact-masked copies are prepared in `submission/evidence/` (`docs/submission_artifacts_manifest.md`), not yet committed |
| Deliverable. Retrieval results in README | Verified | Table matches the saved report |
| Deliverable. Demo video | Not implemented | Not recorded. Script and commands in README "Demo sequence" |
| Bonus. OCR | Verified in Docker; blocked on host | No Tesseract on this Windows host; setup steps in README |
| Bonus. Token and latency tracking | Partial | In traces; no currency cost, no query cache |
| Bonus. Docker | Partial | `compose.yaml`; only Qdrant used in validation |
| Bonus. Web UI, go/no-go, CI | Not implemented | Optional |
| Bonus. Bid comparison | Partial | CLI question-level comparison (`ask --compare-bids`); no side-by-side field comparison of the extraction JSON |
