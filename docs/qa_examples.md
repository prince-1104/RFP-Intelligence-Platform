# Cited Q&A examples

Status: **pending**. No live model was configured when Phase 6 was implemented, so no real
examples exist. Nothing in this repository should be read as a generated answer.

Generate them with a configured model and the local Qdrant collection running:

```powershell
$env:LLM_PROVIDER = "openai"; $env:LLM_MODEL = "<model>"; $env:LLM_API_KEY = "<key>"
.\.venv\Scripts\python -m rfp.cli ask-samples --questions examples\qa_questions.json --output artifacts\qa
```

`examples/qa_questions.json` holds 14 questions, 7 per bid, including the assignment's examples
(final deadline after addenda, Addendum 2 changes, bid bond, Bid2 affidavits, warranty) and one
question expected to be unanswerable. Cross-bid comparison is out of scope because `ask` is
restricted to one bid.

Outputs (under `artifacts/`, which is gitignored):

- `artifacts/qa/qa_samples.json`: a JSON list of Q&A responses, one per question.
- `artifacts/qa/qa_samples.md`: the same content, readable, with the model name.
- `artifacts/qa/traces/qa_<trace_id>.json`: one trace per question.

Each response looks like this. The values are illustrative, not model output:

```json
{
  "bid_id": "Bid1",
  "question": "What is the final proposal submission deadline after all addenda?",
  "answer": "<answer text>",
  "status": "answered",
  "citations": [
    {
      "evidence_id": "E3",
      "document": "Addendum 1 RFP JA-207652 Student and Staff Computing Devices.pdf",
      "page": 1,
      "html_element_id": null,
      "document_type": "addendum",
      "addendum_number": 1,
      "excerpt": "<verbatim text from the cited passage>"
    }
  ],
  "confidence": null,
  "validation": {"passed": true, "attempts": 1, "checks": [], "reasons": []},
  "trace_id": "<uuid>",
  "model": {"provider": "openai", "model": "<model>"},
  "evidence_count": 10
}
```

Before treating the examples as evidence of quality, review each answer against the cited pages.
Passing the automatic citation check means the quotes exist and the facts appear in them. It does
not prove the answer is the right reading of the documents.
