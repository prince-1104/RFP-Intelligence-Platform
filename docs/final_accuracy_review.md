# Final accuracy review (Phase 9)

Run date 2026-10-24. Final outputs: `artifacts/extraction_v4` (`openai` / `gpt-4o-mini`, local
loopback Qdrant, no re-indexing). Citation checks: `artifacts/eval/extraction_v4_check.json`.

## Who judged what

- **Automated checks** (scripts, deterministic):
  - schema and field order;
  - whether each value's words are on its cited pages (`scripts/verify_extraction.py`);
  - Q&A citation excerpts located on their cited pages;
  - retrieval label grounding (`scripts/evaluate_retrieval.py verify`);
  - secret scan.
- **Model-assisted judgments** (written by the AI assistant that made the code changes): every
  "expected value" and every verdict below. They were re-checked against the parsed source text in
  `artifacts/assessment/<Bid>/parsed`, not copied from the Phase 8 review.
- **Human review: none.** No person has confirmed these verdicts. The open decisions are listed at
  the end of this document.

Verdicts:
- correct: the value states what the source says.
- partial: true but missing a stated detail, or carrying extra text.
- wrong: the value is not what the field asks for.
- missed: the source states a value, but the output has none.
- correctly null: the source states nothing for this field.

"Citation" is the verifier result on the cited pages:
- verbatim: the value appears word for word on the cited page.
- all tokens: every word of the value is on the cited page, though not in one run.
- partial (share): a restatement; the number is the share of value words found. The validator's
  restatement check passed for these.

## Bid Number rule (Bid1 and Bid2)

The assignment specification lists the 20 field names but does not define them. The output
schema allows one string per field and has no slot for a second identifier.

**Bid1 identifiers:**
- The BidNet listing (`#g_90`) gives "Solicitation Number JA-207652", "SOURCING #168884" and
  "Reference Number 00004079100".
- The RFP cover (FINAL p1) says "Request For Proposal 168884 JA-207652".

**Bid2 identifiers:**
- The listing gives "Solicitation Number BPM044557" and "Reference Number 00004079359".
- PORFP p1 gives "PORFP Number: Fixed Price #E20P4600040" and "eMMA Project Number: BPM044557".
- Questions use the subject "QUESTION for Dell Laptop #E20P4600040" (p2).
- Invoices must "Reference the States assigned PORFP number" (p2).

**Rule:** Bid Number is the number the solicitation is listed and filed under. It is enforced
deterministically in `semantics.bid_number_problems` in this order:
1. A listing site's "Reference Number" is never accepted.
2. If the evidence labels a Solicitation, Bid, RFP, RFQ, IFB or ITB number, the value must be one
   of those.
3. Otherwise, if the listing passage and the solicitation documents share an identifier, the value
   must be one of the shared identifiers. A number printed only on a form, or only on the listing,
   is rejected.

Step 3 is needed because the indexed listing chunk lost its labels: it reads
"00004079359 … BPM044557 Dell Laptops w/Extended Warranty". Step 2 therefore cannot fire for
Bid2's listing. Re-indexing would restore the labels, but it is not required for this rule, so it
was not done.

**Aliases:** every other labelled identifier in the Bid Number evidence is added to the notes with
its file and page or HTML element (`semantics.identifier_aliases`). Listing reference numbers are
excluded. The rule produces:

| Bid | Value | Note |
|---|---|---|
| Bid1 | JA-207652 | "The documents also identify this solicitation as SOURCING #168884 (… BidNet Direct.html, #g_90)." |
| Bid2 | BPM044557 | "The documents also identify this solicitation as PORFP Number: #E20P4600040 (PORFP_-_Dell_Laptop_Final.pdf, page 1)." |

Both identifiers stay traceable, and the 20-field contract is unchanged. Choosing BPM044557 over
E20P4600040 is a documented interpretation, not a fact in the source: a reviewer who wants the
agency's form number can read it from the note. Tests:
- `test_bid_number_prefers_the_labelled_solicitation_number`;
- `test_bid_number_must_be_shared_by_the_listing_and_the_solicitation_documents`;
- `test_workflow_keeps_the_solicitation_number_and_cites_the_alias`.

## Bid1 (`artifacts/extraction_v4/Bid1.json`)

| Field | Extracted | Expected (source) | Source | Citation | Verdict | Explanation |
|---|---|---|---|---|---|---|
| Bid Number | JA-207652; alias SOURCING #168884 cited | JA-207652 | Listing #g_90 "Solicitation Number JA-207652"; FINAL p1 | verbatim | correct | Labelled solicitation number; the sourcing number is kept as a cited alias |
| Title | Student and Staff Computing Devices | Same | FINAL p1–2, addenda p1 | verbatim | correct | |
| Due Date | July 9, 2024 at 2:00 PM CST; note: base document stated 27-JUN-2024 14:00:00 | July 9, 2024 2:00 PM CST | Addendum 2 p1 "The new due date for this RFP will be July 9, 2024 at 2:00 PM CST" | verbatim | correct | Note comes from the addendum change record |
| Bid Submission Type | online via our electronic system; note on manual submissions | Online electronic system preferred; manual submissions go to Procurement Services | FINAL p9 | verbatim | correct | |
| Term of Bid | 3 years with 2 renewals of 1 year each; note: total may not exceed 5 years | 3-year term, two successive 1-year extensions, 5 years maximum | FINAL p2 "1.2 Terms Initial Term: 3 Renewal 1: 1 Renewal 2: 1 … not to exceed a total of 5 years(s)" and "LENGTH OF CONTRACT … three (3) year agreement with two (2) successive one (1) year extensions" | partial (0.6) | correct | Faithful restatement; every number is in the passage. The verifier's token share is low because "renewals" and "each" are not verbatim |
| Pre Bid Meeting | 10-JUN-2024 14:00:00 | June 10, 2024, 2:00 PM CST, held via Microsoft Teams | FINAL p2 | verbatim | partial | Date and time correct; the Teams format is in the passage but not in the value |
| Installation | All deliveries and deployments must incorporate white glove services, which include asset decaling, asset reporting, etching, and deployment services. | White-glove deployment services | FINAL p3 | all tokens | correct | |
| Bid Bond Requirement | null | None stated | FINAL p9 only mentions "any … bid bond … requirements as noted elsewhere" | — | correctly null | |
| Delivery Date | Starting in September of 2024 | No fixed delivery date. Addendum 1 says quotes will be requested starting September 2024 | Addendum 1 p1 | verbatim | partial | The closest statement in the source; it describes when quotes start, not when goods are delivered |
| Payment Terms | null (unresolved) | No buyer payment terms are stated | FINAL p51 has only the M/WBE clause on prime vendors paying subcontractors | — | correctly null | The subcontractor clause was rejected three times by the new Payment Terms rule. The output says "unresolved" with that reason rather than "not found" |
| Any Additional Documentation Required | Form 1295 should be submitted with the RFP response. | Form 1295, MWBE forms, W-9, and reseller certifications | Addendum 1 p4; FINAL p33 "MWBE Forms must be completed and attached … W9 Form completed, signed and attached"; FINAL p3 | verbatim | partial | One of four required documents |
| MFG for Registration | Manufacturer or Authorized Reseller Qualifications | No manufacturer is named. Resellers must submit factory-authorized repair and maintenance certifications | FINAL p3 | verbatim | partial | The value is the section heading. The requirement sentence after it is not included |
| Contract or Cooperative to use | Central Texas Purchasing Alliance (CTPA) | Same | FINAL p20 | verbatim | correct | |
| Model_no | null | None. This is a category RFP | Price sheet pp. 34–35 lists tiers, not models | — | correctly null | |
| Part_no | null | None | — | — | correctly null | |
| Product | Ten device tiers, Tier 1 Small Student Chromebook Laptop (Touch) … Display Monitor - Non-Touch | Same ten line items, one each per line | FINAL pp. 34–35 | all tokens | correct | |
| contact_info | ALZATE, JASMINE, JALZATE@dallasisd.org, (972)925-4100 | Buyer Jasmine Alzate, JALZATE@dallasisd.org | FINAL p2 (buyer and email); p1 letterhead (phone) | all tokens | partial | The phone is the Procurement Services department line, not the buyer's |
| company_name | Dallas Independent School District | Same | FINAL p2, p31 | verbatim | correct | |
| Bid Summary | Abstractive: scope, device categories, 3+1+1-year term, multiple awards, questions answered by addendum | Also the July 9, 2024 due date and the electronic submission | FINAL p2; listing #g_103 | partial (0.88) | partial | Written in the model's own words now (it was a copied paragraph in v3); the due date and submission method are missing |
| Product Specification | Tier requirements from pp. 4–6 (resolution, RAM, Wi-Fi, battery, ports, storage, OS, warranty, then later tiers) | Minimum requirements for each tier | FINAL pp. 4–6 | all tokens | partial | Starts partway through Tier 1; Tier 1's processor and display lines are missing |

## Bid2 (`artifacts/extraction_v4/Bid2.json`)

| Field | Extracted | Expected (source) | Source | Citation | Verdict | Explanation |
|---|---|---|---|---|---|---|
| Bid Number | BPM044557; alias "PORFP Number: #E20P4600040" cited to PORFP p1 | BPM044557 under the rule above | Listing #g_240; PORFP p1 | verbatim | correct | The documented rule; E20P4600040 is kept and cited |
| Title | Dell Laptops w/Extended Warranty | Same | Listing "Title Dell Laptops w/Extended Warranty" (#g_240) | verbatim | correct | Was the PORFP form heading in v3. Two extra listing navigation elements are also cited |
| Due Date | 06/10/2024 | 06/10/2024 at 2:00 PM EDT | PORFP p1; listing "Closing Date 06/10/2024 02:00 PM EDT" | verbatim | partial | The time is missing |
| Bid Submission Type | eMaryland Marketplace Advantage (eMMA) e-Procurement system | Same | PORFP p1 | verbatim | correct | |
| Term of Bid | null | None. This is a single purchase order under a master contract | Only a 3-year warranty and 90-day quote validity (p2–p3) | — | correctly null | |
| Pre Bid Meeting | null | None | — | — | correctly null | |
| Installation | null | None; only delivery to the Treasurer's Office | — | — | correctly null | |
| Bid Bond Requirement | null | None | — | — | correctly null | |
| Delivery Date | within 45 days of Award | Same | PORFP p2 item 9 | verbatim | correct | |
| Payment Terms | Invoice(s) shall be submitted within 10 days of delivering the equipment … | No net payment term. Invoicing instructions only | PORFP p2 | verbatim | partial | Describes invoicing, not when the State pays |
| Any Additional Documentation Required | Contract Affidavit, Mercury Affidavit | Mercury Affidavit (required); Contract Affidavit (form in the package); warranty certificate or affidavit upon award; Letter of Authorization if requested | PORFP p2 items 4 and 8, p3; Contract_Affidavit.pdf; Mercury_Affidavit.pdf | all tokens | correct | The real required forms; no checkbox text. It cites the forms rather than the p2 requirement sentence, and leaves out the award-stage certificate and the on-request Letter of Authorization |
| MFG for Registration | Dell | Must be an authorized reseller for Dell | PORFP p2 item 4 | verbatim | correct | |
| Contract or Cooperative to use | Desktop, Laptop and Tablet 2015 Master Contract, 060B5400007 | Same | PORFP p1 | verbatim | correct | |
| Model_no | Dell Latitude 5550 | Same | PORFP p1/p3, specs p1 | verbatim | correct | |
| Part_no | SI# CC7802 | SI# CC7802 (state item number) or 210-BLYZ (Dell base SKU) | PORFP p3 product table; specs p1 "SI# CC7802 … SKU Description 210-BLYZ" | verbatim | correct | Both are defensible; v3 chose 210-BLYZ. Needs a human decision |
| Product | laptops to accommodate MD529 employees; note "Possibly incomplete: Product omits the quantity …" | 30 Dell Latitude 5550 laptops and 30 Dell Thunderbolt 4 Docks (WD22TB4) | PORFP p3 | verbatim | partial | The model never named the table items, even with the omission as the retry reason. The value is kept and flagged as incomplete |
| contact_info | Tamaira Hawkins, thawkins@treasurer.state.md.us, 410-260-7533 | Same | PORFP p3 | all tokens (1.0) | correct | The PDF text reads "md .us". The validator and the verifier now rejoin it, and the source text and locator are unchanged |
| company_name | MD State Treasurer's Office | Same | PORFP p3 | verbatim | correct | |
| Bid Summary | Questions due 06/01/2024; submission through eMMA only; small business reserve | Also 30 laptops and docks, the 06/10/2024 due date, and delivery within 45 days | PORFP pp. 1–2 | partial (0.9) | partial | No longer a copied passage; the product and quantity are missing |
| Product Specification | 1. Dell Limited Hardware Warranty Extended for all machines purchased - 3 Years | CPU, RAM, storage, OS and 3-year warranty | Specs p1; PORFP p3 | verbatim | partial | The model's first two answers mixed unsupported parts into the value. The third returned only the warranty. The same-code repeat run returned the full CPU/RAM/storage line |

## Totals

Phase 8 output was re-scored with the same criteria, which are stricter on Pre Bid time and Due
Date time. Under these criteria Bid1 v3 Payment Terms is "wrong" (Phase 8 called it partial).

| Verdict | Bid1 v3 | Bid1 v4 | Bid2 v3 | Bid2 v4 |
|---|---|---|---|---|
| correct | 7 | 9 | 10 | 11 |
| partial | 8 | 7 | 5 | 5 |
| wrong | 1 | 0 | 0 | 0 |
| missed | 1 | 0 | 1 | 0 |
| correctly null | 3 | 4 | 4 | 4 |

The repeat run with the same code (`artifacts/extraction_v4_repeat`) would score:
- Bid1: 8 correct and 8 partial. Installation became the partial "Yes, deployment services must
  include installation."
- Bid2: 12 correct and 4 partial. Product Specification was complete.

## Gap investigations

| Gap | Passage | Failure class | Fix | Test |
|---|---|---|---|---|
| Bid2 affidavits | PORFP p2 "must provide a Mercury Affidavit"; Contract_Affidavit.pdf | Semantic validation. "Contract Affidavit and Mercury Affidavit." failed because the support tokenizer kept the trailing period ("affidavit.") | Trailing punctuation is stripped from support tokens. Documents also gets a checkbox-instruction rejection and a definition saying a tick-box instruction is not a document | `test_documentation_list_with_trailing_period_and_checkbox_instruction` |
| Bid1 MFG | FINAL p3 "Manufacturer or Authorized Reseller Qualifications For proposing vendors who are resellers…" | Prompt context. The definition asked only for a named manufacturer, and Bid1 names none, so the model returned null | The definition now covers the reseller qualification when no manufacturer is named. Result: partial (the heading only) | `test_mfg_accepts_a_reseller_qualification_without_a_named_manufacturer` |
| Bid1 Term | FINAL p2 | Semantic validation and generation. The faithful paraphrase "3 years with 2 renewals of 1 year each" was rejected as unsupported. The "copy word for word" retry then truncated the value to "three (3) year agreement" | Descriptive fields accept a faithful restatement: every number on the cited page, and 80% of content words by their first six letters. Connectives such as "each" do not count. Term must keep renewal or extension options stated next to it. The retry hint for descriptive fields asks for the complete value | `test_term_keeps_renewals_and_accepts_a_faithful_paraphrase` |
| Bid2 Title | Listing #g_240 | Generation, with a parsing contributor. Every PORFP page starts with the form heading, and the listing chunk has lost its "Title" label | A heading made only of document-type words is rejected. The retry shows the rejected value and adds the field's first query restricted to `bid_page` | `test_title_rejects_a_document_type_heading`, `test_retry_prompt_shows_the_rejected_value_and_title_retry_searches_the_listing` |
| Bid2 Product | PORFP p3 product table | Generation. The table is serialized as JSON-like cells, and the model returns the prose phrase | Product must carry a standalone quantity when the cited text has a Qty column. An incomplete but supported value is kept with a "Possibly incomplete" note if the retries do not improve on it. Still partial | `test_product_must_carry_the_quantity_a_product_table_states`, `test_product_quantity_ignores_digits_inside_codes`, `test_incomplete_supported_value_is_kept_with_a_note_when_retries_do_not_improve` |
| Bid2 Summary | PORFP p2 | Prompt context. The shared prompt told every group "Copy values verbatim" | The summary prompt asks for its own sentences. A summary that copies one passage of more than 25 words is rejected | `test_summary_must_not_copy_a_passage_and_its_prompt_asks_for_own_sentences` |
| Bid2 contact email | PORFP p3 "thawkins@treasurer.state.md .us" | Parsing (PDF text), then the verifier's support check | `join_split_emails` joins only a break before a dot, or after a dot when the rest is a dotted lowercase domain. A sentence-ending address is never merged. It is used by the validator, the semantic windows and the verifier | `test_split_email_domains_are_joined_only_when_unambiguous`, `test_verifier_scores_a_split_email_as_verbatim` |
| Bid1 Payment Terms (found in this review) | FINAL p51 | Semantic validation. The pay/invoice cue matched the M/WBE subcontractor clause | A vendor-pays-subcontractor clause is rejected | `test_payment_terms_reject_a_subcontractor_payment_clause` |
| Bid2 Model_no lost in one run (found in this review) | PORFP p3 | Citation mapping. The model copied the value from a shown passage but cited another id; the single-field retry no longer showed that passage | A value of at least 6 characters, found verbatim in a passage shown in the same prompt, is cited to that passage. The trace records "citation repaired" | `test_verbatim_value_with_a_wrong_citation_is_pointed_at_the_shown_passage` |
| Bid2 Product Specification with "Not found in documents" inside (found in this review) | Specs p1 | Parsing of the model reply. Object values were joined with their placeholders | Placeholder parts are dropped when an object or list reply is joined | `test_placeholder_parts_of_an_object_reply_are_dropped` |

## Run-to-run variation

The Phase 8 runs (`extraction_v3_first_attempt` … `_fourth_attempt`, `extraction_v3`) were made
with different code: each attempt fixed something. Only 7 of 20 fields matched across all five
runs for each bid. That spread mostly measures code changes, not model nondeterminism.

To measure model variation, Phase 9 ran the same code twice, twice over:
- `extraction_v4_third_attempt` and `_third_attempt_repeat`;
- `extraction_v4` and `extraction_v4_repeat`.

| Pair | Bid1 identical values | Bid2 identical values | First-round searches identical |
|---|---|---|---|
| third attempt / repeat | 16/20 | 16/20 | 31/31 per bid |
| final / repeat | 15/20 | 19/20 | 31/31 per bid |

**Why values vary:**
- Retrieval, ordering, deduplication and truncation were identical between runs, so they are not
  the cause.
- The model returns different wording at temperature 0 with a fixed seed. OpenAI documents the
  seed as best-effort only.
- A different first answer triggers a different retry path, which then sees narrower evidence.

Most differing fields are wording variants of the same fact:
- Bid1 Term, both correct.
- Bid1 Delivery Date, both partial.
- Bid1 Contract, differing only in letter case.
- Bid2 Documents, a comma versus "and".
- Bid2 MFG: "Dell" versus "The Master Contractor must be an authorized reseller for Dell."

The substantive differences are:
- Bid2 Product Specification (warranty only versus the full line);
- Bid1 Installation (complete versus a weaker paraphrase);
- in the third-attempt pair, Bid2 Model_no. That pair led to the citation repair.

**Stabilizing changes, each kept within the existing retry bound of 2:**
- JSON object mode and a fixed seed, for extraction and critic requests on the `openai` provider
  only. Q&A requests and other OpenAI-compatible providers are unchanged.
- The rejected value is shown on retry, and the trace records it, so a retry does not silently
  repeat it.
- A supported value rejected only as incomplete is kept and flagged if no retry does better. A
  value of the wrong kind is never kept.
- Citation repair for verbatim values, and placeholder removal in object replies.
- Retries were not raised and no tokens were added.

Same-code agreement is 15–19/20. The 7/20 for the Phase 8 runs is not a baseline for this figure,
because those runs used different code. No same-code pair was run before these changes, so their
effect on variation is not measured, and some variation remains.

## Q&A and retrieval evidence check

The answers and stored datasets were re-read and not rerun. No questions or labels were changed.

**Q&A, 18-question set (`artifacts/qa_phase8`):**
- Automated: all 26 citation excerpts are verbatim on their cited page or element.
- Model-assisted verdicts:
  - Correct: questions 0, 1, 2, 4, 5, 6, 9, 11, 12, 13 and 14.
  - Partial, question 3: the answer leaves out the tablet's 802.11AC and Bluetooth 4.2 line,
    which it does cite.
  - Partial, question 10: the answer says "can be contacted via email" but does not give the name
    or address shown in its own citation.
  - Correct abstentions, questions 7 and 16: no liquidated damages appear in either bid's parsed
    text.
  - Correct abstention, question 15: Bid2 has no addendum; "Addendums" appears only as listing
    tabs.
  - Cross-bid probes, questions 8 and 17: correctly abstained. E20P4600040 belongs to Bid2, and
    Jasmine Alzate to Bid1.

**Q&A gold set (`artifacts/eval/qa_v7.json`):** 9/14. All five failures are false abstentions,
and their labels are correct:
- Bid1 pre-proposal: claim grounding rejected "June 10, 2024" against a quote reading
  "06/10/2024". The two date formats are not treated as equal.
- Bid1 contact: "Procurement Services" was stated in a neighbouring quote, not in the linked one.
- Bid2 due date: the quote was not a verbatim span.
- Bid2 affidavits and Bid2 RAM: the model abstained.

These are open Q&A defects, not label errors. They were not changed in this phase.

**Retrieval (`evaluation/assessment_retrieval.json`):**
- Automated: `verify` is OK for 55 cases and 86 judgments.
- Model-assisted: a sample of 16 cases, two of each kind, was read against the expected passages:
  - b1-id-solicitation, b1-id-sourcing;
  - b1-final-deadline, b1-original-due-date, b1-deadline-extension;
  - b1-pre-proposal, b1-contract-term, b1-warranty-minimum, b1-repair-turnaround;
  - b1-form-1295, b1-tier3-processor;
  - b1-unknown-id, b1-unanswerable-bid-bond;
  - b2-mercury-required, b2-unknown-id, b2-unanswerable-liquidated-damages.

  Every judged passage answers its query. The listing times agree with the RFP once converted
  (2:00 PM CST equals 03:00 PM EDT).

**Incorrect labels found: none** in the sample. The rest of the dataset was not read by hand.

## Remaining errors and root causes

| Field | Status | Root cause |
|---|---|---|
| Bid1 Documents | partial | Generation: the model picks one of four forms. The MWBE and W-9 rows are in a table on p33 |
| Bid1 MFG | partial | Generation: the heading is chosen rather than the sentence after it. The heading passes the manufacturer cue |
| Bid1 Product Specification | partial | Retrieval and chunking: Tier 1's processor and display lines are in a chunk that is not cited |
| Bid1 contact phone | partial | Source: only a department phone is printed |
| Bid1 Pre Bid, Bid2 Due Date | partial | Generation: the value keeps the date and time but drops the format (Teams) or the time |
| Bid1 Delivery, Bid2 Payment | partial | Source: neither bid states the asked-for detail directly |
| Bid2 Product | partial, flagged | Generation from a JSON-serialized table |
| Bid2 Product Specification | partial in v4, correct in the repeat | Provider nondeterminism |
| Both summaries | partial | Generation: the summary evidence is one or two passages, so the due date and products are often left out |
| Q&A gold false abstentions (5) | open | Claim grounding does not equate date formats or credit a label stated in a neighbouring quote; model abstentions |

## Tasks for a human reviewer

1. Confirm the Bid Number interpretation (BPM044557, with E20P4600040 as the cited alias) and the
   Part_no choice (SI# CC7802 or 210-BLYZ).
2. Confirm or overturn every model-assisted verdict above, especially the partial ones.
3. Decide whether "Possibly incomplete" values (Bid2 Product) should be shown to users or held
   back.
4. Read the rest of the retrieval labels (39 cases not sampled) and the Q&A gold answers.
5. Decide whether a re-index is worth it to restore the listing field labels. It would make
   Title and Bid Number evidence explicit.
