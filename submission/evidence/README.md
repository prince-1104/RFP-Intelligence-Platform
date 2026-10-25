# Submission evidence

Copies of the generated artifacts that the assignment asks for (section 11). The originals live in
`artifacts/`, which is git-ignored and was not modified. These copies were produced by
`scripts/build_submission_evidence.py build`, which changes only one thing: email addresses and
telephone numbers inside JSON strings are replaced with `[email redacted]` or `[phone redacted]`.
`redactions.json` lists each replacement by JSON path and kind (never the value) and records the
SHA-256 of every source file.

**No human has reviewed these outputs.** Review notes and verdicts inside them are
assistant-assisted.

| File | Source | Kind | What it shows | Assignment item |
|---|---|---|---|---|
| `Bid1_extraction.json`, `Bid2_extraction.json` | `artifacts/extraction_v4/` | Live, `gpt-4o-mini`, 2026-10-24 | The 20 fields with value, sources (file and page or HTML element), confidence and notes; `addendum_changes`; `validation` counts | JSON output per bid (8.1, 11) |
| `Bid1_extraction_trace.json` | `artifacts/extraction_v4/traces/extraction_Bid1.json` | Live | One full extraction run: 86 steps by 8 agents, search queries, evidence locators, validator decisions, retries, tokens and latency. It contains no passage text or prompts | Agent trace (7.2, 11) |
| `qa_examples.json` | `artifacts/qa_examples.json` | Live | 18 questions over both bids: 13 cited answers and 5 abstentions, each with validation and an assistant review note | Sample Q&A log, ≥10 questions (11) |
| `retrieval_evaluation.json` | `artifacts/eval/retrieval_phase7.json` | Local search, no LLM | Recall@5, MRR@10, nDCG@10, hit@5 and latency for dense, sparse, hybrid and hybrid + rerank over 55 cases, plus per-case ranks | Retrieval evaluation report (6.4, 11) |
| `submission_audit.json` | `artifacts/eval/submission_audit.json` | Automated check | Machine-readable result of `scripts/audit_submission.py`: field counts, citations resolved, trace consistency, Q&A excerpt checks, README table match | Verification report |
| `Bid2_process_bid_run_summary.json` | `artifacts/pipeline_v1/Bid2/run_summary.json` | Live | The one-command pipeline's stages, counts, exit code and output paths for Bid2 | One command after setup (9) |
| `demo_ask_bid1_due_date.json` | `artifacts/final_demo/` | Live | Bid1 deadline after addenda, cited to Addendum 2 page 1 | Q&A example 7.3 |
| `demo_ask_bid2_collection.json` | `artifacts/final_demo/` | Live | `ask --collection pipeline_check` answering from a separately indexed collection | Q&A mode |
| `demo_ask_bid2_abstention.json` | `artifacts/final_demo/` | Live | "Not found in documents" for a Bid1 contact asked of Bid2 (bid isolation) | Guardrail (7.2) |
| `phaseA_ask_bid2_affidavits.json` | `artifacts/phaseA_qa/01_affidavit_bid2.json` | Live, 2026-10-25 | Bid2 affidavits after the false-abstention fix: Mercury Affidavit cited to PORFP p2; Contract and Proposal Affidavit listed as unaddressed | Q&A example 7.3 |
| `phaseA_compare_warranty.json` | `artifacts/phaseA_qa/05_warranty_compare_after_reason_fix.json` | Live | "Compare the warranty requirements of both bids": per-bid cited findings and lexical aspect relations | Q&A example 7.3 (cross-bid) |
| `phaseA_compare_warranty_run1.json` | `artifacts/phaseA_qa/02_warranty_compare.json` | Live | First run of the same question, kept rather than replaced; one Bid2 claim is broader than its quote | Same |
| `phaseA_compare_isolation_probe.json` | `artifacts/phaseA_qa/03_isolation_probe_compare.json` | Live | Mercury Affidavit compared across bids: Bid1 not found, Bid2 answered from its own documents | Bid isolation (7.2) |
| `phaseA_compare_unanswerable.json` | `artifacts/phaseA_qa/04_unanswerable_compare.json` | Live | A comparison neither bid answers: `Not found in documents` | Guardrail (7.2) |
| `phaseB_ask_bid1_warranty.json` | `artifacts/phaseB_addenda/qa/bid1_warranty.json` | Live, 2026-10-25 | Bid1 warranty after addendum reconciliation: Chromebook one-year and Windows laptop three-year minimums, OEM warranties, cited to Addendum 1 p1 | Q&A example 7.3; addenda (7.1) |
| `phaseB_compare_warranty.json` | `artifacts/phaseB_addenda/qa/compare_warranty.json` | Live | Warranty comparison in which the Bid1 finding cites Addendum 1 p1–p2 and the Bid2 finding cites PORFP p3 | Q&A example 7.3 (cross-bid) |
| `phaseB_Bid1_extraction.json` | `artifacts/phaseB_addenda/extraction/Bid1.json` | Live | Bid1 extraction with ten `addendum_changes` records (Due Date replace by Addendum 2; nine Addendum 1 clarifications), each with change type, status and verbatim quote | Addendum reconciliation (7.1, 8.1) |
| `phaseB_reconciliation_Bid1.json` | `artifacts/phaseB_addenda/reconciliation/reconciliation_Bid1.json` | Local search, no LLM | Per-field requirement history: baseline passages, each addendum revision with type, status, reason and sources | Addendum reconciliation (7.1) |
| `redactions.json` | Generated | — | Masked paths and source hashes | — |

The evaluation questions and labels are already tracked in `evaluation/` (`assessment_retrieval.json`,
`qa_reviewed.json`). Offline tests (`tests/`) use fake models and a synthetic bid; none of the files
here came from them.

## Effect of the redaction

- Masked: `contact_info` in both extraction files and in `phaseB_Bid1_extraction.json` (one email
  and one phone number each), and six
  email addresses in Q&A examples 2 and 11 (answer, claim, citation excerpt, review note).
- Names, organizations, values, citations, metrics and every other field are unchanged.
- The masked excerpts are no longer verbatim copies of the source. They were verified before
  masking, against the originals (`submission_audit.json`).
- `trace` paths in `qa_examples.json` and paths inside `submission_audit.json` point to the
  original locations under `artifacts/`, which are not in the repository.

## Verifying

From a clone (no `artifacts/`, models, Qdrant or keys needed):

```powershell
.\.venv\Scripts\python scripts\build_submission_evidence.py verify
```

This checks that every file parses, that both extraction files have the 20 fields with no uncited
value, that the trace comes from a live model and covers the 20 fields, that at least 10 Q&A answers
carry citations, that the Phase A files validate against the Q&A and comparison schemas and came
from a live model, that the Phase B answers are live and cite an addendum, that the Phase B
extraction records an applied Due Date change with sources, that the reconciliation report is
complete, and that the README retrieval table matches `retrieval_evaluation.json`.

With the local artifacts present (the machine that produced them):

```powershell
.\.venv\Scripts\python scripts\build_submission_evidence.py check   # copies equal a fresh build; sources unchanged
.\.venv\Scripts\python scripts\audit_submission.py                  # citations resolve against parsed pages
```

## Reproducing

The commands that produced the originals, the LLM settings they need, and the known limitations
are in the README section "Submission Evidence and Reproducibility". Reproduction needs the
assessment bid documents, which are not in this repository.
