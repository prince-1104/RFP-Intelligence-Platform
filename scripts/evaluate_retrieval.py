"""Labeled retrieval evaluation against LOCAL artifacts and LOCAL Qdrant only.

    python scripts/evaluate_retrieval.py verify [--dataset ...] [--parsed ROOT] [--chunks ROOT]
    python scripts/evaluate_retrieval.py run [--modes dense,sparse,hybrid,hybrid_rerank]
                                             [--rerank-depth N] [--out report.json]
                                             [--collection BASE]

``verify`` proves every judgment is grounded: its text occurs on the declared parsed PDF page (or
HTML element) and in at least one active chunk occurrence, and unknown identifiers occur nowhere.
``run`` searches each case with bid-scoped filters and reports Recall@5, MRR@10 and nDCG@10.
"""

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from local_guard import ROOT, local_only_settings

from rfp.cli import collection_name
from rfp.evaluation.retrieval import (
    CaseResult,
    EvalCase,
    evaluate_case,
    load_dataset,
    normalize_text,
    snippet_pages,
    summarize,
)
from rfp.schemas.chunks import ChunkingResult
from rfp.schemas.parsed import ParsedDocument
from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest

DATASET = ROOT / "evaluation" / "assessment_retrieval.json"
MODES = ["dense", "sparse", "hybrid", "hybrid_rerank"]


def load_chunks(root: Path) -> dict[str, list]:
    by_bid: dict[str, list] = defaultdict(list)
    for path in sorted(root.glob("*/chunks/*.json")):
        if path.name == "summary.json":
            continue
        result = ChunkingResult.model_validate_json(path.read_bytes())
        by_bid[path.parent.parent.name].extend(result.occurrences)
    return by_bid


def load_parsed(root: Path) -> dict[tuple[str, str], ParsedDocument]:
    documents = {}
    for path in sorted(root.glob("*/parsed/*.json")):
        if path.name == "summary.json":
            continue
        document = ParsedDocument.model_validate_json(path.read_bytes())
        documents[(path.parent.parent.name, document.relative_path)] = document
    return documents


def occurrence_matches(judgment, occurrence) -> bool:
    if occurrence.relative_path != judgment.document:
        return False
    text = normalize_text(occurrence.normalized_text)
    if not all(normalize_text(n) in text for n in judgment.text):
        return False
    if judgment.page is not None:
        start = occurrence.page_start or 0
        return start <= judgment.page <= (occurrence.page_end or start)
    if judgment.html_element_id is not None:
        return any(
            s.source_locator.element_id == judgment.html_element_id
            for s in occurrence.source_segments
        )
    return True


def verify(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    chunks = load_chunks(args.chunks)
    parsed = load_parsed(args.parsed)
    problems: list[str] = []
    judgments = 0
    for case in dataset.cases:
        occurrences = chunks.get(case.bid, [])
        if not occurrences:
            problems.append(f"{case.id}: no chunk artifacts for {case.bid}")
            continue
        if case.kind == "unknown_identifier":
            needle = normalize_text(case.query)
            hits = [o for o in occurrences if needle in normalize_text(o.normalized_text)]
            if hits:
                problems.append(f"{case.id}: 'unknown' identifier occurs in {len(hits)} chunks")
        for judgment in case.judgments:
            judgments += 1
            label = f"{case.id} [{judgment.document[:40]} p={judgment.page} "
            label += f"html={judgment.html_element_id}]"
            document = parsed.get((case.bid, judgment.document))
            if document is None:
                problems.append(f"{label}: parsed document not found")
                continue
            if judgment.page is not None:
                pages = {p.page_number: p.text for p in document.parsed_pages}
                found = snippet_pages(pages, judgment.text)
                if judgment.page not in found:
                    problems.append(f"{label}: text not on declared page; found on {found}")
            if not any(occurrence_matches(judgment, o) for o in occurrences):
                problems.append(f"{label}: no active chunk occurrence matches")
    answerable = sum(case.answerable for case in dataset.cases)
    kinds: dict[str, int] = defaultdict(int)
    for case in dataset.cases:
        kinds[case.kind] += 1
    print(
        f"dataset {dataset.version}: {len(dataset.cases)} cases ({answerable} answerable), "
        f"{judgments} judgments; kinds={dict(sorted(kinds.items()))}"
    )
    for problem in problems:
        print("PROBLEM", problem)
    print("verification:", "OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def describe(result) -> str:
    c = result.citation
    if c.page_start:
        where = f"p{c.page_start}" + (f"-{c.page_end}" if c.page_end != c.page_start else "")
    else:
        loc = c.html_locators[0] if c.html_locators else None
        where = f"html#{loc.element_id if loc else '?'}"
    return f"{c.relative_path[:38]} {where}"


def run(args: argparse.Namespace) -> int:
    from rfp.search.wiring import search_service

    dataset = load_dataset(args.dataset)
    settings = local_only_settings()
    if args.collection:
        settings.qdrant_collection = args.collection
    # Generous timeouts: a contended workstation must not turn into spurious failures.
    search = settings.search.model_copy(
        update={"qdrant_timeout_seconds": 300, "inference_timeout_seconds": 600}
    )
    service = search_service(settings.model_copy(update={"search": search}))
    modes = args.modes.split(",")
    report: dict = {
        "dataset": dataset.version,
        "collection": settings.qdrant_collection,
        "modes": {},
        "cases": {},
    }

    def searcher(mode: str):
        def search(case: EvalCase):
            request = SearchRequest(
                query=case.query,
                top_k=10,
                mode=RetrievalMode(mode),
                filters=SearchFilters(bid_ids=[dataset.bids[case.bid]], doc_types=case.doc_types),
            )
            return service.search(request, rerank_depth=args.rerank_depth)

        return search

    # Warm every model once so the first measured query does not include model loading.
    warm = dataset.cases[0]
    for mode in modes:
        searcher(mode)(warm)

    exit_code = 0
    for mode in modes:
        outcomes: list[CaseResult] = []
        latencies = []
        for case in dataset.cases:
            started = time.perf_counter()
            outcome = evaluate_case(case, dataset.bids[case.bid], mode, searcher(mode))
            latencies.append((time.perf_counter() - started) * 1000)
            outcomes.append(outcome)
        summary = summarize(outcomes)
        summary["latency_p50_ms"] = round(statistics.median(latencies), 1)
        summary["latency_max_ms"] = round(max(latencies), 1)
        report["modes"][mode] = summary
        if summary["errors"] or summary["cross_bid_leaks"] or summary["citation_errors"]:
            exit_code = 1
        for outcome in outcomes:
            entry = report["cases"].setdefault(outcome.case.id, {})
            entry[mode] = {
                "recall@5": outcome.recall_5,
                "mrr@10": outcome.mrr_10,
                "ndcg@10": outcome.ndcg_10,
                "first_relevant_rank": outcome.first_relevant_rank,
                "unknown_ok": outcome.unanswerable_ok,
                "error": outcome.error,
                "warnings": outcome.response.warnings if outcome.response else None,
                "top3": [describe(r) for r in outcome.response.results[:3]]
                if outcome.response
                else [],
            }

    print(f"dataset {dataset.version}; top_k=10; rerank_depth={args.rerank_depth or 'default'}")
    header = [
        "mode",
        "recall@5",
        "mrr@10",
        "ndcg@10",
        "hit@5",
        "unknown_ok",
        "leaks",
        "cit_err",
        "errors",
        "p50_ms",
    ]
    print(" | ".join(header))
    for mode, s in report["modes"].items():
        print(
            f"{mode} | {s['recall@5']} | {s['mrr@10']} | {s['ndcg@10']} | {s['hit@5']} | "
            f"{s['unknown_identifier_ok']}/{s['unknown_identifier_cases']} | {s['cross_bid_leaks']}"
            f" | {s['citation_errors']} | {s['errors']} | {s['latency_p50_ms']}"
        )
    print("\nper-case first relevant rank (None = not in top 10):")
    for case in dataset.cases:
        if not case.answerable:
            continue
        ranks = [report["cases"][case.id][m]["first_relevant_rank"] for m in modes]
        print(f"  {case.id:32} " + "  ".join(f"{m}={r}" for m, r in zip(modes, ranks, strict=True)))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print("report written to", args.out)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "run"):
        command = sub.add_parser(name)
        command.add_argument("--dataset", type=Path, default=DATASET)
    sub.choices["verify"].add_argument(
        "--parsed", type=Path, default=ROOT / "artifacts/assessment/v2"
    )
    sub.choices["verify"].add_argument(
        "--chunks", type=Path, default=ROOT / "artifacts/assessment/v3"
    )
    sub.choices["run"].add_argument("--modes", default=",".join(MODES))
    sub.choices["run"].add_argument("--rerank-depth", type=int, default=None)
    sub.choices["run"].add_argument("--out", type=Path, default=None)
    sub.choices["run"].add_argument(
        "--collection",
        type=collection_name,
        default=None,
        help="Base collection name (default: configured)",
    )
    args = parser.parse_args()
    return verify(args) if args.command == "verify" else run(args)


if __name__ == "__main__":
    sys.exit(main())
