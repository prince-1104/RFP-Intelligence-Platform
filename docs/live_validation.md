# Live end-to-end validation (Phase 7)

Run date 2026-10-24. Every result below comes from a live model call (`openai` / `gpt-4o-mini`,
configured through the existing `build_live_model()` client) against the local loopback Qdrant
collection that already held both bids. Nothing was re-indexed, and no remote service other than
the configured model provider was contacted. Offline test results are reported separately at the end.

The accuracy verdicts are an assistant review of each value against the parsed source text in
`artifacts/assessment/<Bid>/parsed`. A human reviewer has not confirmed them yet.

## LLM configuration check

`build_live_model()` read `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` from the private `.env`.
One `{"ok": true}` call returned in about 2 s using 21 input and 5 output tokens. Only the
provider, model, token counts and latency were printed. `scripts/verify_extraction.py` checks
outputs and traces for every secret value configured in `.env` and for API-key-shaped strings.
Every run found none.

## Extraction (`extract-bid`)

```powershell
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts\extraction_v2 --trace-dir artifacts\extraction_v2\traces
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid2 --output artifacts\extraction_v2 --trace-dir artifacts\extraction_v2\traces
.\.venv\Scripts\python scripts\verify_extraction.py --outputs artifacts\extraction_v2 --out artifacts\eval\extraction_v2_check.json
```

| Run | Bid | Accepted | Not found | Unresolved | Model calls | Tokens in / out |
|---|---|---|---|---|---|---|
| First live run (`artifacts/extraction`) | Bid1 | 6 | 12 | 2 | 21 | 48,016 / 3,710 |
| First live run | Bid2 | 8 | 9 | 3 | 24 | 50,119 / 4,771 |
| After fixes (`artifacts/extraction_v2`) | Bid1 | 12 | 8 | 0 | 18 | 34,795 / 1,637 |
| After fixes | Bid2 | 12 | 7 | 1 | 23 | 40,591 / 2,399 |

The first run exposed four defects, all now fixed and covered by tests in `tests/test_extraction.py`:

1. The group prompts described fields loosely ("issuing organization", "contract term"). The model
   answered under its own keys (`Issuing Organization`, `term_of_bid`), so correct values were
   discarded and fields ended as null or unresolved. The prompt now lists the exact keys, and reply
   keys that differ only in case or separators are matched.
2. Hits from all of a field's query steps were concatenated, and only the first 8 were shown to the
   model. Secondary queries (addendum-only, specs-only, label searches) were therefore invisible
   whenever the primary query returned 8 or more hits. Steps are now merged round-robin by rank.
   Title and contact_info also gained label-style queries ("request for proposal solicitation
   title", "buyer name and email address").
3. Addendum reconciliation used the first date in any RFP passage as the original due date, so it
   recorded the advertisement date 26-MAY-2024. It now uses only a date that directly follows a
   due-date label, which gives 27-JUN-2024 14:00:00. With no label, the original stays null.
4. The validator required a full date for Delivery Date and rejected "September of 2024". A stated
   month and year is now accepted. It must still be present in the cited evidence.

Automated checks on `artifacts/extraction_v2`, from `artifacts/eval/extraction_v2_check.json`:

- All outputs validate against `BidExtraction`, and the 20 field names match in assignment order.
- No cited file comes from the other bid.
- Every trace has outcome `completed`, retries never exceed 2, and no secrets were found.
- 22 of 24 accepted values appear verbatim or token-complete on their cited pages.
- The two exceptions are both Bid1 values cited to page 1 whose text is on page 2. The chunk spans
  pages 1 and 2, but the output source keeps only the chunk's first page.

### Field review (`artifacts/extraction_v2`)

Verdicts:

- **correct:** the value and source are right.
- **partial:** the value is related but incomplete, or the citation page is off.
- **wrong:** the value is not what the field asks for.
- **missed:** the value is null but stated in the source.
- **null ok:** no source statement was found.

| Field | Bid1 value → verdict | Bid2 value → verdict |
|---|---|---|
| Bid Number | JA-207652 → correct | 00004079359 (BidNet reference) → partial; the PORFP number is E20P4600040 |
| Title | Student and Staff Computing Devices → correct | "Purchase Order Request for Proposals (PORFP) Hardware Master Contract" → partial; the form header, not "Dell Laptops w/Extended Warranty" |
| Due Date | July 9, 2024 at 2:00 PM CST (Addendum 2 p1) → correct | 06/10/2024 (PORFP p1) → correct; but its notes wrongly call 06/01/2024 (the questions deadline) the original due date |
| Bid Submission Type | null → missed (FINAL p9: online via the iSupplier portal) | "Sealed Proposals process under COMAR 21.05.03" → partial; notes contradict the value |
| Term of Bid | 3 years + two 1-year renewals, max 5 → correct | null → null ok (single purchase order) |
| Pre Bid Meeting | 10-JUN-2024 14:00:00 → correct, cited p1 (text on p2) | null → null ok |
| Installation | null → missed (FINAL p7 deployment services include software installation) | null → null ok |
| Bid Bond Requirement | null → null ok (generic bond clauses only) | null → null ok |
| Delivery Date | September 2024 → partial; Addendum 1 says quote requests start then, not delivery | 06/10/2024 → **wrong**; that is the due date, and the source says delivery within 45 days of award (p2) |
| Payment Terms | Prime vendors pay subcontractors within 10 days → partial; a subcontractor clause, not vendor payment terms | Invoice within 10 days of delivery, with required contents → partial |
| Any Additional Documentation Required | IRS W-9 form text (p54, OCR) → partial | null → missed (Mercury Affidavit, Contract Affidavit) |
| MFG for Registration | Resellers submit factory-authorized repair certifications → partial | Affidavit business-type checkbox text → **wrong** |
| Contract or Cooperative to use | Central Texas Purchasing Alliance membership → correct | Desktop, Laptop and Tablet 2015 Master Contract, 060B5400007 → correct |
| Model_no | null → null ok (category RFP) | null → missed (Latitude 5550) |
| Part_no | null → null ok | CC7802 → correct |
| Product | null → missed (laptops, desktops, tablets, monitors, p3) | null → missed (30 × Dell Latitude 5550) |
| contact_info | Jasmine Alzate, JALZATE@dallasisd.org, (972)925-4100 → partial; the phone is the department line, and the citation is p1 (text on p2) | unresolved → missed (Tamaira Hawkins, 410-260-7533) |
| company_name | null → missed (Dallas ISD) | MD State Treasurer's Office → correct |
| Bid Summary | Only the contract-term sentence → partial | Long verbatim copy of PORFP p2 → partial |
| Product Specification | null → missed (FINAL pp. 4–6) | CPU, 16 GB RAM, 256 GB SSD, Windows 11 Pro, 3-year warranty → correct |

| Verdict | Bid1 | Bid2 |
|---|---|---|
| correct | 6 | 5 |
| partial | 6 | 5 |
| wrong | 0 | 2 |
| missed | 5 | 4 |
| null ok | 3 | 4 |

Remaining extraction accuracy issues:

- **Validator too weak for dates:** the validator checks that a value appears in its evidence, not
  that it answers the field. Bid2 Delivery Date (the due date) and MFG for Registration (checkbox
  text) pass for that reason.
- **Missed values:** some values are retrieved but the model still returns null. Bid1 company_name,
  Product and Product Specification; Bid2 Product, Model_no and the affidavits.
- **Notes not validated:** the Bid2 Due Date note invents an original date.
- **Citation page:** one-page citations for chunks that span two pages.
- **Confidence:** `confidence` is a fixed value per status (0.86 / 0.74 / 0.35), not a calibrated
  probability.

## Q&A (`ask-samples`)

```powershell
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_phase7_questions.json --output artifacts\qa_phase7_final
```

The question set has 18 questions, 9 per bid. They were chosen from facts confirmed in the source
and cover these topics:

- deadline, contact, scope, technical, eligibility and commercial;
- addendum changes;
- absent answers;
- two cross-bid isolation probes.

No genuine conflict exists in either bid. Bid1's due date is revised by Addendum 2, which is an
explicit update. The time zones on the Bid1 page agree. So no conflict question could be grounded.

The reviewed examples with citations, locators, validation outcome and trace path are in
`artifacts/qa_examples.json`. The raw responses and per-question traces are in
`artifacts/qa_phase7_final/`.

| # | Bid | Question | Status | Verdict |
|---|---|---|---|---|
| 1 | Bid1 | Final due date after all addenda | answered (Addendum 2 p1) | correct |
| 2 | Bid1 | Buyer and email | answered (FINAL p1–2) | correct |
| 3 | Bid1 | Device types covered | answered (BidNet `#g_103`) | correct |
| 4 | Bid1 | Wireless and Bluetooth standards | answered (pp. 4–6) | partial: overgeneralizes the 802.11AC device |
| 5 | Bid1 | Reseller repair certifications | answered (p3) | correct |
| 6 | Bid1 | Contract term and renewals | answered (p2) | correct |
| 7 | Bid1 | Addendum 1: when quotes are requested | answered (Addendum 1 p1) | correct |
| 8 | Bid1 | Liquidated damages | not_found | correct abstention |
| 9 | Bid1 | PORFP E20P4600040 (a Bid2 id) | not_found | correct isolation |
| 10 | Bid2 | Proposal due date and time | answered (BidNet `#g_249`) | correct; 1 non-verbatim quote discarded |
| 11 | Bid2 | Procurement officer contact | answered (p2) | partial: the email is only in the citation, and the name is missing |
| 12 | Bid2 | Laptop model and quantity | answered (p3) | correct |
| 13 | Bid2 | Processor | answered (specs p1) | correct |
| 14 | Bid2 | Eligibility | answered (p1) | correct |
| 15 | Bid2 | Delivery after award | answered (p2) | correct |
| 16 | Bid2 | Addenda changing the due date | not_found | correct abstention (Bid2 has no addenda) |
| 17 | Bid2 | Liquidated damages | not_found | correct abstention |
| 18 | Bid2 | Jasmine Alzate's email (a Bid1 contact) | not_found | correct isolation |

The final run gave 11 correct cited answers, 2 partially correct, 5 correct abstentions, 0 false
abstentions and 0 wrong answers. Every answered citation is a verbatim span of a passage of the
same bid.

Earlier runs of the same set exposed three Q&A defects, now fixed and tested in
`tests/test_qa_grounding.py`:

1. Some indexed table cells contain a literal `\n` (backslash then n). Quotes copied across those
   cells never matched. The quote locator now treats a literal `\n`, `\r` or `\t` as whitespace,
   which fixed the eligibility question.
2. A claim citing one valid and one non-verbatim quote was rejected entirely. The non-verbatim
   quote is now discarded. The claim must still pass every grounding check against its remaining
   valid quotes. The first attempt still asks for a correction, and the discard is reported in
   `limitations` and `validation.reasons`. This fixed the Bid2 due date.
3. Raw line breaks inside JSON strings made one correction reply unparseable. Parsing is now
   lenient about control characters. Identical repeated claims are reported once.

Gold-labelled set (`evaluation/qa_reviewed.json`, 14 cases), scored with
`scripts/evaluate_qa.py score --samples artifacts\qa_v6\qa_samples.json`:

| Metric | Baseline `artifacts/qa` | Phase 6 `qa_v5` | Phase 7 `qa_v6` |
|---|---|---|---|
| Answer correctness | 12/14 | 9/14 | 9/14 |
| Citation source accuracy | 0.80 | 0.83 | 0.83 |
| False abstention | 1/11 | 5/11 | 5/11 |
| Correct abstention | 3/3 | 3/3 | 3/3 |
| Claim support (automated) | 5/10 | 7/7 | 7/7 |
| Exact quote validity (automated) | 0.90 | 6/7 | 6/7 |

The gold set's false abstentions are on questions phrased differently from the Phase 7 set:
pre-proposal date reformatting, Bid1 contact, Bid2 affidavits, Bid2 due date wording and Bid2 RAM.
Strict grounding still trades recall for precision. The baseline answered more questions, but
half of its claims were unsupported by their quotes.

## Retrieval evaluation

```powershell
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --modes dense,sparse,hybrid,hybrid_rerank --out artifacts\eval\retrieval_phase7.json
```

`verify` passed: 55 cases, 50 answerable, 86 judgments, labels unchanged. The run used the
default rerank depth of 20, and its results match the historical `VERIFICATION.md` table.

| Configuration | Recall@5 | MRR@10 | nDCG@10 | hit@5 | p50 latency (ms) |
|---|---|---|---|---|---|
| dense (BGE-small) | 0.703 | 0.688 | 0.659 | 0.88 | 58 |
| sparse (BM25) | 0.786 | 0.781 | 0.749 | 0.88 | 27 |
| hybrid (RRF) | 0.736 | 0.795 | 0.741 | 0.88 | 44 |
| hybrid + rerank (depth 20) | 0.772 | 0.769 | 0.765 | 0.88 | 1,675 |

Every mode had 0 errors, 0 cross-bid leaks and 0 citation errors. All 3 unknown identifiers
returned no evidence.

Label review status:

- **Automated:** `verify` checks that each judgment's required text is present at its document and
  page or element.
- **Human:** a human has not approved the 55 cases. Items still needing review are listed in the
  dataset's `needs_human_labeling` field and in `evaluation/review_packet/`.

## Offline verification

Run on the final code: `pytest -m "not integration"` (6 integration tests deselected), `ruff check`,
`ruff format --check` and `mypy src/rfp`. The current counts are in the README. These tests use
fake models and constructed search responses, so they verify the implementation, not live
accuracy.

## Limitations

- Model output varies between runs. Repeated live runs of the same questions gave different
  answers.
- Grounding is lexical. A claim built from supported words can still misread its quote.
- The extraction validator checks that evidence contains the value, not that the value answers
  the field (see Bid2 Delivery Date).
- Scanned pages (Bid1 FINAL pp. 54–59) are OCR text without table structure.
- All verdicts here are assistant reviews pending human confirmation.
- No demo video exists. The reproducible demo sequence is in the README.
