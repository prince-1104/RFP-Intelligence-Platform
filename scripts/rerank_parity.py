"""Reranker batching parity, latency and memory against LOCAL Qdrant and the local model cache.

    python scripts/rerank_parity.py parity [--depth 40] [--batch-sizes 1,4,8,16,32,64]
    python scripts/rerank_parity.py bench  [--depths 10,20,40] [--batch-sizes 1,16,32]
                                           [--repeats 3] [--stride N] [--out FILE]
    python scripts/rerank_parity.py memory [--depths 10,20,40] [--batch-sizes 1,16,32] [--stride N]

Every mode runs the real ``SearchService`` over the labeled dataset queries (bid-scoped filters);
only the reranker batch size and rerank depth change between runs. ``parity`` uses one pair per
model call (batch size 1) as the reference. ``memory`` runs each configuration in a fresh
subprocess with an identical warm-up and workload, so peak working sets are comparable.
"""

import argparse
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path

from local_guard import ROOT, local_only_settings, memory_mb

from rfp.evaluation.retrieval import EvalCase, load_dataset
from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest

DATASET = ROOT / "evaluation" / "assessment_retrieval.json"
# Cross-encoder logits are float32; different padded lengths change GEMM blocking and
# accumulation order, which perturbs logits of magnitude <= ~12 by ~1e-6..1e-5. 1e-4 is an
# order of magnitude above that noise and far below any score gap that should decide rank.
TOLERANCE = 1e-4


def ints(text: str) -> list[int]:
    return [int(value) for value in text.split(",") if value]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[index]


class Harness:
    def __init__(self):
        from rfp.search.wiring import search_service

        self.dataset = load_dataset(DATASET)
        settings = local_only_settings()
        # Generous timeouts: a contended workstation must not turn into spurious failures.
        search = settings.search.model_copy(
            update={"qdrant_timeout_seconds": 300, "inference_timeout_seconds": 600}
        )
        self.service = search_service(settings.model_copy(update={"search": search}))
        self.pre_rerank: list[list[str]] = []
        original = self.service._rerank

        def recording(plan, ordered, depth):
            self.pre_rerank.append([c.point_id for c in ordered])
            return original(plan, ordered, depth)

        self.service._rerank = recording  # type: ignore[method-assign]
        # Unknown-identifier cases never reach the reranker; they add no rerank signal.
        self.cases = [c for c in self.dataset.cases if c.kind != "unknown_identifier"]

    def search(self, case: EvalCase, batch_size: int, depth: int):
        self.service.runtime.reranker.batch_size = batch_size  # type: ignore[attr-defined]
        request = SearchRequest(
            query=case.query,
            top_k=depth,
            mode=RetrievalMode.hybrid_rerank,
            filters=SearchFilters(bid_ids=[self.dataset.bids[case.bid]], doc_types=case.doc_types),
        )
        self.pre_rerank.clear()
        response = self.service.search(request, rerank_depth=depth)
        return response, list(self.pre_rerank[-1]) if self.pre_rerank else []


def snapshot(response) -> list[dict]:
    return [
        {
            "id": str(r.occurrence_id),
            "text": r.text,
            "citation": r.citation.model_dump(mode="json"),
            "additional": [c.model_dump(mode="json") for c in r.additional_citations],
            "score": r.scores.reranker_score,
            "bonus": r.scores.identifier_bonus,
            "rrf": r.scores.rrf_score,
        }
        for r in response.results
    ]


def parity(args: argparse.Namespace) -> int:
    harness = Harness()
    sizes = ints(args.batch_sizes)
    harness.search(harness.cases[0], 1, args.depth)  # warm models
    failures: list[str] = []
    worst: dict[int, float] = {b: 0.0 for b in sizes}
    compared = 0
    for case in harness.cases:
        reference, reference_order = harness.search(case, 1, args.depth)
        expected = snapshot(reference)
        ids = [item["id"] for item in expected]
        if len(ids) != len(set(ids)):
            failures.append(f"{case.id}: duplicate results in reference")
        for size in sizes:
            response, order = harness.search(case, size, args.depth)
            if order != reference_order:
                failures.append(f"{case.id} B={size}: pre-rerank candidate order differs")
            actual = snapshot(response)
            if [a["id"] for a in actual] != ids:
                failures.append(f"{case.id} B={size}: final ranking differs")
            for a, e in zip(actual, expected, strict=False):
                if (a["text"], a["citation"], a["additional"], a["bonus"], a["rrf"]) != (
                    e["text"],
                    e["citation"],
                    e["additional"],
                    e["bonus"],
                    e["rrf"],
                ):
                    failures.append(f"{case.id} B={size}: text/citation/metadata differs")
                    break
                if a["score"] is None or e["score"] is None:
                    if a["score"] != e["score"]:
                        failures.append(f"{case.id} B={size}: reranker score missing")
                    continue
                worst[size] = max(worst[size], abs(a["score"] - e["score"]))
            if response.warnings != reference.warnings:
                failures.append(f"{case.id} B={size}: warnings differ")
            compared += 1
    print(f"cases={len(harness.cases)} comparisons={compared} depth={args.depth}")
    print(f"tolerance={TOLERANCE}")
    for size in sizes:
        status = "OK" if worst[size] <= TOLERANCE else "EXCEEDS"
        print(f"  batch_size={size:3}  max |score - unbatched| = {worst[size]:.3e}  {status}")
        if worst[size] > TOLERANCE:
            failures.append(f"B={size}: score difference {worst[size]:.3e} > {TOLERANCE}")
    for failure in failures:
        print("FAIL", failure)
    print("parity:", "OK" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


def bench(args: argparse.Namespace) -> int:
    harness = Harness()
    # Every n-th case keeps both bids and all case kinds in a smaller workload.
    cases = harness.cases[:: args.stride]
    harness.search(cases[0], 32, max(ints(args.depths)))  # warm models
    rows = []
    for depth in ints(args.depths):
        for size in ints(args.batch_sizes):
            harness.search(cases[0], size, depth)  # warm this shape
            rerank, total, errors = [], [], 0
            for _ in range(args.repeats):
                for case in cases:
                    try:
                        response, _ = harness.search(case, size, depth)
                    except Exception:
                        errors += 1
                        continue
                    if "rerank" in response.timings_ms:
                        rerank.append(response.timings_ms["rerank"])
                    total.append(response.timings_ms["total"])
            row = {
                "depth": depth,
                "batch_size": size,
                "samples": len(rerank),
                "errors": errors,
                "rerank_p50_ms": round(statistics.median(rerank), 1) if rerank else None,
                "rerank_p95_ms": round(percentile(rerank, 0.95), 1) if rerank else None,
                "total_p50_ms": round(statistics.median(total), 1) if total else None,
                "total_p95_ms": round(percentile(total, 0.95), 1) if total else None,
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
    # Top-5 stability of each depth against the deepest depth (same batch size, single pass).
    deepest = max(ints(args.depths))
    size = ints(args.batch_sizes)[-1]
    changes = {}
    for depth in ints(args.depths):
        changed = 0
        for case in cases:
            shallow, _ = harness.search(case, size, depth)
            deep, _ = harness.search(case, size, deepest)
            top = [str(r.occurrence_id) for r in shallow.results[:5]]
            changed += top != [str(r.occurrence_id) for r in deep.results[:5]]
        changes[depth] = changed
    print(json.dumps({"top5_differs_from_depth": deepest, "cases": len(cases), **changes}))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"rows": rows, "top5_changes": changes}, indent=1))
    return 0


def memory_child(args: argparse.Namespace) -> int:
    harness = Harness()
    # Warm-up on a different query at depth 1 loads every model without growing the
    # cross-encoder arena to the measured shape.
    harness.search(harness.cases[-1], 1, 1)
    warm = memory_mb()
    for item in harness.cases[:: args.stride]:
        harness.search(item, args.batch_size, args.depth)
    after = memory_mb()
    # Peak private commit is unaffected by working-set trimming under memory pressure.
    key = "peak_private_mb" if "peak_private_mb" in after else "peak_rss_mb"
    print(
        json.dumps(
            {
                "depth": args.depth,
                "batch_size": args.batch_size,
                "metric": key,
                "after_warmup_mb": round(warm[key], 1),
                "peak_mb": round(after[key], 1),
                "workload_growth_mb": round(after[key] - warm[key], 1),
                "peak_rss_mb": round(after["peak_rss_mb"], 1),
            }
        )
    )
    return 0


def memory(args: argparse.Namespace) -> int:
    for depth in ints(args.depths):
        for size in ints(args.batch_sizes):
            command = [
                sys.executable,
                str(Path(__file__)),
                "_memory_child",
                "--depth",
                str(depth),
                "--batch-size",
                str(size),
                "--stride",
                str(args.stride),
            ]
            output = subprocess.run(command, capture_output=True, text=True, check=False)
            lines = [line for line in output.stdout.splitlines() if line.startswith('{"depth"')]
            print(lines[-1] if lines else f"depth={depth} B={size} FAILED rc={output.returncode}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parity")
    p.add_argument("--depth", type=int, default=40)
    p.add_argument("--batch-sizes", default="1,4,8,16,32,64")
    b = sub.add_parser("bench")
    b.add_argument("--depths", default="10,20,40")
    b.add_argument("--batch-sizes", default="1,16,32")
    b.add_argument("--repeats", type=int, default=3)
    b.add_argument("--stride", type=int, default=1)
    b.add_argument("--out", type=Path, default=None)
    m = sub.add_parser("memory")
    m.add_argument("--depths", default="10,20,40")
    m.add_argument("--batch-sizes", default="1,16,32")
    m.add_argument("--stride", type=int, default=1)
    c = sub.add_parser("_memory_child")
    c.add_argument("--depth", type=int, required=True)
    c.add_argument("--batch-size", type=int, required=True)
    c.add_argument("--stride", type=int, default=1)
    args = parser.parse_args()
    handlers = {"parity": parity, "bench": bench, "memory": memory, "_memory_child": memory_child}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
