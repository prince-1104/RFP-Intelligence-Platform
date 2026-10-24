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
| `trace.py` | Ordered steps with timings, tool metadata, and token counts |
| `state.py` | Shared bid, evidence, drafts, validation, retries, and token totals |

Search calls share one client lock. Model calls for separate groups run in parallel up to
`extraction concurrency`. A field that fails validation is retrieved and extracted again.
After the initial attempt plus two retries it stays null, with the rejection in `notes`.
Other fields are kept.

`python -m rfp.cli extract-bid --bid-id Bid1 --output artifacts/extraction` reads loopback
Qdrant only. A remote host or a URL with credentials is refused. Test mode (`--llm-mode test`)
returns "Not found in documents" for every field unless `--fixture` supplies scripted responses.
That mode is for tests. It is not a live extraction.
