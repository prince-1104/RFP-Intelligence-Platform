# Submission artifacts manifest

Prepared 2026-10-25 for owner approval. Nothing has been staged, committed or pushed. Contents
were inspected with an automated scan (secrets and key shapes, email addresses, phone numbers,
absolute paths, connection strings, strings over 300 characters) and by reading each file's
structure. No secret, API key, connection string, absolute path or username was found in any
candidate. No human has reviewed the outputs themselves.

Classifications: **Safe to publish**, **Requires redaction** (a masked copy is prepared),
**Do not publish**.

## Assignment requirements (section 11) and the evidence chosen

| Requirement | Evidence in `submission/evidence/` | Needed? |
|---|---|---|
| JSON output files, one per bid | `Bid1_extraction.json`, `Bid2_extraction.json` | Required |
| Agent trace: one full example of an extraction run | `Bid1_extraction_trace.json` | Required (one trace); a Bid2 trace is optional |
| Sample Q&A log, ≥10 questions with cited answers | `qa_examples.json` | Required; per-question Q&A traces are not required |
| Retrieval evaluation report: question set and metrics per configuration | `retrieval_evaluation.json`; questions and labels already tracked in `evaluation/assessment_retrieval.json` | Required |
| Demo video or live demo | Not an artifact; script in README | Required; **outstanding** |
| Machine-readable verification report | `submission_audit.json` | Not required; supporting |
| Demo output or run summary | `Bid2_process_bid_run_summary.json`, `demo_ask_*.json` | Not required; supporting |

## Candidates

"Ignored" means git-ignored at its original path. Every original remains in place, unmodified.

| Original path | Purpose | Ignored | Bid excerpts | Personal info | Sensitive business info | Secrets | Classification | Action |
|---|---|---|---|---|---|---|---|---|
| `artifacts/extraction_v4/Bid1.json` | JSON per bid | Yes | Yes: values are spans of the solicitation, longest 1,340 chars (Product Specification) | Buyer name, email and department phone in `contact_info` | Public solicitation terms only | None | Requires redaction | Copied as `Bid1_extraction.json`, email and phone masked |
| `artifacts/extraction_v4/Bid2.json` | JSON per bid | Yes | Yes, longest 406 chars | Procurement officer name, email and phone in `contact_info` | Public solicitation terms only | None | Requires redaction | Copied as `Bid2_extraction.json`, email and phone masked |
| `artifacts/extraction_v4/traces/extraction_Bid1.json` | Full extraction trace | Yes | Short only: search queries, file and page locators, validator reasons with rejected values; no passages or prompts | None found | None | None | Safe to publish | Copied unchanged as `Bid1_extraction_trace.json` |
| `artifacts/extraction_v4/traces/extraction_Bid2.json` | Second trace | Yes | Same as Bid1 | None found | None | None | Safe to publish | **Not included**: not required; available on approval |
| `artifacts/qa_examples.json` | Q&A log | Yes | Yes: short verbatim quotes; longest answer 490 chars | 6 email occurrences (Bid1 buyer, Bid2 officer) in examples 2 and 11 | None | None | Requires redaction | Copied as `qa_examples.json`, 6 emails masked |
| `artifacts/eval/retrieval_phase7.json` | Retrieval report | Yes | Only truncated file names with page or element ids | None | None | None | Safe to publish | Copied unchanged as `retrieval_evaluation.json` |
| `artifacts/eval/submission_audit.json` | Verification report | Yes | None | None | None | None | Safe to publish | Copied unchanged |
| `artifacts/pipeline_v1/Bid2/run_summary.json` | One-command run summary | Yes | None | None | Loopback Qdrant URL and collection name only | None | Safe to publish | Copied unchanged as `Bid2_process_bid_run_summary.json` |
| `artifacts/final_demo/ask_*.json` (3) | Live Q&A demo outputs | Yes | Short quotes | None | None | None | Safe to publish | Copied unchanged as `demo_ask_*.json` |
| `artifacts/final_demo/traces/*.json` | Q&A traces | Yes | None (question stored as a hash) | None | None | None | Safe to publish | **Not included**: not required |
| `artifacts/eval/extraction_v4_check.json` | Per-field citation support | Yes | Yes, repeats every extraction value | Same contacts as the extraction files | None | None | Requires redaction | **Not included**: duplicates the extraction values; `submission_audit.json` covers verification; reproducible with `verify_extraction.py` |
| `artifacts/eval/qa_v7.json` | Q&A gold-set scores | Yes | Short | 1 email | None | None | Requires redaction | **Not included**: the results are summarized in tracked `docs/qa_examples.md` |
| `data/assessment/*` (bid PDFs and HTML) | Inputs | Yes | Full documents | Yes | Full documents | None | Do not publish | Excluded; publication is not authorized |
| `artifacts/assessment/`, `artifacts/parsed/`, `artifacts/chunks/`, other runs, `.cache/`, Qdrant volume | Intermediate outputs, caches, index | Yes | Full document text | Yes | Yes | None | Do not publish | Excluded |
| `.env` | Configuration | Yes | — | — | — | Yes | Do not publish | Excluded |

## Redaction

`scripts/build_submission_evidence.py build` replaces email addresses and phone numbers inside
JSON string values with `[email redacted]` and `[phone redacted]`. It changes nothing else.
`submission/evidence/redactions.json` lists each masked JSON path and the SHA-256 of each source.
A structural comparison confirmed that the copies differ from the originals only at those 10
paths. Effects:

- Names and organizations are kept. The `contact_info` fields still show who to contact, but not
  how.
- Masked Q&A excerpts (examples 2 and 11) are no longer verbatim. Citation file and page are
  unchanged, and the excerpts were verified before masking.
- No value was replaced with an invented one. No label or metric changed.

## Already published (decision needed)

`origin` (`github.com/prince-1104/RFP-Intelligence-Platform`) is **public**, according to the
GitHub API on 2026-10-25. Commits up to Phase 10 are pushed, and these tracked files already carry
bid text or contact details:

| Tracked file | Content |
|---|---|
| `evaluation/review_packet/packet.json` (3.4 MB) | Full text of retrieved chunks for 55 cases; 14 distinct email addresses, including vendor addresses from the documents (dell.com, emc.com) |
| `evaluation/review_packet/evidence.csv` (1.1 MB) | 1,294 retrieved excerpts (up to 1,310 chars); 9 distinct email addresses |
| `evaluation/review_packet/cases.csv`, `evaluation/assessment_retrieval.json`, `evaluation/qa_reviewed.json` | Labels with short quotes, including the buyer and officer emails |
| `docs/final_accuracy_review.md`, `docs/live_validation.md`, `docs/qa_examples.md` | Field values including contact emails and phone numbers |

The review packet is effectively a large extract of the source documents, which `.gitignore`
deliberately excludes. Masking the new evidence copies does not change what is already public.
Options for the owner: accept (the solicitations are public procurement notices); remove or slim
the packet in a new commit (it stays in history); or rewrite history and force-push (this
invalidates existing clones). None of these was done.

## Git

- `.gitignore` is unchanged. `submission/` matches no rule; `artifacts/`, `data/assessment/*`,
  `.env`, caches and logs remain ignored (`git check-ignore`).
- Files proposed for staging: `submission/evidence/` (12 files), `scripts/build_submission_evidence.py`,
  `docs/submission_artifacts_manifest.md`, `README.md`.
