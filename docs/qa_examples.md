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

## Phase A: affidavit fix and cross-bid comparison (live, 2026-10-25)

Model `openai` / `gpt-4o-mini`. Outputs are in `artifacts/phaseA_qa/` (git-ignored; its
`README.md` lists every run), with masked copies in `submission/evidence/phaseA_*.json`. Every run
made is listed here, including the superseded one. Each excerpt was checked against the
source text (local Qdrant passages) by the assistant; no human has reviewed these.

**Cause of the `qa_v5`/`qa_v7` affidavit abstention.** Retrieval returned PORFP p2 ("The Master
Contractor must provide a Mercury Affidavit") first, and the first reply's Mercury claim passed
validation. Two other claims ("Vendors must submit a Proposal / Contract Affidavit") failed,
because no quote states a requirement for them. The correction prompt allows `not_found`; the model
took it, and the validated Mercury claim was discarded. `ask` now keeps the latest validated claims
when a later attempt abstains or fails. The fix does not mention affidavits or bids.

| Question | Status | Answer and citations | Review |
|---|---|---|---|
| Bid2: Which affidavits must vendors submit with their proposal? | answered | "Vendors must submit a Mercury Affidavit." — PORFP p2: "The Master Contractor must provide a Mercury Affidavit:" | Correct; gold item completeness 1/2. The Contract Affidavit (and the Proposal Affidavit it refers to) are listed as "named in retrieved evidence but not addressed" because no passage says they are due with the proposal. Kept from attempt 1; attempt 2 failed |
| Compare the warranty requirements of both bids (run 1) | answered | Bid1: "minimum of three years" (FINAL p6 "Warranty: 3-year minimum"); repairs at no cost within five business days (FINAL p4). Bid2: "three years following the date of delivery" (PORFP p3 "Extended for all machines purchased - 3 Years") | Facts true. The Bid2 claim's "following the date of delivery" is on p3 but not in its quote. Bid1 omits Addendum 1 (one-year Chromebooks, three-year Windows laptops, OEM warranties) and the 1-year tablet minimum, although Addendum 1 p1 was retrieved |
| Compare the warranty requirements of both bids (run 2) | answered | Bid1: 3-year minimum (FINAL p6), warranties equal to or exceeding those offered to the public (p4), no-cost repairs within five business days (p4). Bid2: "3 years following the date of delivery" (PORFP p3, quoted exactly) | Same Addendum 1 omission. The Bid1 claim says "for staff laptops", which is narrower than the source (the 3-year minimum is listed under several device tiers). Aspect `warranty period` is `different` lexically ("3-year minimum" vs "3 Years") |
| Compare the Mercury Affidavit requirements of both bids (isolation probe) | partial | Bid1: Not found in documents. Bid2: Mercury Affidavit required (PORFP p2) and "The product(s) offered do not contain mercury." (Mercury_Affidavit.pdf p1) | Correct isolation: Bid2's documents were not used for Bid1 |
| Compare the on-site vendor parking arrangements described in both bids | not_found | Not found in documents (both bids) | Correct abstention |

Gold scoring of the affidavit answer (`score` on `bid2-affidavits`): answer correctness 1, source
accuracy 1, false abstention 0, item completeness 0.5. The 14-case set was not re-run as a whole in
Phase A, so the table above it still describes `qa_v5`.

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
