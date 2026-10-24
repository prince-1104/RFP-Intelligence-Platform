"""Repeatable Phase 4 smoke test with real FastEmbed models and a local Qdrant server.

Prerequisites: ``docker compose up -d qdrant``, ``python -m rfp.cli init-models`` (one-time model
download) and Phase 3 chunk artifacts (``python examples/create_sample.py`` then parse/chunk, or
the committed ``artifacts/chunks``). Uses a dedicated collection base name and embedding cache so
it never touches other collections. Exits non-zero on any failed check.
"""

import argparse
import json
import sys
from pathlib import Path

from rfp.cli import local_settings
from rfp.schemas.search import RetrievalMode, SearchRequest
from rfp.search.indexing import load_local_artifacts
from rfp.search.service import SearchService
from rfp.search.wiring import local_indexing_service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=Path("artifacts/chunks"))
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--collection", default="rfp_smoke")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/embeddings-smoke"))
    args = parser.parse_args()

    settings = local_settings(args.qdrant_url)
    settings.qdrant_collection = args.collection
    checks: list[dict] = []

    def check(name: str, passed: bool, **detail) -> None:
        checks.append({"check": name, "passed": bool(passed), **detail})

    generations, failures = load_local_artifacts(args.chunks, settings)
    check("artifacts_loaded", generations and not failures, generations=len(generations))
    indexer = local_indexing_service(settings, args.cache_dir)
    first = indexer.index(generations)
    check("first_index_succeeded", first.succeeded, **_totals(first.as_dict()))
    second = indexer.index(generations)
    statuses = {outcome.status for outcome in second.outcomes}
    check("reindex_unchanged", statuses == {"unchanged"}, statuses=sorted(statuses))

    search = SearchService(indexer.runtime, indexer.client, settings)
    for mode in RetrievalMode:
        response = search.search(SearchRequest(query="PX-420", mode=mode, top_k=5))
        hit = next(
            (
                r
                for r in response.results
                if r.citation.relative_path == "specifications.pdf"
                and r.citation.page_start == 1
                and "PX-420" in r.text
            ),
            None,
        )
        check(
            f"identifier_pdf_page1_{mode.value}",
            hit is not None,
            rank=hit.rank if hit else None,
            top=_cite(response.results[0]) if response.results else None,
            timings_ms=response.timings_ms,
        )

    response = search.search(SearchRequest(query="installation and documentation requirements"))
    top = response.results[0] if response.results else None
    check(
        "natural_language_pdf_page2",
        top is not None
        and top.citation.relative_path == "specifications.pdf"
        and top.citation.page_start == 2,
        top=_cite(top) if top else None,
    )

    response = search.search(SearchRequest(query="When was the procurement notice issued?"))
    html = next((r for r in response.results if r.citation.relative_path == "notice.html"), None)
    check(
        "html_null_page_with_locator",
        html is not None and html.citation.page_start is None and html.citation.location_known,
        result=_cite(html) if html else None,
    )

    response = search.search(SearchRequest(query="model ZZ-9999"))
    check(
        "unknown_identifier_no_evidence",
        not response.results and "identifier_evidence_not_found" in response.warnings,
    )

    passed = all(item["passed"] for item in checks)
    print(
        json.dumps(
            {
                "passed": passed,
                "collection": first.collection,
                "profile": first.profile_fingerprint,
                "checks": checks,
            },
            indent=2,
        )
    )
    return 0 if passed else 1


def _totals(report: dict) -> dict:
    keys = ("points", "unique_contents", "cache_hits", "cache_misses", "truncated_inputs")
    return {key: report[key] for key in keys}


def _cite(result) -> dict:
    citation = result.citation
    return {
        "rank": result.rank,
        "file": citation.relative_path,
        "page": citation.page_start,
        "html_locators": [loc.model_dump(exclude_none=True) for loc in citation.html_locators],
        "section": citation.section_path,
        "final_score": result.scores.final_score,
    }


if __name__ == "__main__":
    sys.exit(main())
