"""Offline checks of an extraction JSON against parsed source pages. No model or Qdrant calls.

Per field it reports the status, whether every cited file belongs to the bid, and whether the
value is present on the cited pages (verbatim, or every value token present). It also checks the
20 field names, retry bounds in the trace, and that no configured secret value or API-key-shaped
string appears in the outputs or traces. Values are never judged correct here.
"""

import json
import re
from pathlib import Path

from rfp.agents.schemas import REQUIRED_FIELDS, BidExtraction, ExtractionTrace
from rfp.agents.semantics import join_split_emails
from rfp.agents.validator import contained, normalize

KEY_SHAPE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})")
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
TOKEN = re.compile(r"[a-z0-9][a-z0-9./+@-]{2,}")

Pages = dict[tuple[str, str], list[tuple[int | None, str]]]


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


def pages_from_parsed(parsed_dir: Path, bid: str) -> Pages:
    """(bid label, original file name) -> pages, for one folder of parsed artifacts."""
    pages: Pages = {}
    for path in sorted(parsed_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        pages[(bid, document["original_file_name"])] = [
            (page["page_number"], page["text"] or "") for page in document["parsed_pages"]
        ]
    return pages


def cited_text(pages: Pages, bid: str, file: str, page: int | None) -> str | None:
    entries = pages.get((bid, file))
    if entries is None:
        return None
    if page is None:
        return "\n".join(text for _, text in entries)
    return "\n".join(text for number, text in entries if number == page)


def support(value: str, text: str) -> tuple[str, float]:
    text = join_split_emails(text)
    if contained(value, text):
        return "verbatim", 1.0
    tokens = TOKEN.findall(normalize(value))
    if not tokens:
        return "none", 0.0
    haystack = normalize(text)
    share = sum(token in haystack for token in tokens) / len(tokens)
    return ("all_tokens" if share == 1 else "partial"), round(share, 2)


def check_bid(output: Path, trace_path: Path | None, pages: Pages, max_retries: int) -> dict:
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
