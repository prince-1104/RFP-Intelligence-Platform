# Final submission audit

Audit date: 2026-10-24. Source of requirements: `Assignment-AI Engineer.pdf` (7 pages, in the
assessment bundle `Statements (AI Engineer-Emplay Inc)/Assignment-Data-Statements (AI Engineer-Emplay
Inc)/`, outside this repository). Section numbers below are the assignment's.

Statuses:

- **Verified:** the requirement is met, and the stated check was run during this audit or is
  reproduced by a saved report the audit re-checked.
- **Partial:** part of the requirement is met; the gap is named.
- **Blocked:** implemented, but it cannot run in the current environment.
- **Not implemented.**

"Verified" never means "human-reviewed". **No human has reviewed any output.** Accuracy
verdicts (`docs/final_accuracy_review.md`), Q&A review notes (`artifacts/qa_examples.json`), the Q&A
gold answers (`evaluation/qa_reviewed.json`, "assistant-assisted source review; pending human
confirmation") and retrieval relevance labels (`evaluation/assessment_retrieval.json`) are
automated or assistant-assisted. A passing test suite does not establish extraction accuracy. The
synthetic unseen-bid fixture does not establish generalization to real unseen bids.

## How the evidence was checked

| Check | Command | Result (2026-10-24) |
|---|---|---|
| Artifact audit (fields, citations resolve, traces, Q&A excerpts, README table) | `scripts/audit_submission.py --out artifacts/eval/submission_audit.json` | 0 problems in 6 extraction outputs, 18 Q&A examples and the retrieval table; 1 imprecise citation page (below) |
| Extraction check reproduces saved report | `scripts/verify_extraction.py --outputs artifacts/extraction_v4` | Identical to `artifacts/eval/extraction_v4_check.json` |
| Q&A gold labels on their declared pages | `scripts/evaluate_qa.py verify` | 14 cases, 0 problems |
| Q&A score reproduces saved report | `scripts/evaluate_qa.py score --samples artifacts/qa_v7/qa_samples.json` | Identical to `artifacts/eval/qa_v7.json` |
| Retrieval labels grounded | `scripts/evaluate_retrieval.py verify` | 55 cases, 86 judgments, OK |
| Retrieval metrics reproduce | `scripts/evaluate_retrieval.py run --modes dense,sparse,hybrid,hybrid_rerank --out artifacts/eval/retrieval_phase11.json` | Every quality metric equal to `retrieval_phase7.json`; only latency differs |
| Offline tests | `pytest -m "not integration"` | 407 passed |
| Integration tests (disposable `rfp_it_*` collections, local Qdrant) | `pytest -m integration` with `RFP_QDRANT_INTEGRATION_URL`, `RFP_MODEL_SMOKE=1` | 6 passed; no test collection left behind |
| Lint, format, types | `ruff check`, `ruff format --check`, `mypy src/rfp` | Clean |
| Secret scan of new outputs | `extraction_check.leaks` over `artifacts/final_demo`, new reports, README | No leaks |
| Live demo commands | `ask` (Bid1 due date; Bid2 via `--collection pipeline_check`; Bid2 cross-bid probe), refusals | Outputs in `artifacts/final_demo/`; model `gpt-4o-mini` |

Nothing was re-indexed into the main collection `bid_documents__79fea6c89a6d` (533 points), and no
label or evaluation question was changed. All outputs of this audit went to new paths.

## Section 5: Part A, ingestion and parsing

| Requirement | Implementation | Evidence | Verification | Status | Remaining limitation |
|---|---|---|---|---|---|
| Parse HTML bid pages and PDF files, keeping page structure | `ingestion/` (BeautifulSoup, PyMuPDF, pdfplumber) | `artifacts/assessment/<Bid>/parsed`; `process-bid` parse stage in `artifacts/pipeline_v1/*/run_summary.json` | Parser tests; every citation resolves to a parsed page or HTML element (audit) | Verified | — |
| Extract tables correctly | Native PDF and HTML table extraction | Parsed artifacts; table citations in search results | Parser tests | Partial | Table correctness not human-checked. Scanned pages (Bid1 FINAL pp. 54–59) are OCR text without table structure |
| Clean text: remove repeated headers/footers, fix broken lines and hyphenation, normalize whitespace | `chunking/normalize.py` | Chunk tests | Offline tests | Partial | **Repeated page headers and footers are not removed** (e.g. "Request For Proposal 168884 JA-207652 …" stays in the text) |
| Metadata: bid_id, file_name, doc_type, addendum_number, page_number, document date where available | `schemas/parsed.py` (`document_date` optional) | Search and Q&A citations carry bid, file, document type, addendum number and page or HTML element; extraction sources carry file and page or element | Audit; tests | Verified | Document date is filled only where detected |
| Handle scanned, empty and unreadable files gracefully and log them | Parser failure handling; `process-bid` stage reports | `tests/test_pipeline.py` (malformed PDF, empty HTML, unsupported file); live Bid1 run reports 6 OCR page failures and exits 3 | Offline tests and live run | Verified | — |
| OCR (bonus) | `ingestion/ocr.py` (Tesseract) | OCR text for Bid1 FINAL pp. 54–59 in `artifacts/assessment/Bid1/parsed` (container parse) | Saved artifacts | Verified in Docker; **blocked on this Windows host** (no Tesseract) | Install steps in README "OCR on a Windows host"; not tested on this machine |

## Section 6: Part B, RAG search engine

| Requirement | Implementation | Evidence | Verification | Status | Remaining limitation |
|---|---|---|---|---|---|
| RFP-suited chunking with overlap, justified in README | `chunking/engine.py` (heading-, list- and table-aware, 600/800 tokens, 100 overlap) | README Phase 3 | Chunk tests | Verified | Token counts are a byte estimate, not the embedding tokenizer |
| Embeddings | FastEmbed BGE-small-en-v1.5 (dense), Qdrant/bm25 (sparse) | `.env.example` `SEARCH__*`; collection profile `79fea6c89a6d` | Retrieval rerun | Verified | — |
| Vector store with metadata | Qdrant (local) | `bid_documents__79fea6c89a6d`, 533 points | Retrieval rerun; integration tests | Verified | — |
| Incremental indexing | `search/indexing.py`, generations and content hashes | `tests/test_search_indexing.py`; `pipeline_check` collection built by separate per-bid `process-bid` runs | Offline tests; Phase 10 live runs | Verified | — |
| Hybrid search with fusion | Dense + BM25 + exact identifiers, RRF | Retrieval report | Rerun reproduces | Verified | — |
| Metadata filtering (bid, doc type, addendum number) | `SearchFilters` (`bid_ids`, `doc_types`, `addendum_numbers`) | 0 cross-bid leaks in all 4 modes | Rerun | Verified | — |
| Re-ranking | Cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2`, depth 20 | `hybrid_rerank` mode | Rerun | Verified | ~1.6 s per query on CPU |
| Query rewriting / expansion | `plan_query`, synonym map (e.g. deadline → due date) | `search/` tests; `query` diagnostics in responses | Offline tests | Verified | Synonym list is fixed and English-only |
| Citations: source file, page, chunk text | `Citation` schema | All extraction and Q&A citations resolve (audit) | Audit | Verified | Q&A example 2 cites pages 1–2 with page 1 first; the text is on page 2 (pre-Phase 8 run) |
| REST API and/or CLI | CLI: `search`, `index-bid`, `ask`, `process-bid`. API: `GET/POST /search`, `POST /index`, index status | CLI used live throughout | Live CLI; API offline tests | Verified (CLI); API partial | API not exercised live; **no `POST /ask` endpoint** |
| Web UI (bonus) | — | — | — | Not implemented | Optional |
| ≥15 question → passage pairs covering both bids | `evaluation/assessment_retrieval.json` | 55 cases (50 answerable, 3 unknown identifiers, 2 unanswerable), 86 judgments | `evaluate_retrieval.py verify` | Verified | **Labels are assistant-made; not human-approved** |
| Recall@k and MRR/nDCG for ≥2 configurations, table in README | `evaluation/`, `scripts/evaluate_retrieval.py` | README retrieval table = `artifacts/eval/retrieval_phase7.json` = `retrieval_phase11.json` | Audit compares table; rerun | Verified | Small set; metrics depend on unreviewed labels |

Retrieval results (55 cases, 50 scored; reproduced 2026-10-24):

| Configuration | Recall@5 | MRR@10 | nDCG@10 | hit@5 |
|---|---|---|---|---|
| dense | 0.703 | 0.688 | 0.659 | 0.88 |
| sparse (BM25) | 0.786 | 0.781 | 0.749 | 0.88 |
| hybrid (RRF) | 0.736 | 0.795 | 0.741 | 0.88 |
| hybrid + rerank | 0.772 | 0.769 | 0.765 | 0.88 |

## Section 7: Part C, multi-agent system

| Requirement | Implementation | Evidence | Verification | Status | Remaining limitation |
|---|---|---|---|---|---|
| Orchestrator / planner | `agents/orchestrator.py` | Trace `artifacts/extraction_v4/traces/extraction_Bid1.json` (86 events) | Audit: live model, outcome `completed`, statuses match output | Verified | — |
| Ingestion agent | `process-bid` validate, parse, chunk, index stages (`pipeline.py`) | `artifacts/pipeline_v1/*/run_summary.json` | Live runs; `tests/test_pipeline.py` | Partial | Implemented as deterministic pipeline stages, not as an agent in the orchestrator. File handling needs no model, so no LLM step was added |
| Retrieval / search agent | `agents/retrieval.py` (bid-scoped, multi-query, rerank) | 36 search calls in the Bid1 trace | Audit | Verified | — |
| Extraction agents (specialists) | `agents/extractors.py`: identity, logistics, commercial, product, summary | Agents listed in all 6 live traces | Audit | Verified | — |
| Addendum reconciliation agent | `agents/reconciliation.py` | Bid1: 27-JUN-2024 14:00:00 → July 9, 2024 2:00 PM CST (Addendum 2), in `addendum_changes` | Audit; tests | Partial | **Only the Due Date is reconciled.** Other addendum changes are not applied field by field |
| Validator / critic with feedback and confidence scores | `agents/validator.py`, `semantics.py`, critic step | Retries ≤ 2 in every trace | `verify_extraction.py` (`retries_bounded`) | Partial | **Confidence is a fixed value per status (0.86 / 0.74 / 0.35), not calibrated.** Completeness checks cover only a few fields |
| Q&A / report agent | `agents/qa.py` | `artifacts/qa_examples.json`; `artifacts/final_demo/` | Audit: traces exist, excerpts verbatim | Partial | One bid per question; **no cross-bid comparison**. JSON and summary come from the extraction workflow |
| Framework choice explained | Own small orchestrator | `src/rfp/agents/README.md` | Read | Verified | — |
| Shared state | `agents/state.py` | Trace and output | Offline tests | Verified | — |
| Feedback loop with maximum retries | Orchestrator retry, max 2 | Trace `retry` fields | `verify_extraction.py` | Verified | — |
| Parallel agents | Group concurrency | `tests/test_extraction.py` | Offline test | Verified (offline) | Not measured live |
| Structured outputs | Pydantic models for all messages | `agents/schemas.py` | Offline tests | Verified | — |
| Guardrails: "Not found in documents", every value cited | Validator; Q&A `check_reply` | No accepted value without a citation in 6 outputs; Bid2 cross-bid probe abstains (`artifacts/final_demo/ask_bid2_abstention.json`) | Audit; live | Verified | Grounding is lexical, not entailment |
| Observability: input, tool calls, output, tokens, latency; one example trace | `agents/trace.py` | Bid1 trace: 8 agents, 36 searches, 26 model calls, 41,983 input / 2,653 output tokens, per-step `duration_ms` | Audit | Verified | No currency cost |
| Error handling (timeout, bad JSON, empty retrieval) | Model and search error paths | `tests/test_pipeline.py` (malformed output), extraction and Q&A tests | Offline tests | Verified | — |
| Extraction mode | `extract-bid`, `process-bid` | `artifacts/extraction_v4`, `pipeline_v1`, `pipeline_v2` | Live | Verified | — |
| Q&A mode with the section 7.3 example questions | `ask`, `ask-samples` | See next table | Live runs | Partial | 1 example unsupported, 1 false abstention |

Section 7.3 examples, using the latest saved live answers:

| Example question | Evidence | Outcome (assistant review) |
|---|---|---|
| Submission deadline for Bid1 after all addendums | `artifacts/final_demo/ask_bid1_due_date.json`; `qa_examples.json` #1 | Correct: July 9, 2024 at 2:00 PM CST, cites Addendum 2 p. 1 |
| Affidavits required for the Dell laptop bid | `artifacts/qa_v7/qa_samples.json` (Bid2) | **False abstention** ("Not found in documents"); Bid2 contains two affidavit PDFs |
| Compare warranty requirements of both bids | — | **Not supported**: `ask` covers one bid. Each bid's warranty was answered separately in `qa_v7` (correct per gold) |
| Is a bid bond required, and how much | `qa_v7` (Bid1, Bid2) | Correct abstentions per gold |
| What changed in Addendum 2 | `qa_v7` (Bid1) | Correct (the due-date change) |

## Section 8: Part D, structured extraction

| Requirement | Implementation | Evidence | Verification | Status | Remaining limitation |
|---|---|---|---|---|---|
| The 20 fields for every bid | `agents/schemas.py` `REQUIRED_FIELDS` | `artifacts/extraction_v4/Bid1.json`, `Bid2.json` | Audit: 20 fields in order in all 6 outputs | Verified (structure) | — |
| One JSON per bid: value, sources, confidence; null with reason; `addendum_changes`; `validation` | `BidExtraction` | Same files; validation counts match field statuses (audit) | Audit | Verified (structure) | Confidence not calibrated |
| Due Date with time and time zone, reflecting addenda | Extractor + reconciliation | Bid1 correct with time zone. **Bid2 is "06/10/2024" without the time and zone** (source: 02:00 PM EDT) | Audit; review | Partial | — |
| Extraction accuracy | Whole workflow | Model-assisted review of `extraction_v4`: Bid1 9 correct / 7 partial / 0 wrong / 0 missed / 4 null ok; Bid2 11 / 5 / 0 / 0 / 4 (`docs/final_accuracy_review.md`). `process-bid` reruns: Bid1 8/8/0/0/4, Bid2 10/6/0/0/4 | Assistant review against parsed text | Partial | **Not human-reviewed.** 12 partial fields across both bids; values vary between runs |

## Section 9: technical requirements

| Requirement | Implementation | Evidence | Verification | Status | Remaining limitation |
|---|---|---|---|---|---|
| Python 3.10+, any LLM provider | Python 3.12; `openai` / `openai_compatible` via `agents/llm.py` | Traces: `openai` / `gpt-4o-mini` | Live runs | Verified | Only OpenAI-compatible APIs implemented |
| Keys and model names from environment or config | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` in private `.env` | Secret scans | `verify_extraction.py`, audit scan | Verified | — |
| Runnable with one command after setup | `python -m rfp.cli process-bid --input <folder> --output <new folder>` | `artifacts/pipeline_v1`, `pipeline_v2`, `demo_process` | Live (Bid1 exit 3 for host OCR, Bid2 exit 0) | Verified | Setup still needs local Qdrant, `init-models` and keys |
| Modular structure | `ingestion/`, `chunking/`, `search/`, `agents/`, `api/`, `evaluation/`, `tests/` | Tree | Read | Verified | — |
| Unit tests for parsing and search | `tests/` | 407 offline + 6 integration passed | pytest | Verified | — |
| Works on a new, unseen bid folder without code changes | No bid literals in `src/`; `process-bid` derives everything from the folder | `tests/test_pipeline.py` (synthetic Harborview bid) | Offline tests | Partial | **Synthetic fixture with a fake model only**; no real unseen bid was available |

## Section 11: deliverables

| Deliverable | Evidence | Status | Remaining limitation |
|---|---|---|---|
| Source code in a Git repository | `origin` = `github.com/prince-1104/RFP-Intelligence-Platform` | Partial | Phase 11 changes are not committed or pushed |
| README: setup, dependencies, each mode, design decisions | `README.md` (chunking Phase 3, embeddings and retrieval Phase 4, agents Phase 5 and `src/rfp/agents/README.md`, prompts in `agents/extractors.py` and `qa.py`) | Verified | Long, phase-ordered document |
| Architecture diagram | `docs/architecture.md` (mermaid) | Verified | — |
| JSON output per bid with citations and confidence | `artifacts/extraction_v4/Bid1.json`, `Bid2.json` | **Blocked for submission** | `artifacts/` is git-ignored, so these files are not in the repository |
| Retrieval evaluation report | Question set `evaluation/assessment_retrieval.json` (tracked); results in README (tracked) and `artifacts/eval/retrieval_phase7.json` (ignored) | Verified (README table); report file not in the repository | — |
| Sample Q&A log, ≥10 cited answers | `artifacts/qa_examples.json`: 18 live questions, 13 answered with 20 citations (all resolve; 20/20 excerpts verbatim within the cited pages) and 5 abstentions; `docs/qa_examples.md` (tracked) | **Blocked for submission** | JSON is git-ignored. Review verdicts (11 correct, 2 partial, 5 correct abstentions) are assistant-made |
| One full extraction trace | `artifacts/extraction_v4/traces/extraction_Bid1.json` | **Blocked for submission** | Git-ignored |
| Demo video (5–10 min) or live demo | Script and commands in README "Demo sequence" | Not implemented | **No video has been recorded** |

Update 2026-10-25: contact-masked copies of the JSON per bid, the Bid1 trace, the Q&A log and the
retrieval report are prepared in `submission/evidence/` for owner approval
(`docs/submission_artifacts_manifest.md`). They stay blocked until they are committed.

## Section 12: bonus

| Item | Status | Note |
|---|---|---|
| Web UI | Not implemented | — |
| OCR | Verified in Docker; blocked on host | See Part A |
| Bid comparison agent | Not implemented | — |
| Go / no-go recommendation | Not implemented | — |
| Semantic caching; cost and latency per run | Partial | Token counts and per-step latency in traces; embedding cache for indexing; no query cache, no currency cost |
| Docker and CI | Partial | `Dockerfile`, `compose.yaml`; only the Qdrant service was used in validation; no CI pipeline |

## Section 13: submission guidelines

| Guideline | Status | Note |
|---|---|---|
| Repository link with all deliverables | Blocked | Deliverable artifacts are git-ignored (above) |
| Assumptions and known limitations listed | Verified | README limitation lists and "Known scope limits"; this document |

## Changes made in this audit

- `ask --collection NAME`: `src/rfp/cli.py`, `src/rfp/agents/qa_runner.py`. Same name rule as
  `process-bid`. The loopback check, bid filter and citation validation are unchanged. With no
  option, the search is built exactly as before. Tests: `tests/test_ask_collection.py` (a bid in a
  new collection answers with a citation; another bid's evidence in that collection is never
  shown or cited; a missing collection raises `index_not_found`; an invalid name and a remote URL
  are refused; the CLI passes the name and otherwise the default).
- `scripts/audit_submission.py`: the read-only artifact audit used above.
- README: "Final submission" section (artifact audit, `ask --collection`, Windows OCR setup,
  scope limits, PowerShell demo with live and offline steps separated, video script).
- `ASSIGNMENT_CHECKLIST.md`: statuses aligned with this document.

## Human review checklist (owner), in priority order

1. **Decide which artifacts to submit and add them to the repository.** `artifacts/` is ignored.
   Minimum set: `artifacts/extraction_v4/Bid1.json`, `Bid2.json`, `traces/extraction_Bid1.json`,
   `artifacts/qa_examples.json`, `artifacts/eval/retrieval_phase7.json`. These contain excerpts of
   the bid documents, which are deliberately not committed, so this is a disclosure decision.
2. **Disputed identifiers and aliases.** Bid2 Bid Number is "BPM044557" (eMMA project number) with
   "PORFP Number: #E20P4600040" kept as a cited alias. `docs/final_accuracy_review.md` calls this a
   documented interpretation, not a fact in the source. Confirm it, and confirm Bid1 "JA-207652"
   with "SOURCING #168884" as its alias. The Bid1 Q&A probe "What is PORFP number E20P4600040 for?"
   abstains because that number belongs to Bid2.
3. **Partial fields** (`docs/final_accuracy_review.md` gives the evidence for each).
   - Bid1 (7): Pre Bid Meeting (Teams format missing), Delivery Date, Additional Documentation (1 of
     4 forms), MFG for Registration (heading only), contact_info (department phone), Bid Summary,
     Product Specification (Tier 1 processor and display missing).
   - Bid2 (5): Due Date (no time or zone), Payment Terms, Product (no quantity), Bid Summary, Product
     Specification.
   - Also confirm the 8 nulls accepted as correct, including Bid1 Payment Terms (unresolved).
4. **Whether incomplete fields should be exposed.** Decide whether partial values stay in the JSON
   as they are, or are nulled with a note. The current output keeps them, with confidence 0.86.
5. **Q&A abstentions and partial answers.** The 5 false abstentions on the 14-case gold set
   (`artifacts/eval/qa_v7.json`: Bid1 pre-proposal and contact; Bid2 affidavits, due date, RAM; 9/14
   correct) and the 2 partial answers in `qa_examples.json` (#4 wireless standards, #11 Bid2
   procurement contact).
6. **Citation correctness.** Open a sample of cited pages, including Q&A example 2 (cites pages
   1–2 with page 1 first; the buyer line is on page 2) and the six live extraction traces.
7. **Retrieval labels.** Check a sample of `evaluation/assessment_retrieval.json`, at least one
   per kind (8 kinds), and the `needs_human_labeling` list. The metrics depend on these labels.
8. **Record or schedule the demo** using the README script; the video is outstanding.
9. **Optional:** install Tesseract on the host and rerun Bid1 `process-bid` into a new output folder
   to remove the partial OCR status.
