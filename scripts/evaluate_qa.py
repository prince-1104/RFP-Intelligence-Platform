"""Offline Q&A evaluation. Reads saved responses and parsed text; no model or Qdrant calls.

    python scripts/evaluate_qa.py verify [--gold evaluation/qa_reviewed.json] [--parsed ROOT]
    python scripts/evaluate_qa.py score --samples artifacts/qa/qa_samples.json [--out report.json]

``verify`` checks that every gold quote is a verbatim span of its declared page.
``score`` reports gold metrics on verified cases and automated checks on all responses.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp.evaluation.qa import load_gold, load_pages, score, verify_gold  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify", "score"])
    parser.add_argument("--gold", type=Path, default=ROOT / "evaluation" / "qa_reviewed.json")
    parser.add_argument("--parsed", type=Path, default=ROOT / "artifacts" / "assessment")
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    gold = load_gold(args.gold)
    pages = load_pages(args.parsed) if args.parsed.is_dir() else None
    if args.command == "verify":
        if pages is None:
            print(json.dumps({"error": "parsed_root_missing", "path": str(args.parsed)}))
            return 1
        problems = verify_gold(gold, pages)
        print(json.dumps({"cases": len(gold.cases), "problems": problems}, indent=2))
        return 1 if problems else 0
    if args.samples is None:
        parser.error("score requires --samples")
    responses = json.loads(args.samples.read_text(encoding="utf-8"))
    report = score(gold, responses, pages)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
