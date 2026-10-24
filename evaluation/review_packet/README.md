# Retrieval label review packet

This packet lets a reviewer confirm or correct the ground-truth labels in
`evaluation/assessment_retrieval.json` before they are used to tune retrieval or approve a
production indexing canary. Reviewing never edits that file: decisions go into a copy of
`cases.csv`, are validated, and are merged into **separate** proposed files.

| File | Purpose |
| --- | --- |
| `cases.csv` | One row per case (55). Current labels on the left, blank reviewer columns on the right. Edit a copy. |
| `evidence.csv` | Candidates actually returned by search per case and mode (hybrid_rerank top 10; hybrid, sparse, dense top 5). |
| `packet.json` | Same data with full chunk text, the manifest and each bid's document list (used by validation). |
| `SUMMARY.md` | Dataset facts, observations from this retrieval run, known misses. |

Regenerate with `python scripts/build_retrieval_review_packet.py build` (local Qdrant only,
read-only). Output is deterministic for unchanged inputs; regenerating after the dataset changes
invalidates existing review files (validation refuses them).

## What the current labels are

Each answerable case has one or more **judgments**: a source document, a one-based PDF `page`
or an HTML `html_element_id`, the text that must appear in the retrieved chunk, and a grade
(2 = directly answers, 1 = partial/supporting). In `cases.csv` they appear in
`current_expected_evidence` as

```
[J1] grade=2 <document> | page=<n> | text=<snippet> && <snippet>
```

The dataset has **no expected-answer field**, so `current_expected_answer` is always blank.
The reviewer writes the answer. `listed_in_needs_human_labeling` is set only where a
`needs_human_labeling` note names the case ID; the other notes are in `SUMMARY.md` and must be
mapped by the reviewer. Every case is unreviewed until a reviewer sets `review_status`.

## Classifications

- **answerable**: the bid's documents contain the answer; at least one judgment required.
- **unanswerable**: a natural-language question the documents do not answer (for example a bid
  bond requirement that does not exist). Search may still return related text; the case passes
  only if a human confirms none of it answers the question.
- **unknown_identifier**: an identifier that occurs nowhere in the bid (`JA-999999`,
  `E20P9999999`, `210-ZZQX`). Correct behaviour is no results plus the warning
  `identifier_evidence_not_found`.

## How to review a case

1. Read the `query` and decide from the **source documents** (open the PDF at the page, or the
   HTML page) what the correct answer is. Use the documents in `data/assessment/<Bid>`.
2. Check every current judgment: is the document right, is the page/element right, does the
   required text appear there, and does it support the answer? Use `evidence.csv` to see what
   search returned and its actual citations.
3. A relevant search result does not prove the label is correct. `matches_current_judgments`
   is an automatic text+locator match against the current labels, not a human confirmation; a
   match can be to a wrong label, and a correct passage can fail to match a too-strict label.
4. Fill in the reviewer columns:

| Column | Content |
| --- | --- |
| `review_status` | `approved` (labels correct as they are), `corrected` (labels need changes), `rejected` (drop the case), `needs_discussion` (unresolved). Blank = unreviewed. |
| `reviewer_id` | Your initials or ID. Required for any status. |
| `reviewer_answerability` | `answerable`, `unanswerable` or `unknown_identifier`. Required for approved/corrected. `approved` must equal `current_answerability`. |
| `reviewer_kind` | Optional; required only when an unanswerable/unknown case becomes answerable (`identifier`, `specification`, `deadline`, `addendum_change`, `affidavit`, `requirement`). |
| `reviewer_expected_answer` | The answer in your words. Required for answerable cases; must be blank otherwise. |
| `reviewer_evidence` | Supporting locations, one per line: `<document> \| page=<n>` or `<document> \| html=<element id>`. Add `\| text=<snippet> && <snippet>` for any location not in the current labels (text must appear verbatim in the chunk). Required for answerable cases; blank otherwise. |
| `reviewer_notes` | Rationale. Required for every status except `approved`. |

Use the document names exactly as listed in `packet.json` → `bid_documents`. PDF pages are
one-based physical pages of the file (as in the viewer), not printed page numbers.

## Hard cases

- **Conflicting documents** (e.g. an addendum changes the RFP): the latest controlling document
  is the answer; keep superseded text out of the evidence unless the question asks for it, and
  say so in the notes (see `b1-final-deadline`).
- **Missing evidence**: if you cannot find support in the documents, do not approve. Use
  `corrected` with `unanswerable`, or `needs_discussion`.
- **Incorrect citation** (right text, wrong page/element): `corrected`, with the right locator
  in `reviewer_evidence`.
- **Ambiguous labels or extraction disagreements** (e.g. Bid1 page 35 prose vs table quantities;
  Bid2 PORFP page 1 date ordering): check the PDF visually; if still ambiguous use
  `needs_discussion` and describe both readings.
- **OCR pages** (Bid1 main RFP pages 54-59, IRS Form W-9): no cases target them; do not add
  table cells that the OCR output does not contain.

## Disagreements

Each reviewer works on their own copy (`cases.<reviewer>.csv`). Compare copies and resolve
differences by discussion; record the outcome and the reasoning in `reviewer_notes` of the
agreed file. Unresolved cases stay `needs_discussion` and keep their current labels.

## Validate and merge

```powershell
.\.venv\Scripts\python scripts\build_retrieval_review_packet.py validate --reviews evaluation\review_packet\cases.rv1.csv
.\.venv\Scripts\python scripts\build_retrieval_review_packet.py merge --reviews evaluation\review_packet\cases.rv1.csv `
    --out-dataset evaluation\review_packet\proposed_dataset.json --out-labels evaluation\review_packet\approved_labels.json
```

`validate` rejects unknown, duplicate or missing case IDs, reviewer fields filled without a
status, unknown statuses or classifications, missing reviewer ID, answers or rationale, documents
not in the bid, PDF evidence without a page and HTML evidence without an element ID.
`merge` refuses any invalid file, never writes the canonical dataset or the review file, refuses
to overwrite existing outputs without `--force`, and prints the change summary. `--require-complete`
refuses unless every case is approved, corrected or rejected.

Only `corrected` changes labels in the proposed dataset (`rejected` removes the case);
`approved` keeps them; unreviewed and `needs_discussion` cases keep their current labels and are
listed as not approved. `approved_labels.json` records every decision, the reviewer, the
answer, the evidence and a copy of the original case for audit.

## After approval

1. Verify the proposed labels are grounded:
   `python scripts/evaluate_retrieval.py verify --dataset evaluation\review_packet\proposed_dataset.json`.
2. Recalculate metrics with the same command and modes as the baseline:
   `python scripts/evaluate_retrieval.py run --dataset evaluation\review_packet\proposed_dataset.json --out ...`.
3. Replacing `evaluation/assessment_retrieval.json` is a separate, deliberate step: review the
   printed change summary (and `git diff` after copying), bump `version`, and update
   `needs_human_labeling` for the items resolved.
