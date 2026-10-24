# Extraction agents

Phase 5 adds a bounded extraction workflow on top of `SearchService.search`. It does not
replace ingestion, chunking, or the search index, and it never calls `index-bid`.

The coordinator is a small Python orchestrator rather than LangGraph, CrewAI, or another
framework. `pyproject.toml` does not include those packages, and the workflow is a fixed
graph: retrieve, extract, reconcile, validate, retry at most twice, then write JSON. An
extra framework would not change that control flow. Model calls go through `llm.py` and
use `httpx`, which is already installed. Tests inject a fake model. `extract-bid --llm-mode live`
fails if `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY` are missing. It does not invent values.

| Piece | Responsibility |
| --- | --- |
| `orchestrator.py` | Plans field groups, runs independent groups concurrently, retries failed fields, writes the final state |
| `retrieval.py` | Calls `SearchService.search` with the current bid id on every query |
| `extractors.py` | Identity, logistics, commercial, product, and summary prompts |
| `reconciliation.py` | Records a due-date change only when addendum text states the new date |
| `validator.py` | Checks citations, locators, support in the cited text, and date shape |
| `semantics.py` | Field-meaning rules (a cue in or near the value, rejected value shapes) and grounded notes |
| `trace.py` | Ordered steps with timings, tool metadata, and token counts |
| `state.py` | Shared bid, evidence, drafts, validation, retries, and token totals |

Search calls share one client lock. Model calls for separate groups run in parallel up to
`extraction concurrency`. A field that fails validation is retrieved and extracted again.
After the initial attempt plus two retries it stays null, with the rejection in `notes`.
Other fields are kept.

Each field's query steps are merged round-robin by rank, so secondary queries (addendum-only,
specs-only, label-style sparse) reach the first eight passages shown to the model. The prompt
names the exact JSON keys; reply keys that differ only in case or separators (`term_of_bid`)
are still matched.

The output `confidence` is required by the assignment schema. It is a fixed value per
validation status (accepted 0.86, not found 0.74, unresolved 0.35), not a calibrated
probability, and should be read as a status code.

`python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts/extraction` reads loopback
Qdrant only. A remote host or a URL with credentials is refused. Test mode (`--llm-mode test`)
returns "Not found in documents" for every field unless `--fixture` supplies scripted responses.
That mode is for tests. It is not a live extraction.

## Q&A (`qa.py`, `qa_runner.py`)

`ask` answers one question about one bid. Retrieval uses the same bid-scoped adapter, plus an
addendum-only sparse query for deadline or addendum questions. The model must cite passage ids and
return claims linked to verbatim quotes. `check_reply` rejects unknown ids, quotes that are not a
contiguous span of the passage (`quotes.py`), claims whose details are missing from their own
quotes (`grounding.py`), single-source conflicts, deadline answers that contradict an explicit
addendum revision, and list answers that skip items named in the evidence (`checklist.py`). One
correction is allowed; then unsupported claims are dropped, or the answer is
`Not found in documents`. Q&A answers report no numeric confidence (`confidence` is `null`).
