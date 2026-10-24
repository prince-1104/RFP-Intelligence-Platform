# Live end-to-end validation (Phases 7, 8 and 9)

Run date 2026-10-24. Every result below comes from a live model call (`openai` / `gpt-4o-mini`,
configured through the existing `build_live_model()` client) against the local loopback Qdrant
collection that already held both bids. Nothing was re-indexed, and no remote service other than
the configured model provider was contacted. Offline test results are reported separately at the end.

The accuracy verdicts are an assistant review of each value against the parsed source text in
`artifacts/assessment/<Bid>/parsed`. A human reviewer has not confirmed them yet. Automated
checks (schema, citation support, secrets) are listed separately from these verdicts.

## Phase 9: evidence-based review and final corrections

The per-field review, the Bid Number rule, the gap investigations, the variation study and the
Q&A and retrieval spot checks are in [`final_accuracy_review.md`](final_accuracy_review.md).

```powershell
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts\extraction_v4 --trace-dir artifacts\extraction_v4\traces
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid2 --output artifacts\extraction_v4 --trace-dir artifacts\extraction_v4\traces
.\.venv\Scripts\python scripts\verify_extraction.py --outputs artifacts\extraction_v4 --traces artifacts\extraction_v4\traces --out artifacts\eval\extraction_v4_check.json
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify
```

The same two extraction commands wrote `artifacts/extraction_v4_repeat` from the same code to
measure variation. Earlier Phase 9 runs are kept:

| Folder | What it shows |
|---|---|
| `extraction_v4_first_attempt` | First Phase 9 code. Bid2 Product passed because of "MD529", the Bid2 alias was missing, and Bid1 Payment kept the subcontractor clause |
| `extraction_v4_second_attempt` | Bid1 Term and Bid2 Product became unresolved, and Bid2 Title was null, because retries repeated the rejected value |
| `extraction_v4_third_attempt` and `_repeat` | Same-code pair. The Bid2 Model_no citation slip appeared in one run only |

The Phase 7 (`extraction_v2`) and Phase 8 (`extraction_v3*`) folders are unchanged.

| Run | Bid | Accepted | Not found | Unresolved | Model calls | Tokens in / out |
|---|---|---|---|---|---|---|
| Phase 9 (`extraction_v4`) | Bid1 | 16 | 3 | 1 | 26 | 41,983 / 2,653 |
| Phase 9 | Bid2 | 16 | 4 | 0 | 25 | 42,454 / 2,236 |

Bid1's unresolved field is Payment Terms. The only candidate was the clause on vendors paying
their subcontractors, which the new rule rejects. The documents state no buyer payment terms.

Model-assisted re-score (not human-reviewed) using the Phase 9 criteria:

| Verdict | Bid1 v3 | Bid1 v4 | Bid2 v3 | Bid2 v4 |
|---|---|---|---|---|
| correct | 7 | 9 | 10 | 11 |
| partial | 8 | 7 | 5 | 5 |
| wrong | 1 | 0 | 0 | 0 |
| missed | 1 | 0 | 1 | 0 |
| correctly null | 3 | 4 | 4 | 4 |

Automated checks (`artifacts/eval/extraction_v4_check.json`):
- both outputs are schema-valid with the 20 names in order;
- no source comes from the other bid;
- both traces are `completed` with retries of at most 2;
- of the 32 accepted values, 23 are verbatim on their cited pages and 6 more have every token
  there. The other 3 are restatements the validator accepted: Bid1 Term (token share 0.6) and the
  two summaries (0.88 and 0.9);
- the Bid2 contact email now scores 1.0;
- a secret scan of 331 artifact and doc files found no leaks.

Code changes, all general and with no bid values in production logic:
- the Bid Number rule and cited aliases;
- restatement support for descriptive fields;
- omission checks (Term renewals, Product quantity);
- rejection of document-type headings, copied summaries and subcontractor payment clauses;
- split-email joining;
- citation repair for verbatim values;
- placeholder removal in object replies;
- the rejected value shown in retry prompts and traces;
- incomplete values kept with a flag;
- JSON mode and a seed for OpenAI extraction calls.

Q&A and retrieval were not rerun. Their code paths are unchanged: Q&A requests do not use the new
JSON-mode flag.

## Phase 8: field-meaning validation and recovered fields

```powershell
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts\extraction_v3 --trace-dir artifacts\extraction_v3\traces
.\.venv\Scripts\python -m rfp.cli extract-bid --bid-id Bid2 --output artifacts\extraction_v3 --trace-dir artifacts\extraction_v3\traces
.\.venv\Scripts\python scripts\verify_extraction.py --outputs artifacts\extraction_v3 --out artifacts\eval\extraction_v3_check.json
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_phase7_questions.json --output artifacts\qa_phase8
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_questions.json --output artifacts\qa_v7
.\.venv\Scripts\python scripts\evaluate_qa.py score --samples artifacts\qa_v7\qa_samples.json --out artifacts\eval\qa_v7.json
.\.venv\Scripts\python scripts\evaluate_retrieval.py verify
.\.venv\Scripts\python scripts\evaluate_retrieval.py run --modes dense,sparse,hybrid,hybrid_rerank --out artifacts\eval\retrieval_phase8.json
```

| Run | Bid | Accepted | Not found | Unresolved | Model calls | Tokens in / out |
|---|---|---|---|---|---|---|
| Phase 7 (`extraction_v2`) | Bid1 | 12 | 8 | 0 | 18 | 34,795 / 1,637 |
| Phase 7 | Bid2 | 12 | 7 | 1 | 23 | 40,591 / 2,399 |
| Phase 8 (`extraction_v3`) | Bid1 | 16 | 4 | 0 | 26 | 42,554 / 3,003 |
| Phase 8 | Bid2 | 15 | 5 | 0 | 22 | 37,385 / 1,961 |

Four intermediate runs are kept in `artifacts/extraction_v3_first_attempt` through
`_fourth_attempt`. Each exposed a defect that was fixed before the next run. The defects are
listed under "Defects found during the re-runs" below. `artifacts/extraction_v2` is unchanged.

### Field-meaning rules

`src/rfp/agents/semantics.py` adds deterministic rules that run inside `validate_field` after the
support check. A failed rule is an ordinary rejection reason, so the existing bounded retry (at
most 2) applies. If the retries also fail, the field becomes unresolved with no value. No
per-field LLM call was added. Confidence semantics are unchanged.

A *cue* must appear in the value itself or within 150 characters before or 80 characters after
the value in the cited passage. When the whole value is not in the passage, its dates, emails,
phones and distinctive words are used to find it.

| Field | Rule |
|---|---|
| Due Date | Cue: due, deadline, closing, submit, receive, no later than, opening |
| Delivery Date | Cue: deliver (not "deliverable"), ship, lead time, arrive. A nearby due or closing label is reported as belonging to another field. A relative schedule ("within 45 days of award") counts as a date |
| Pre Bid Meeting | Cue: pre-bid, pre-proposal, conference, meeting, site visit |
| Bid Submission Type | The value itself must name a channel: online, portal, electronic, email, upload, mail, hard copy, envelope, system |
| Term of Bid | Needs a number or number word, and a term, renewal or period cue |
| Installation, Bid Bond, Payment Terms, Contract or Cooperative | Topic cues (install, deploy, setup; bond, security; pay, invoice; contract, cooperative, alliance, agreement) |
| Any Additional Documentation Required | The value must name a document type (affidavit, form, certificate, attachment, …) |
| MFG for Registration | Checkbox glyphs and "check applicable box" are rejected. A sentence must itself state the manufacturer or authorization requirement; a bare name of up to four words may rely on the passage around it |
| contact_info | Needs an email or phone, with a contact cue (buyer, contact, procurement, officer, POC, questions) nearby |
| company_name | Needs words and an organization cue (district, office, department, agency, …). It may not be an email or phone |
| Bid Number, Model_no, Part_no | Must contain a digit and may not be a date, phone or email. After each round, Model_no or Part_no is rejected if it contains the accepted Bid Number |
| Product Specification | The value must name a technical term (GB, GHz, processor, memory, display, battery, warranty, …) |

### Incorrect fields: root cause and fix

| Field | Phase 7 value | Root cause | Fix | Phase 8 value |
|---|---|---|---|---|
| Bid2 Delivery Date | 06/10/2024 (wrong) | The validator checked that the value was in the passage, not what it meant. The date sits under a "Due Date" column in the PORFP p3 product table | Delivery cue rule. A relative schedule is accepted as a date | within 45 days of Award (p2) |
| Bid2 MFG for Registration | Affidavit checkbox text (wrong) | Same validator gap | Checkbox rejection and manufacturer cue rule | Dell (p2: "must be an authorized reseller for Dell") |
| Bid2 Bid Submission Type | "Sealed Proposals process under COMAR 21.05.03" (partial) | A procurement method was accepted as a channel | The value must name a channel. The field definition says a regulation name is not a submission type | eMaryland Marketplace Advantage (eMMA) e-Procurement system (p1) |
| Bid1 Model_no (seen in an intermediate run) | JA-207652 | The solicitation number is printed on every price-sheet page | Reject product identifiers that repeat the accepted Bid Number | null |

### Missed fields: root cause and fix

| Field | Source passage | Failure point | Fix |
|---|---|---|---|
| Bid2 Any Additional Documentation Required | PORFP p2 (Mercury Affidavit), Contract_Affidavit.pdf | Retrieval query. The catalog query contained "W-9", which the search engine treats as an identifier. Bid2 has no W-9, so every search returned nothing (`identifier_evidence_not_found`) | Identifier-free queries. A test asserts no catalog query, including retry variants, contains an identifier |
| Bid2 Model_no, Product | PORFP p3, specs p1 | Extraction context. The model cited an id shown under a sibling field in the same prompt, which was rejected as "Fabricated citation" | A field may cite any passage shown in the same request. Ids never shown remain fabricated |
| Bid2 contact_info | PORFP p3 | Parsing of the model reply. Retries returned ids as integers (`[5, 7]`), and once the value as a JSON object; both were dropped. The PDF text also splits the email ("md .us"), so a correct email failed the token check | Integer and string ids map to the field's ids, and object values are joined. Dotted tokens are also compared with whitespace removed |
| Bid1 company_name | FINAL p2: "The Dallas Independent School District … is soliciting offers" | Retrieval query. The query did not reach that sentence, and the model cited unrelated clauses | Added the step "organization soliciting offers or proposals" (rank 1 for Bid1, no change for Bid2) |
| Bid1 Bid Submission Type, Bid2 Bid Submission Type, Bid2 MFG, Bid2 Product | FINAL p9; PORFP pp. 1–3 | Extraction context. Passages were cut to their first 700 characters. The answers sit at characters 821 to 1,324, so the model never saw them. The critic had the same cut and rejected correct values | The prompt and critic show the 700-character window that best matches the field's query terms (critic: the value's words). The validator still checks the full passage |
| Bid1 Product, Product Specification, Installation | FINAL pp. 3–7, 34–35 | Extraction context. No field definitions were shown, and the model returned null for category RFPs | Each field block now has a one-line definition (`FIELD_DEFINITIONS`) |
| Bid2 Bid Summary (unresolved in an intermediate run) | PORFP p2 | Validator. Every summary word had to appear in the cited text, which an abstractive summary cannot meet | For Bid Summary only, 80% of content words must come from the cited text. Every number must still appear in the evidence |

The model returns an unsupported paraphrase on retries often enough that the retry prompt now
adds "copy the value word for word" after a "does not support" rejection.

Notes are grounded in `semantics.grounded_notes`. On accepted values, sentences that claim the
value is absent are dropped. So are sentences about an original, previous or revised value when
no `AddendumChange` is recorded, and sentences with a date not in the cited passage. When an
addendum change is recorded, the note is written from that record. The invented "Original due
date was 06/01/2024" on Bid2 is gone. Bid1's note now reads "Addendum 2 states July 9, 2024 at
2:00 PM CST; the base document stated 27-JUN-2024 14:00:00."

### Citation page locators

A chunk that crosses pages keeps its first page as `page`. `SourceRef.spans` now holds per-page
character ranges of the chunk text, built from the existing `source_segments`. Chunk text is the
source pieces joined by a blank line. The map is used only when the segment lengths add up to the
text length exactly; otherwise behavior is unchanged. Extraction sources list each page that holds
the value, and Q&A citations narrow `page`/`page_end` to the quoted span. Identifiers, source
text and page labels are unchanged.

| Citation | Phase 7 | Phase 8 |
|---|---|---|
| Bid1 Pre Bid Meeting `10-JUN-2024 14:00:00` | FINAL p1 (text on p2) | FINAL p2 |
| Bid1 contact_info | FINAL p1 only | FINAL p1 (department phone, letterhead) and p2 (buyer name and email) |
| Q&A Bid1 buyer question | FINAL pp. 1–2 | FINAL p2 |

`verify_extraction.py` now finds all 16 accepted Bid1 values supported on their cited pages. In
Phase 7 there were two exceptions.

### Defects found during the re-runs

1. First run: the prompt said "exact bracketed ids", and the model returned `"[due-date-3]"` as a
   string. Bid2 dropped to 6 accepted. The wording was reverted, and string id lists are parsed.
2. Second run: a relaxed MFG rule accepted an unrelated Bid1 sentence because "factory-authorized"
   was nearby, and Bid1 Model_no repeated the Bid Number. Both are now rejected.
3. Third run: the critic rejected correct values whose evidence was past its 700-character cut.
4. Fourth run: paraphrases (Bid1 Term, Bid2 affidavits) were rejected and the retries gave up.

### Field review (`artifacts/extraction_v3`)

Same verdict definitions as Phase 7 (below).

| Field | Bid1 value → verdict | Bid2 value → verdict |
|---|---|---|
| Bid Number | JA-207652 → correct | BPM044557 → correct; labelled "Solicitation Number" on the listing and "eMMA Project Number" in the PORFP. The PORFP's own number E20P4600040 is an alternative (needs human decision) |
| Title | Student and Staff Computing Devices → correct | PORFP form header → partial (unchanged) |
| Due Date | July 9, 2024 at 2:00 PM CST, note from the addendum record → correct | 06/10/2024 → correct; the invented note is gone |
| Bid Submission Type | "online via our electronic system" (p9), note on manual sealed submissions → correct (was missed) | eMMA e-Procurement system (p1), note "not accepted by email, fax, U.S. Mail, or hand delivery" → correct (was partial) |
| Term of Bid | "three (3) year agreement" → partial; the renewals are missing (was correct) | null → null ok |
| Pre Bid Meeting | 10-JUN-2024 14:00:00, p2 → correct; citation fixed | null → null ok |
| Installation | "deployment and setup" (p7) → partial; a fragment of the deployment services list (was missed) | null → null ok |
| Bid Bond Requirement | null → null ok | null → null ok |
| Delivery Date | September 2024 → partial (unchanged) | within 45 days of Award → correct (was wrong) |
| Payment Terms | Subcontractor payment clause → partial (unchanged) | Invoice within 10 days → partial (unchanged) |
| Any Additional Documentation Required | "Form 1295 should be submitted with the RFP response." → partial; one of several forms | null → missed (unchanged). The affidavits were found in the second run and lost to a paraphrase in the final run |
| MFG for Registration | null → missed (was partial). The rule correctly rejected an unrelated sentence, and the retry returned null | Dell → correct (was wrong) |
| Contract or Cooperative to use | Central Texas Purchasing Alliance (CTPA) → correct | 2015 Master Contract, 060B5400007 → correct |
| Model_no | null → null ok | Dell Latitude 5550 → correct (was missed) |
| Part_no | null → null ok | 210-BLYZ (SKU of the Latitude 5550 base, specs p1) → correct |
| Product | List of the ten device tiers (pp. 34–35) → correct (was missed) | laptops → partial; no model or quantity (was missed) |
| contact_info | ALZATE, JASMINE, JALZATE@dallasisd.org, (972)925-4100 → partial; the phone is the department line. Citations fixed | Tamaira Hawkins, thawkins@…, 410-260-7533 → correct (was missed) |
| company_name | Dallas Independent School District → correct (was missed) | MD State Treasurer's Office → correct |
| Bid Summary | Scope, device categories and term (p2) → partial; no dates or submission method (improved) | Verbatim copy of PORFP p2 → partial (unchanged) |
| Product Specification | Tier requirements from pp. 4–6 → partial; starts partway through the first tier (was missed) | CPU, RAM, storage, OS, warranty → correct |

| Verdict | Bid1 Phase 7 | Bid1 Phase 8 | Bid2 Phase 7 | Bid2 Phase 8 |
|---|---|---|---|---|
| correct | 6 | 8 | 5 | 11 |
| partial | 6 | 8 | 5 | 4 |
| wrong | 0 | 0 | 2 | 0 |
| missed | 5 | 1 | 4 | 1 |
| null ok | 3 | 3 | 4 | 4 |

Two Bid1 fields are worse than in Phase 7: Term of Bid (correct → partial) and MFG for
Registration (partial → missed). Model output still varies between runs. The second v3 run had
the Bid2 affidavits correct; the final run did not.

### Automated checks and regressions

- `artifacts/eval/extraction_v3_check.json`:
  - both outputs are schema-valid with the 20 names in order, and no source comes from the other bid;
  - every trace is `completed`, retries are at most 1, and no secrets were found;
  - 29 of 31 accepted values are verbatim or token-complete on their cited pages. Bid2 contact_info
    scores 0.75 because the verifier does not rejoin the split email domain, and the Bid2 summary
    scores 0.99.
- Q&A, 18-question set (`artifacts/qa_phase8`): 11 correct, 2 partial, 5 correct abstentions,
  0 wrong. This matches Phase 7. The two partials are the same as before.
- Q&A gold set (`artifacts/eval/qa_v7.json`):
  - answer correctness 9/14, citation source accuracy 0.83 and false abstention 5/11;
  - claim support 7/7 and exact quote validity 6/7.

  These are identical to Phase 7.
- Retrieval (`artifacts/eval/retrieval_phase8.json`): `verify` OK; Recall@5, MRR@10, nDCG@10 and
  hit@5 are identical to Phase 7 in all four modes, with 0 errors, leaks and citation errors.
  Labels and questions were not changed.
- Secret scan of every new artifact folder (164 files): no leaks.
- Offline: `pytest -m "not integration"` gave 365 passed. `ruff check`, `ruff format --check` and
  `mypy src/rfp` are clean. New tests are in `tests/test_semantics.py`.

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
  answers, and repeated extraction runs gained and lost individual fields.
- Grounding is lexical. A claim built from supported words can still misread its quote.
- Field-meaning rules are keyword cues near the value. They catch a value of the wrong kind
  (a due date as a delivery date, checkbox text, a regulation as a channel). They cannot judge
  completeness (Bid1 Term, Bid2 Product) or pick between two valid identifiers (Bid2 Bid Number).
- Phase 7 sections below describe `extraction_v2`; its defects listed there are addressed in
  Phase 8 above.
- Scanned pages (Bid1 FINAL pp. 54–59) are OCR text without table structure.
- All verdicts here are assistant reviews pending human confirmation.
- No demo video exists. The reproducible demo sequence is in the README.
