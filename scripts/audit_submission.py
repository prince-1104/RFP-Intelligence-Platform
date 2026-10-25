"""Re-check the submission artifacts against their sources. Read-only: no model, Qdrant or network.

Checks that each extraction JSON has the 20 fields, that every citation names a file of that bid
and a page or HTML element that exists in the parsed artifacts, that traces come from a live model
and are internally consistent, that each Q&A excerpt is a verbatim span of the cited page, and
that the README retrieval table equals the saved evaluation report. It does not judge whether a
value is correct.

    python scripts/audit_submission.py [--out artifacts/eval/submission_audit.json]
"""

import argparse
import json
import sys
from pathlib import Path

from rfp.agents.schemas import REQUIRED_FIELDS, BidExtraction, ExtractionTrace
from rfp.agents.validator import contained
from rfp.evaluation.extraction_check import cited_text, pages_from_parsed

ROOT = Path(__file__).resolve().parents[1]
EXTRACTIONS = [
    ("extraction_v4", "artifacts/extraction_v4", "artifacts/assessment/{bid}/parsed"),
    ("pipeline_v1", "artifacts/pipeline_v1/{bid}", "artifacts/pipeline_v1/{bid}/parsed"),
    ("pipeline_v2", "artifacts/pipeline_v2/{bid}", "artifacts/pipeline_v2/{bid}/parsed"),
    ("demo_process", "artifacts/demo_process/{bid}", "artifacts/demo_process/{bid}/parsed"),
]
QA_EXAMPLES = "artifacts/qa_examples.json"
RETRIEVAL = "artifacts/eval/retrieval_phase7.json"
README_MODES = {
    "dense (BGE-small)": "dense",
    "sparse (BM25)": "sparse",
    "hybrid (RRF)": "hybrid",
    "hybrid + rerank": "hybrid_rerank",
}
METRICS = ("recall@5", "mrr@10", "ndcg@10", "hit@5")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def html_ids(parsed_dir: Path) -> dict[str, str]:
    """File name -> raw parsed JSON, used to confirm that a cited HTML element id exists."""
    raw = {}
    for path in parsed_dir.glob("*.json"):
        if path.name != "summary.json":
            raw[load(path)["original_file_name"]] = path.read_text(encoding="utf-8")
    return raw


def check_citation(pages, raw, bid, file, page, element) -> str | None:
    entries = pages.get((bid, file))
    if entries is None:
        return f"{file}: not a document of {bid}"
    if page is not None and page not in {number for number, _ in entries}:
        return f"{file}: page {page} does not exist"
    if element is not None and f'"{element}"' not in raw.get(file, ""):
        return f"{file}: element {element} not found"
    return None


def check_extraction(run: str, output: Path, trace: Path, parsed: Path) -> dict:
    document = BidExtraction.model_validate_json(output.read_bytes())
    bid = document.bid_id
    pages = pages_from_parsed(parsed, bid)
    raw = html_ids(parsed)

    def resolve(source) -> str | None:
        return check_citation(pages, raw, bid, source.file, source.page, source.html_element_id)

    problems = []
    if tuple(document.fields) != REQUIRED_FIELDS:
        problems.append("field names or order differ from the 20 required fields")
    counts = {"accepted": 0, "not_found": 0, "unresolved": 0}
    citations = 0
    for name, field in document.fields.items():
        if field.value is None:
            status = "not_found" if field.notes == "Not found in documents" else "unresolved"
            if not field.notes:
                problems.append(f"{name}: null without a reason")
        else:
            status = "accepted"
            if not field.sources:
                problems.append(f"{name}: value without a citation")
        counts[status] += 1
        for source in field.sources:
            citations += 1
            if issue := resolve(source):
                problems.append(f"{name}: {issue}")
    for change in document.addendum_changes:
        for source in change.sources:
            citations += 1
            if issue := resolve(source):
                problems.append(f"addendum change {change.field}: {issue}")
    validation = document.validation
    if (validation.passed, validation.not_found) != (counts["accepted"], counts["not_found"]):
        problems.append(f"validation block {validation.model_dump()} does not match {counts}")
    report = {
        "run": run,
        "bid_id": bid,
        "output": str(output.relative_to(ROOT)),
        "fields": len(document.fields),
        "statuses": counts,
        "citations_checked": citations,
        "addendum_changes": len(document.addendum_changes),
        "problems": problems,
    }
    if trace.is_file():
        data = ExtractionTrace.model_validate_json(trace.read_bytes())
        searches = [e for e in data.events if e.agent == "retrieval"]
        report["trace"] = {
            "path": str(trace.relative_to(ROOT)),
            "model": data.model.model_dump() if data.model else None,
            "outcome": data.outcome,
            "events": len(data.events),
            "agents": sorted({e.agent for e in data.events}),
            "search_calls": len(searches),
            "model_calls": data.token_usage.calls,
            "token_usage": data.token_usage.model_dump(),
            "final_statuses_match": data.final_statuses
            == {
                name: (
                    "accepted"
                    if f.value is not None
                    else "not_found"
                    if f.notes == "Not found in documents"
                    else "unresolved"
                )
                for name, f in document.fields.items()
            },
        }
        if data.model is None or data.model.provider == "fake":
            problems.append("trace was not produced by a live model")
        if not searches or data.token_usage.calls == 0:
            problems.append("trace has no search or model calls")
    else:
        problems.append("trace missing")
    return report


def check_qa(path: Path) -> dict:
    payload = load(path)
    examples = payload["examples"]
    problems = []
    parsed_cache: dict[str, tuple] = {}
    verbatim = citations = 0
    range_only: list[str] = []
    for item in examples:
        bid = item["bid_id"]
        if bid not in parsed_cache:
            parsed = ROOT / "artifacts" / "assessment" / bid / "parsed"
            parsed_cache[bid] = (pages_from_parsed(parsed, bid), html_ids(parsed))
        pages, raw = parsed_cache[bid]
        trace_path = ROOT / item["trace"]
        if not trace_path.is_file():
            problems.append(f"#{item['id']}: trace missing")
        else:
            trace = load(trace_path)
            model = trace.get("model") or {}
            if model.get("provider") in (None, "fake"):
                problems.append(f"#{item['id']}: trace not from a live model")
            if trace.get("trace_id") and trace["trace_id"] not in item["trace"]:
                problems.append(f"#{item['id']}: trace id differs from file name")
        if item["status"] == "answered" and not item["citations"]:
            problems.append(f"#{item['id']}: answer without a citation")
        for citation in item["citations"]:
            citations += 1
            issue = check_citation(
                pages, raw, bid, citation["document"], citation["page"], citation["html_element_id"]
            )
            if issue:
                problems.append(f"#{item['id']}: {issue}")
                continue
            text = cited_text(pages, bid, citation["document"], citation["page"]) or ""
            if contained(citation["excerpt"], text):
                verbatim += 1
                continue
            last = citation.get("page_end") or citation["page"]
            span = "\n".join(
                cited_text(pages, bid, citation["document"], number) or ""
                for number in range(citation["page"] or 0, (last or 0) + 1)
            )
            if citation["page"] is not None and contained(citation["excerpt"], span):
                verbatim += 1
                range_only.append(
                    f"#{item['id']}: excerpt is on a later page of the cited range "
                    f"{citation['page']}-{last}, not on the first page"
                )
            else:
                problems.append(f"#{item['id']}: excerpt not found on the cited page")
    statuses = [item["status"] for item in examples]
    verdicts = [item.get("review", {}).get("verdict") for item in examples]
    return {
        "path": str(path.relative_to(ROOT)),
        "model": payload["summary"].get("model"),
        "examples": len(examples),
        "answered": statuses.count("answered"),
        "not_found": statuses.count("not_found"),
        "citations": citations,
        "verbatim_citations": verbatim,
        "imprecise_first_page": range_only,
        "review_verdicts": {v: verdicts.count(v) for v in sorted(set(map(str, verdicts)))},
        "review_method": payload["summary"].get("review_method"),
        "problems": problems,
    }


def readme_table(readme: Path) -> dict[str, dict[str, float]]:
    rows = {}
    for line in readme.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] in README_MODES and len(cells) >= 5:
            rows[README_MODES[cells[0]]] = dict(
                zip(METRICS, (float(c) for c in cells[1:5]), strict=True)
            )
    return rows


def check_retrieval() -> dict:
    report = load(ROOT / RETRIEVAL)["modes"]
    table = readme_table(ROOT / "README.md")
    problems = []
    for mode, values in table.items():
        for metric, shown in values.items():
            saved = report[mode][metric]
            if abs(saved - shown) > 0.0005 + 1e-9:
                problems.append(f"{mode} {metric}: README {shown} vs saved {saved}")
    if set(table) != set(README_MODES.values()):
        problems.append(f"README table rows found: {sorted(table)}")
    summary = {
        mode: {key: report[mode][key] for key in (*METRICS, "cross_bid_leaks", "citation_errors")}
        for mode in report
    }
    return {"report": RETRIEVAL, "modes": summary, "problems": problems}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    extractions = []
    for run, folder, parsed in EXTRACTIONS:
        for bid in ("Bid1", "Bid2"):
            output = ROOT / folder.format(bid=bid) / f"{bid}.json"
            if not output.is_file():
                continue
            trace = output.parent / "traces" / f"extraction_{bid}.json"
            extractions.append(check_extraction(run, output, trace, ROOT / parsed.format(bid=bid)))
    result = {
        "extractions": extractions,
        "qa": check_qa(ROOT / QA_EXAMPLES),
        "retrieval": check_retrieval(),
    }
    problems = sum(len(item["problems"]) for item in extractions)
    problems += len(result["qa"]["problems"]) + len(result["retrieval"]["problems"])
    result["problem_count"] = problems
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        if args.out.exists():
            print(f"refusing to overwrite {args.out}", file=sys.stderr)
            return 2
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
