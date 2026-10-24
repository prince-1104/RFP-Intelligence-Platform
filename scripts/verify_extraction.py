"""Offline checks of extract-bid outputs against parsed source pages. No model or Qdrant calls.

    python scripts/verify_extraction.py --outputs artifacts/extraction_v2 [--out report.json]

Per field it reports the status, whether every cited file belongs to the bid's folder, and
whether the value is present on the cited pages (verbatim, or every value token present).
It also checks the 20 field names, retry bounds in the trace, and that no configured secret
value or API-key-shaped string appears in the outputs or traces. Values are never judged
correct here; that needs a human comparison with the source.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp.agents.schemas import ExtractionConfig  # noqa: E402
from rfp.evaluation.extraction_check import (  # noqa: E402, F401
    check_bid,
    cited_text,
    leaks,
    secret_values,
    support,
)
from rfp.evaluation.qa import load_pages  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--traces", type=Path)
    parser.add_argument("--parsed", type=Path, default=ROOT / "artifacts" / "assessment")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    pages = load_pages(args.parsed)
    traces = args.traces or args.outputs / "traces"
    max_retries = ExtractionConfig().max_retries
    bids = []
    written = []
    for output in sorted(args.outputs.glob("*.json")):
        trace = traces / f"extraction_{output.stem}.json"
        bids.append(check_bid(output, trace, pages, max_retries))
        written += [output] + ([trace] if trace.is_file() else [])
    report = {"bids": bids, "secret_leaks": leaks(written, secret_values(args.env_file))}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    for bid in bids:
        print(f"{bid['bid_id']}: names_ok={bid['field_names_ok']} {bid['validation']}")
        for row in bid["fields"]:
            detail = row["status"]
            if row["status"] == "accepted":
                detail += (
                    f" support={row['support_on_cited_pages']}({row['token_share']})"
                    f" foreign={row['foreign_files']}"
                    f" sources={len(row['sources'])}"
                    f" off_value_sources={row['sources_without_value_tokens']}"
                )
            print(f"  {row['field']:<40} {detail}")
        if "trace" in bid:
            trace = bid["trace"]
            print(
                f"  trace: {trace['outcome']} retries<= {trace['max_retry']} {trace['token_usage']}"
            )
    print("secret_leaks:", report["secret_leaks"])
    return 1 if report["secret_leaks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
