"""Repeated PDF header/footer report for parsed bid artifacts. No model, Qdrant or network.

    python scripts/report_page_furniture.py --parsed artifacts/assessment/v2
        --output artifacts/phaseC_pdf_cleanup/furniture_report.json
        [--baseline-chunks artifacts/assessment/v3 --cleaned-chunks artifacts/phaseC_pdf_cleanup]

For every parsed PDF under ``<parsed>/<bid>/parsed/*.json`` it lists each text repeated in a page
band (``rfp.chunking.furniture``): band, normalized key, truncated text, pages, decision, the pages
it was removed from, and the reason. With both chunk roots (``<root>/<bid>/chunks/*.json``) it also
compares chunk counts per document. The parsed and chunk artifacts are only read; an existing
report is not overwritten.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp.chunking.furniture import (  # noqa: E402
    EDGE_FRACTION,
    MAX_CHARS,
    MIN_DENSITY,
    MIN_PAGES,
    detect,
)
from rfp.schemas.chunks import ChunkingResult  # noqa: E402
from rfp.schemas.parsed import ParsedDocument  # noqa: E402


def chunk_counts(root: Path) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for path in sorted(root.glob("*/chunks/*.json")):
        if path.name == "summary.json":
            continue
        result = ChunkingResult.model_validate_json(path.read_bytes())
        counts[(path.parent.parent.name, result.relative_path)] = len(result.occurrences)
    return counts


def build(parsed_root: Path, baseline: Path | None, cleaned: Path | None) -> dict:
    documents = []
    totals: Counter[str] = Counter()
    for path in sorted(parsed_root.glob("*/parsed/*.json")):
        if path.name == "summary.json":
            continue
        document = ParsedDocument.model_validate_json(path.read_bytes())
        if document.source_content_type != "application/pdf":
            continue
        candidates = detect(document)
        for candidate in candidates:
            totals[candidate.decision] += 1
            totals["removed_blocks"] += len(candidate.removed)
        documents.append(
            {
                "bid": path.parent.parent.name,
                "document": document.relative_path,
                "pages": document.page_count,
                "candidates": [candidate.as_dict() for candidate in candidates],
            }
        )
    report: dict = {
        "thresholds": {
            "edge_fraction": EDGE_FRACTION,
            "min_pages": MIN_PAGES,
            "min_density": MIN_DENSITY,
            "max_chars": MAX_CHARS,
        },
        "summary": {
            "pdf_documents": len(documents),
            "candidates": totals["removed"] + totals["retained"],
            "removed_candidates": totals["removed"],
            "retained_candidates": totals["retained"],
            "removed_blocks": totals["removed_blocks"],
        },
        "documents": documents,
    }
    if baseline is not None and cleaned is not None:
        before, after = chunk_counts(baseline), chunk_counts(cleaned)
        report["chunk_counts"] = {
            "baseline_total": sum(before.values()),
            "cleaned_total": sum(after.values()),
            "documents": [
                {
                    "bid": bid,
                    "document": name,
                    "baseline": before.get((bid, name)),
                    "cleaned": after.get((bid, name)),
                }
                for bid, name in sorted(set(before) | set(after))
            ],
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-chunks", type=Path)
    parser.add_argument("--cleaned-chunks", type=Path)
    args = parser.parse_args()
    if (args.baseline_chunks is None) != (args.cleaned_chunks is None):
        parser.error("--baseline-chunks and --cleaned-chunks go together")
    if args.output.exists():
        print(json.dumps({"error": "output_exists", "path": str(args.output)}))
        return 2
    report = build(args.parsed, args.baseline_chunks, args.cleaned_chunks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({"path": str(args.output), **report["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
