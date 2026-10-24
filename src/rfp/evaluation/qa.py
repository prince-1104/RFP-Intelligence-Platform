"""Score saved Q&A responses against reviewed expectations.

Two kinds of metric are kept apart:
- automated checks that need no gold label (claim support, quote validity against parsed text);
- correctness metrics that use only cases with review_status "verified".
Cases marked "needs_review" are listed but excluded from correctness metrics.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from rfp.agents.grounding import claim_problems
from rfp.agents.quotes import verbatim
from rfp.agents.validator import normalize
from rfp.schemas.parsed import Contract

QuestionType = Literal[
    "single_fact", "multi_fact", "exhaustive_list", "addendum_sensitive", "unknown"
]
Pages = dict[tuple[str, str], list[tuple[int | None, str]]]


class GoldSource(Contract):
    document: str
    page: int | None = None


class GoldQuote(Contract):
    document: str
    page: int | None = None
    text: str = Field(min_length=1)


class QACase(Contract):
    id: str
    bid_id: str
    question: str
    question_type: QuestionType
    review_status: Literal["verified", "needs_review"]
    answerable: bool
    expected_answer: str
    required_facts: list[list[str]] = Field(default_factory=list)
    required_items: list[str] = Field(default_factory=list)
    material_omissions: list[str] = Field(default_factory=list)
    # Regexes for claims the sources do not support; any match makes the answer incorrect.
    forbidden_patterns: list[str] = Field(default_factory=list)
    sources: list[GoldSource] = Field(default_factory=list)
    quotes: list[GoldQuote] = Field(default_factory=list)
    expected_behavior: str = ""
    notes: str = ""


class QAGold(Contract):
    version: str
    description: str
    reviewer: str
    cases: list[QACase]


def load_gold(path: Path) -> QAGold:
    return QAGold.model_validate_json(path.read_bytes())


def load_pages(root: Path) -> Pages:
    """(bid label, original file name) -> [(page number or None for HTML, text)]."""
    pages: Pages = {}
    for path in sorted(root.glob("*/parsed/*.json")):
        if path.name == "summary.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        key = (path.parent.parent.name, document["original_file_name"])
        pages[key] = [
            (page["page_number"], page["text"] or "") for page in document["parsed_pages"]
        ]
    return pages


def _page_text(pages: Pages, bid: str, document: str, start: int | None, end: int | None) -> str:
    entries = pages.get((bid, document), [])
    if start is None:
        return "\n".join(text for _, text in entries)
    last = end or start
    return "\n".join(text for number, text in entries if number and start <= number <= last)


def verify_gold(gold: QAGold, pages: Pages) -> list[str]:
    """Every gold quote must be a verbatim span of the declared page of the declared document."""
    problems = []
    for case in gold.cases:
        if case.review_status == "verified" and case.answerable and not case.quotes:
            problems.append(f"{case.id}: verified answerable case has no quote")
        for quote in case.quotes:
            if (case.bid_id, quote.document) not in pages:
                problems.append(f"{case.id}: unknown document {quote.document}")
                continue
            text = _page_text(pages, case.bid_id, quote.document, quote.page, quote.page)
            if verbatim(quote.text, text) is None:
                problems.append(f"{case.id}: quote not on {quote.document} page {quote.page}")
    return problems


def _claims(response: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    citations = response.get("citations") or []
    by_id = {c.get("citation_id"): c for c in citations if c.get("citation_id")}
    claims = response.get("claims")
    if claims:
        return [(c["text"], [by_id[i] for i in c["citation_ids"] if i in by_id]) for c in claims]
    if response.get("status") == "not_found":
        return []
    return [(response.get("answer", ""), list(citations))]


def _has(text: str, alternatives: Iterable[str]) -> bool:
    folded = normalize(text)
    return any(normalize(option) in folded for option in alternatives)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(gold: QAGold, responses: list[dict[str, Any]], pages: Pages | None = None) -> dict:
    by_key = {(r["bid_id"], normalize(r["question"])): r for r in responses}
    rows = []
    claim_total = claim_supported = 0
    quote_total = quote_valid = quote_unknown = 0
    for case in gold.cases:
        response = by_key.get((case.bid_id, normalize(case.question)))
        row: dict[str, Any] = {"id": case.id, "review_status": case.review_status}
        if response is None:
            row["status"] = "missing"
            rows.append(row)
            continue
        status = response["status"]
        answer = response.get("answer", "")
        citations = response.get("citations") or []
        row["status"] = status
        claim_rows = []
        for text, cited in _claims(response):
            quotes = [c["excerpt"] for c in cited]
            files = [c["document"] for c in cited]
            problems = claim_problems(text, quotes, case.question, files)
            claim_total += 1
            claim_supported += not problems
            claim_rows.append({"claim": text, "problems": problems})
        row["claims"] = claim_rows
        quote_rows: list[bool | None] = []
        for citation in citations:
            quote_total += 1
            if pages is None or (case.bid_id, citation["document"]) not in pages:
                quote_unknown += 1
                quote_rows.append(None)
                continue
            text = _page_text(
                pages,
                case.bid_id,
                citation["document"],
                citation.get("page"),
                citation.get("page_end") or citation.get("page"),
            )
            valid = verbatim(citation["excerpt"], text) is not None
            quote_valid += valid
            quote_rows.append(valid)
        row["quotes_valid"] = quote_rows
        if case.review_status == "verified":
            if case.answerable:
                facts_ok = all(_has(answer, group) for group in case.required_facts)
                forbidden = [p for p in case.forbidden_patterns if re.search(p, answer, re.I)]
                row["forbidden_claims"] = forbidden
                row["correct"] = status == "answered" and facts_ok and not forbidden
                row["missing_facts"] = [
                    group[0] for group in case.required_facts if not _has(answer, group)
                ]
            else:
                row["correct"] = status == "not_found"
            if case.sources and status == "answered":
                row["source_hit"] = any(
                    citation["document"] == source.document
                    and (
                        source.page is None
                        or (
                            citation.get("page") is not None
                            and citation["page"]
                            <= source.page
                            <= (citation.get("page_end") or citation["page"])
                        )
                    )
                    for citation in citations
                    for source in case.sources
                )
            if case.required_items:
                unresolved = " ".join(
                    item["item"]
                    for item in ((response.get("completeness") or {}).get("unresolved") or [])
                )
                covered = [
                    item
                    for item in case.required_items
                    if status != "not_found" and _has(f"{answer} {unresolved}", [item])
                ]
                row["items_covered"] = covered
                row["items_missing"] = [i for i in case.required_items if i not in covered]
        rows.append(row)

    verified = [r for r, c in zip(rows, gold.cases, strict=True) if c.review_status == "verified"]
    answerable = [
        r
        for r, c in zip(rows, gold.cases, strict=True)
        if c.review_status == "verified" and c.answerable and r["status"] != "missing"
    ]
    unanswerable = [
        r
        for r, c in zip(rows, gold.cases, strict=True)
        if c.review_status == "verified" and not c.answerable and r["status"] != "missing"
    ]
    scored = [r for r in verified if "correct" in r]
    sourced = [r for r in verified if "source_hit" in r]
    listed = [r for r in verified if "items_covered" in r]
    items_total = sum(len(r["items_covered"]) + len(r["items_missing"]) for r in listed)
    summary = {
        "cases": len(gold.cases),
        "verified": len(verified),
        "needs_review": [c.id for c in gold.cases if c.review_status == "needs_review"],
        "missing_responses": [r["id"] for r in rows if r["status"] == "missing"],
        "gold_metrics": {
            "answer_correctness": _ratio(sum(r["correct"] for r in scored), len(scored)),
            "citation_source_accuracy": _ratio(sum(r["source_hit"] for r in sourced), len(sourced)),
            "list_item_completeness": _ratio(
                sum(len(r["items_covered"]) for r in listed), items_total
            ),
            "false_abstention_rate": _ratio(
                sum(r["status"] == "not_found" for r in answerable), len(answerable)
            ),
            "correct_abstention_rate": _ratio(
                sum(r["status"] == "not_found" for r in unanswerable), len(unanswerable)
            ),
        },
        "automated_checks": {
            "claim_support_rate": _ratio(claim_supported, claim_total),
            "claims": claim_total,
            "exact_quote_validity": _ratio(quote_valid, quote_total - quote_unknown),
            "quotes": quote_total,
            "quotes_unchecked": quote_unknown,
        },
    }
    return {"summary": summary, "cases": rows}
