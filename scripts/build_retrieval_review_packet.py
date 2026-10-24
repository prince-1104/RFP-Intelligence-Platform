"""Human review packet for the labeled retrieval dataset (LOCAL, READ-ONLY).

    python scripts/build_retrieval_review_packet.py build [--out-dir evaluation/review_packet]
                                                          [--top-k 10] [--secondary-top-k 5]
                                                          [--no-search]
    python scripts/build_retrieval_review_packet.py validate --reviews FILE [--packet FILE]
    python scripts/build_retrieval_review_packet.py merge --reviews FILE --out-dataset FILE
                                                          --out-labels FILE [--require-complete]
                                                          [--packet FILE] [--force]

``build`` searches every case through the existing ``SearchService`` (hybrid_rerank first, then
hybrid, sparse and dense) against local Qdrant only, via ``local_guard`` and a read-only client
proxy; it writes ``cases.csv``, ``evidence.csv``, ``packet.json`` and ``SUMMARY.md``.
``--no-search`` writes the case file without retrieval evidence. ``validate`` checks a reviewed
``cases.csv``. ``merge`` writes a proposed dataset and approved-label record to new paths and
prints the change summary; it never writes the canonical dataset.
"""

import argparse
import json
import sys
from pathlib import Path

from local_guard import ROOT, ReadOnlyQdrant, local_only_settings

from rfp.evaluation.retrieval import EvalCase, load_dataset
from rfp.evaluation.review import (
    KnownMiss,
    ModeEvidence,
    ReviewError,
    collect_evidence,
    dumps,
    merge,
    read_reviews,
    sha256_file,
    validate_reviews,
    write_packet,
    write_text,
)
from rfp.schemas.chunks import ChunkingResult
from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest

DATASET = ROOT / "evaluation" / "assessment_retrieval.json"
OUT_DIR = ROOT / "evaluation" / "review_packet"
CHUNKS = ROOT / "artifacts" / "assessment" / "v3"
PRIMARY = "hybrid_rerank"
SECONDARY = ("hybrid", "sparse", "dense")
# Reported in VERIFICATION.md; listed so the packet shows whether this run reproduces them.
KNOWN_MISSES = (
    KnownMiss(
        "Bid2 specification sheet does not rank for RAM, display and battery questions",
        ("b2-memory", "b2-display", "b2-battery"),
    ),
    KnownMiss(
        "Bid1 award timing and buyer contact miss the reranked top 10",
        ("b1-award-timing", "b1-buyer-contact"),
    ),
)


def bid_documents(root: Path) -> dict[str, list[str]]:
    """Source documents per bid from the local chunk artifacts that were indexed."""
    documents: dict[str, set[str]] = {}
    for path in sorted(root.glob("*/chunks/*.json")):
        if path.name == "summary.json":
            continue
        result = ChunkingResult.model_validate_json(path.read_bytes())
        documents.setdefault(path.parent.parent.name, set()).add(result.relative_path)
    return {bid: sorted(paths) for bid, paths in sorted(documents.items())}


def build(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    before = sha256_file(args.dataset)
    documents = bid_documents(args.chunks)
    if not documents:
        print(f"no chunk artifacts under {args.chunks}; cannot list bid documents", file=sys.stderr)
        return 1
    modes = {PRIMARY: args.top_k, **{m: args.secondary_top_k for m in SECONDARY}}
    manifest = {
        "dataset": args.dataset.relative_to(ROOT).as_posix()
        if args.dataset.is_relative_to(ROOT)
        else str(args.dataset),
        "dataset_sha256": before,
        "dataset_version": dataset.version,
        "chunk_artifacts": args.chunks.relative_to(ROOT).as_posix()
        if args.chunks.is_relative_to(ROOT)
        else str(args.chunks),
    }
    evidence: dict[str, list[ModeEvidence]]
    if args.no_search:
        manifest["retrieval_evidence"] = "not generated (--no-search)"
        evidence = {case.id: [] for case in dataset.cases}
    else:
        from rfp.search.collection import CollectionManager, create_qdrant_client
        from rfp.search.models import shared_runtime
        from rfp.search.service import SearchService

        settings = local_only_settings()
        client = ReadOnlyQdrant(create_qdrant_client(settings))
        runtime = shared_runtime(settings.search)
        manager = CollectionManager(client, settings.qdrant_collection, runtime.profile())
        print(f"target: Qdrant {settings.qdrant_url} collection {manager.name} (read-only proxy)")
        if not manager.exists():
            print(f"collection {manager.name} not found locally; refusing", file=sys.stderr)
            return 1
        service = SearchService(runtime, client, settings)  # type: ignore[arg-type]

        def search(case: EvalCase, mode: str, top_k: int):
            request = SearchRequest(
                query=case.query,
                top_k=top_k,
                mode=RetrievalMode(mode),
                filters=SearchFilters(bid_ids=[dataset.bids[case.bid]], doc_types=case.doc_types),
            )
            return service.search(request)

        profile = runtime.profile()
        manifest.update(
            {
                "retrieval_evidence": "freshly generated by SearchService (local, read-only)",
                "qdrant": f"{settings.qdrant_url} (loopback)",
                "collection": manager.name,
                "embedding_profile": profile.fingerprint,
                "dense_model": profile.dense_model,
                "sparse_model": profile.sparse_model,
                "reranker_model": settings.search.reranker_model,
                "rerank_depth": settings.search.rerank_depth,
                "rerank_batch_size": settings.search.rerank_batch_size,
                "modes_top_k": ", ".join(f"{m}={k}" for m, k in modes.items()),
            }
        )
        evidence = collect_evidence(dataset, search, modes)
    written = write_packet(
        args.out_dir, dataset, evidence, manifest, documents, PRIMARY, KNOWN_MISSES
    )
    if sha256_file(args.dataset) != before:
        print("canonical dataset changed during build; aborting", file=sys.stderr)
        return 1
    for path in written:
        print("wrote", path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    errors = sum(1 for entries in evidence.values() for e in entries if e.error)
    print(f"cases={len(dataset.cases)} search_errors={errors}")
    return 1 if errors else 0


def _validated(args: argparse.Namespace):
    dataset = load_dataset(args.dataset)
    try:
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReviewError(f"cannot read packet {args.packet}; run build first") from None
    if packet["manifest"]["dataset_sha256"] != sha256_file(args.dataset):
        raise ReviewError("dataset changed since the packet was built; rebuild and re-review")
    rows = read_reviews(args.reviews)
    decisions, problems = validate_reviews(rows, dataset, packet["bid_documents"])
    return dataset, decisions, problems


def validate(args: argparse.Namespace) -> int:
    try:
        dataset, decisions, problems = _validated(args)
    except ReviewError as exc:
        print("INVALID", exc)
        return 1
    statuses: dict[str, int] = {}
    for decision in decisions.values():
        statuses[decision.status] = statuses.get(decision.status, 0) + 1
    print(f"cases={len(dataset.cases)} reviewed={len(decisions)} by status={statuses}")
    print(f"unreviewed={len(dataset.cases) - len(decisions)}")
    for problem in problems:
        print("PROBLEM", problem)
    print("validation:", "OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def merge_command(args: argparse.Namespace) -> int:
    canonical = args.dataset.resolve()
    for target in (args.out_dataset, args.out_labels):
        if target.resolve() in (canonical, DATASET.resolve(), args.reviews.resolve()):
            print(f"refusing to write {target}: it is an input or the canonical dataset")
            return 2
        if target.exists() and not args.force:
            print(f"refusing to overwrite {target} (use --force)")
            return 2
    try:
        dataset, decisions, problems = _validated(args)
        if problems:
            for problem in problems:
                print("PROBLEM", problem)
            print("merge refused: fix the review file first")
            return 1
        proposed, labels, changes = merge(dataset, decisions, args.require_complete)
    except ReviewError as exc:
        print("merge refused:", exc)
        return 1
    labels["source_reviews"] = str(args.reviews)
    labels["source_dataset_sha256"] = sha256_file(args.dataset)
    write_text(args.out_dataset, dumps(proposed.model_dump(mode="json", exclude_none=True)))
    write_text(args.out_labels, dumps(labels))
    print(f"counts: {labels['counts']}")
    print(f"not approved (labels unchanged): {len(labels['not_approved'])}")
    print("changes vs canonical dataset:" if changes else "changes vs canonical dataset: none")
    for change in changes:
        print("  " + change)
    print(f"proposed dataset: {args.out_dataset}\napproved labels: {args.out_labels}")
    print(f"canonical dataset unchanged: {args.dataset}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {"build": build, "validate": validate, "merge": merge_command}
    for name in commands:
        command = sub.add_parser(name)
        command.add_argument("--dataset", type=Path, default=DATASET)
        command.add_argument("--chunks", type=Path, default=CHUNKS)
    sub.choices["build"].add_argument("--out-dir", type=Path, default=OUT_DIR)
    sub.choices["build"].add_argument("--top-k", type=int, default=10)
    sub.choices["build"].add_argument("--secondary-top-k", type=int, default=5)
    sub.choices["build"].add_argument("--no-search", action="store_true")
    for name in ("validate", "merge"):
        sub.choices[name].add_argument("--reviews", type=Path, required=True)
        sub.choices[name].add_argument("--packet", type=Path, default=OUT_DIR / "packet.json")
    sub.choices["merge"].add_argument("--out-dataset", type=Path, required=True)
    sub.choices["merge"].add_argument("--out-labels", type=Path, required=True)
    sub.choices["merge"].add_argument("--require-complete", action="store_true")
    sub.choices["merge"].add_argument("--force", action="store_true")
    args = parser.parse_args()
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
