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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp.agents.schemas import (  # noqa: E402
    REQUIRED_FIELDS,
    BidExtraction,
    ExtractionConfig,
    ExtractionTrace,
)
from rfp.agents.validator import contained, normalize  # noqa: E402
from rfp.evaluation.qa import load_pages  # noqa: E402

KEY_SHAPE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})")
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
TOKEN = re.compile(r"[a-z0-9][a-z0-9./+@-]{2,}")


def secret_values(env_file: Path) -> list[str]:
    if not env_file.is_file():
        return []
    values = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        value = value.strip().strip("'\"")
        if sep and SECRET_NAME.search(name) and len(value) >= 8:
            values.append(value)
    return values


def leaks(paths: list[Path], secrets: list[str]) -> list[str]:
    found = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if any(secret in text for secret in secrets) or KEY_SHAPE.search(text):
            found.append(str(path))
    return found


def cited_text(pages, bid: str, file: str, page: int | None) -> str | None:
    entries = pages.get((bid, file))
    if entries is None:
        return None
    if page is None:
        return "\n".join(text for _, text in entries)
    return "\n".join(text for number, text in entries if number == page)


def support(value: str, text: str) -> tuple[str, float]:
    if contained(value, text):
        return "verbatim", 1.0
    tokens = TOKEN.findall(normalize(value))
    if not tokens:
        return "none", 0.0
    haystack = normalize(text)
    share = sum(token in haystack for token in tokens) / len(tokens)
    return ("all_tokens" if share == 1 else "partial"), round(share, 2)


def check_bid(output: Path, trace_path: Path | None, pages, max_retries: int) -> dict:
    document = BidExtraction.model_validate_json(output.read_bytes())
    bid = document.bid_id
    files = {name for label, name in pages if label == bid}
    fields = []
    for name in REQUIRED_FIELDS:
        field = document.fields[name]
        row: dict = {
            "field": name,
            "value": field.value,
            "notes": field.notes,
            "sources": [
                {"file": s.file, "page": s.page, "html_element_id": s.html_element_id}
                for s in field.sources
            ],
        }
        if field.value is None:
            row["status"] = "not_found" if field.notes == "Not found in documents" else "unresolved"
            row["cited_null"] = bool(field.sources)
        else:
            row["status"] = "accepted"
            row["foreign_files"] = sorted({s.file for s in field.sources} - files)
            texts = [cited_text(pages, bid, s.file, s.page) or "" for s in field.sources]
            kind, share = support(field.value, "\n".join(texts))
            row["support_on_cited_pages"] = kind
            row["token_share"] = share
            per_source = [support(field.value, text)[1] for text in texts]
            row["sources_without_value_tokens"] = sum(1 for item in per_source if item == 0)
        fields.append(row)
    report: dict = {
        "bid_id": bid,
        "schema_valid": True,
        "field_names_ok": tuple(document.fields) == REQUIRED_FIELDS,
        "validation": document.validation.model_dump(),
        "addendum_changes": [
            change.model_dump(mode="json") for change in document.addendum_changes
        ],
        "fields": fields,
    }
    if trace_path and trace_path.is_file():
        trace = ExtractionTrace.model_validate_json(trace_path.read_bytes())
        retries = [event.retry for event in trace.events if event.retry is not None]
        report["trace"] = {
            "path": str(trace_path),
            "outcome": trace.outcome,
            "events": len(trace.events),
            "model": trace.model.model_dump(),
            "token_usage": trace.token_usage.model_dump(),
            "max_retry": max(retries, default=0),
            "retries_bounded": max(retries, default=0) <= max_retries,
            "agents": sorted({event.agent for event in trace.events}),
        }
    return report


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
