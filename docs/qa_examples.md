# Cited Q&A examples and evaluation

Phase 7 added an 18-question live set (`examples/qa_phase7_questions.json`). Its reviewed results
are in `artifacts/qa_examples.json` and `docs/live_validation.md`. This page covers the 14-question
gold set.

## Live runs

All runs used `openai` / `gpt-4o-mini` against the local Qdrant collection, with the 14 questions in
`examples/qa_questions.json` (7 per bid). Outputs are under `artifacts/` (gitignored):

| Folder | Code version |
|---|---|
| `artifacts/qa/` | Phase 6 baseline (single answer + citations, fact check against whole passage) |
| `artifacts/qa_v2/` .. `qa_v4/` | intermediate hardening runs |
| `artifacts/qa_v5/` | final hardening: verbatim quotes, claim-level grounding, checklist, claim dropping |

Regenerate with:

```powershell
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_questions.json --output artifacts\qa_v5
```

## Reviewed evaluation set

`evaluation/qa_reviewed.json` holds expectations for the 14 questions. Each case has the expected
answer or abstention, required facts (with accepted alternatives), required list items, material
omissions, forbidden claims, source document and page, and verbatim supporting quotes. Expected
answers were checked against the parsed source text, not taken from model output. All 14 cases are
marked `verified` by an assistant-assisted review and still need human confirmation. Two cases are
unanswerable by design (Bid2 liquidated damages and bid bond); the Bid1 bid-bond case is also
unanswerable because the RFP only mentions bond requirements "noted elsewhere" without stating one.

```powershell
.\.venv\Scripts\python scripts\evaluate_qa.py verify   # every gold quote is on its declared page
.\.venv\Scripts\python scripts\evaluate_qa.py score --samples artifacts\qa_v5\qa_samples.json
```

Gold metrics use verified cases only; `needs_review` cases are listed and excluded. Automated
checks (claim support, exact quote validity against parsed page text) need no gold label and are
not evidence of correctness.

## Results (14 verified cases, 11 answerable, 3 unanswerable)

| Metric | Baseline `qa/` | Final `qa_v5/` |
|---|---|---|
| Answer correctness | 12/14 (0.86) | 9/14 (0.64) |
| Citation source accuracy (answered cases with gold sources) | 0.80 | 0.83 |
| List item completeness (affidavits) | 1/2 | 0/2 (abstained) |
| False abstention rate (answerable) | 1/11 | 5/11 |
| Correct abstention rate (unanswerable) | 3/3 | 3/3 |
| Claim support (automated, claims vs. own quotes) | 5/10 | 7/7 |
| Exact quote validity (automated, vs. parsed page text) | 9/10 | 6/7 |

The hardening removed every unsupported claim (pre-proposal time zone and platform, "all other
provisions unchanged", affidavit requirements) but `gpt-4o-mini` often fails the stricter quoting
rules even after one correction: it reformats dates (06/10/2024 -> June 10, 2024), stitches table
cells into one quote, or names entities absent from its quote. Those replies now abstain instead of
answering, which is why correctness fell. Run-to-run variation is also visible: the affidavit
answer was complete in `qa_v2`, partially correct in `qa_v4`, and abstained in `qa_v5`.

## Per-case review of the baseline answers

| Case | Baseline verdict |
|---|---|
| Bid1 final deadline | Correct (Addendum 2: July 9, 2024 at 2:00 PM CST). |
| Bid1 Addendum 2 changes | Correct date; "all other provisions unchanged" is true in the source but was not in the cited quote. |
| Bid1 bid bond | Correct abstention: only generic "noted elsewhere" bond clauses exist (FINAL pp. 9, 17). |
| Bid1 pre-proposal | Facts true (FINAL p. 2), but the quote did not contain the time zone or TEAMS, and the citation reported page 1 of a chunk spanning pages 1-2. |
| Bid1 RFP number | Correct. |
| Bid1 contact | Incorrect: the RFP directs questions to the Buyer, Jasmine Alzate (JALZATE@dallasisd.org); the answer gave the BidNet general contact. |
| Bid1 warranty | Correct for Addendum 1; omits tablets (1-year) and desktops/monitors (3-year). |
| Bid2 affidavits | Incomplete: Mercury Affidavit only; the Contract Affidavit form is in the bid package (its submission timing is not stated). |
| Bid2 due date | False abstention: 06/10/2024 is in the PORFP table and the BidNet closing date (02:00 PM EDT). |
| Bid2 warranty | Correct (3 years from delivery). |
| Bid2 RAM | Correct (16 GB, from the Dell configuration sheet). |
| Bid2 quantity | Correct (30 Latitude 5550 laptops; verified from the page 3 table). |
| Bid2 liquidated damages | Correct abstention. |
| Bid2 bid bond | Correct abstention. |
