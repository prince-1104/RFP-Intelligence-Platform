"""Copy the reviewed submission artifacts into submission/evidence with contact details masked.

The originals under artifacts/ are only read. In each copy, email addresses and telephone numbers
inside JSON string values are replaced with fixed markers; nothing else changes. `redactions.json`
lists where each marker was applied (JSON path and kind, never the value) and the SHA-256 of every
source file, so `check` can confirm the copies still match a fresh build from unchanged originals.

    python scripts/build_submission_evidence.py build    # needs artifacts/
    python scripts/build_submission_evidence.py check    # needs artifacts/
    python scripts/build_submission_evidence.py verify   # evidence folder and README only
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "submission" / "evidence"
SOURCES = {
    "Bid1_extraction.json": "artifacts/extraction_v4/Bid1.json",
    "Bid2_extraction.json": "artifacts/extraction_v4/Bid2.json",
    "Bid1_extraction_trace.json": "artifacts/extraction_v4/traces/extraction_Bid1.json",
    "qa_examples.json": "artifacts/qa_examples.json",
    "retrieval_evaluation.json": "artifacts/eval/retrieval_phase7.json",
    "submission_audit.json": "artifacts/eval/submission_audit.json",
    "Bid2_process_bid_run_summary.json": "artifacts/pipeline_v1/Bid2/run_summary.json",
    "demo_ask_bid1_due_date.json": "artifacts/final_demo/ask_bid1_due_date.json",
    "demo_ask_bid2_collection.json": "artifacts/final_demo/ask_bid2_pipeline_check.json",
    "demo_ask_bid2_abstention.json": "artifacts/final_demo/ask_bid2_abstention.json",
    "phaseA_ask_bid2_affidavits.json": "artifacts/phaseA_qa/01_affidavit_bid2.json",
    "phaseA_compare_warranty_run1.json": "artifacts/phaseA_qa/02_warranty_compare.json",
    "phaseA_compare_warranty.json": "artifacts/phaseA_qa/05_warranty_compare_after_reason_fix.json",
    "phaseA_compare_isolation_probe.json": "artifacts/phaseA_qa/03_isolation_probe_compare.json",
    "phaseA_compare_unanswerable.json": "artifacts/phaseA_qa/04_unanswerable_compare.json",
}
COMPARISONS = [name for name in SOURCES if name.startswith("phaseA_compare_")]
PATTERNS = {
    "email": (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email redacted]"),
    "phone": (re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b"), "[phone redacted]"),
}
LOG = "redactions.json"


def mask(node: Any, path: str, found: list[dict]) -> Any:
    if isinstance(node, dict):
        return {key: mask(value, f"{path}/{key}", found) for key, value in node.items()}
    if isinstance(node, list):
        return [mask(value, f"{path}/{index}", found) for index, value in enumerate(node)]
    if isinstance(node, str):
        for kind, (pattern, marker) in PATTERNS.items():
            node, count = pattern.subn(marker, node)
            if count:
                found.append({"path": path, "kind": kind, "count": count})
    return node


def render(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def build_all() -> dict[str, str]:
    files: dict[str, str] = {}
    log: dict[str, Any] = {"markers": {k: v[1] for k, v in PATTERNS.items()}, "files": {}}
    for name, source in SOURCES.items():
        raw = (ROOT / source).read_bytes()
        found: list[dict] = []
        files[name] = render(mask(json.loads(raw), "", found))
        log["files"][name] = {
            "source": source,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "redactions": found,
        }
    files[LOG] = render(log)
    return files


def verify() -> list[str]:
    """Checks a reviewer can run from a clone, without artifacts/, models or Qdrant."""
    from audit_submission import METRICS, readme_table

    from rfp.agents.compare import ComparisonResponse
    from rfp.agents.qa import QAResponse
    from rfp.agents.schemas import REQUIRED_FIELDS, BidExtraction, ExtractionTrace

    problems = []
    data = {name: json.loads((DEST / name).read_text(encoding="utf-8")) for name in SOURCES}
    for bid in ("Bid1", "Bid2"):
        document = BidExtraction.model_validate(data[f"{bid}_extraction.json"])
        if tuple(document.fields) != REQUIRED_FIELDS:
            problems.append(f"{bid}: fields differ from the 20 required fields")
        for name, field in document.fields.items():
            if field.value is not None and not field.sources:
                problems.append(f"{bid} {name}: value without a citation")
            if field.value is None and not field.notes:
                problems.append(f"{bid} {name}: null without a reason")
    trace = ExtractionTrace.model_validate(data["Bid1_extraction_trace.json"])
    extraction = data["Bid1_extraction.json"]
    if trace.model is None or trace.model.provider == "fake":
        problems.append("trace was not produced by a live model")
    if set(trace.final_statuses) != set(extraction["fields"]):
        problems.append("trace statuses do not cover the 20 fields")
    answered = [
        item
        for item in data["qa_examples.json"]["examples"]
        if item["status"] == "answered" and item["citations"]
    ]
    if len(answered) < 10:
        problems.append(f"only {len(answered)} cited Q&A answers")
    affidavits = QAResponse.model_validate(data["phaseA_ask_bid2_affidavits.json"])
    if affidavits.model.provider == "fake" or affidavits.status != "answered":
        problems.append("affidavit example is not a live, answered response")
    for name in COMPARISONS:
        response = ComparisonResponse.model_validate(data[name])
        if response.model.provider == "fake":
            problems.append(f"{name}: not produced by a live model")
        if response.bid_ids != ["Bid1", "Bid2"]:
            problems.append(f"{name}: expected a Bid1/Bid2 comparison")
    modes = data["retrieval_evaluation.json"]["modes"]
    table = readme_table(ROOT / "README.md")
    if len(table) < 2:
        problems.append("README retrieval table not found")
    for mode, values in table.items():
        for metric in METRICS:
            if abs(modes[mode][metric] - values[metric]) > 0.0005 + 1e-9:
                problems.append(f"{mode} {metric}: README differs from retrieval_evaluation.json")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "check", "verify"])
    args = parser.parse_args()
    if args.command == "verify":
        problems = verify()
        print(json.dumps({"files": len(SOURCES), "problems": problems}, indent=2))
        return 0 if not problems else 1
    files = build_all()
    if args.command == "build":
        DEST.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (DEST / name).write_text(text, encoding="utf-8")
    stale = [
        name
        for name, text in files.items()
        if not (DEST / name).is_file() or (DEST / name).read_text(encoding="utf-8") != text
    ]
    extra = (
        sorted(path.name for path in DEST.glob("*.json") if path.name not in files)
        if DEST.is_dir()
        else []
    )
    log = json.loads(files[LOG])
    summary = {
        "files": len(SOURCES),
        "redactions": {
            name: sum(item["count"] for item in entry["redactions"])
            for name, entry in log["files"].items()
        },
        "stale": stale,
        "unexpected": extra,
    }
    print(json.dumps(summary, indent=2))
    return 0 if not stale and not extra else 1


if __name__ == "__main__":
    sys.exit(main())
