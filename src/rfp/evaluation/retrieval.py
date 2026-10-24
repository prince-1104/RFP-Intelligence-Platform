"""Labeled retrieval evaluation.

A judgment identifies an expected passage by source document, one-based PDF page or HTML locator,
and text that must appear in the retrieved chunk. Matching is applied only to search output, so
the labels never influence retrieval. Grades: 2 = directly answers, 1 = partial/supporting.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from rfp.schemas.parsed import Contract, DocumentType
from rfp.schemas.search import Citation, EvidenceResult, SearchResponse

CaseKind = Literal[
    "identifier",
    "specification",
    "deadline",
    "addendum_change",
    "affidavit",
    "requirement",
    "unknown_identifier",
    "unanswerable",
]


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


class Judgment(Contract):
    document: str = Field(min_length=1, description="Relative path of the source document")
    page: int | None = Field(default=None, ge=1, description="One-based PDF page")
    html_element_id: str | None = None
    text: list[str] = Field(min_length=1, description="Every string must appear in the chunk")
    grade: Literal[1, 2] = 2

    @model_validator(mode="after")
    def locator(self) -> "Judgment":
        pdf = self.document.lower().endswith(".pdf")
        if pdf and (self.page is None or self.html_element_id is not None):
            raise ValueError("PDF judgments need a page and no HTML locator")
        if not pdf and self.page is not None:
            raise ValueError("HTML judgments must not carry a page number")
        return self

    def matches_citation(self, citation: Citation) -> bool:
        if citation.relative_path != self.document:
            return False
        if self.page is not None:
            start = citation.page_start or 0
            return start <= self.page <= (citation.page_end or start)
        if self.html_element_id is not None:
            return any(loc.element_id == self.html_element_id for loc in citation.html_locators)
        return True

    def matches(self, result: EvidenceResult) -> bool:
        text = normalize_text(result.text)
        if not all(normalize_text(needle) in text for needle in self.text):
            return False
        return any(
            self.matches_citation(c) for c in (result.citation, *result.additional_citations)
        )


class EvalCase(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,80}$")
    bid: str
    kind: CaseKind
    query: str = Field(min_length=1)
    doc_types: list[DocumentType] = Field(default_factory=list)
    answerable: bool = True
    judgments: list[Judgment] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> "EvalCase":
        if self.answerable != bool(self.judgments):
            raise ValueError("Answerable cases need judgments; unanswerable cases must have none")
        if self.kind in {"unknown_identifier", "unanswerable"} and self.answerable:
            raise ValueError(f"{self.kind} cases must be unanswerable")
        return self


class EvalDataset(Contract):
    version: str
    description: str
    bids: dict[str, UUID]
    cases: list[EvalCase]
    needs_human_labeling: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self) -> "EvalDataset":
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Case ids must be unique")
        unknown = {case.bid for case in self.cases} - set(self.bids)
        if unknown:
            raise ValueError(f"Cases reference unknown bids: {sorted(unknown)}")
        return self


def load_dataset(path: Path) -> EvalDataset:
    return EvalDataset.model_validate_json(path.read_text(encoding="utf-8"))


def gains(case: EvalCase, results: Sequence[EvidenceResult]) -> list[int]:
    """Graded gain per rank. Each judgment is credited once, at its first matching rank, so
    duplicated or overlapping chunks cannot inflate a score."""
    credited: set[int] = set()
    output = []
    for result in results:
        best = 0
        for index, judgment in enumerate(case.judgments):
            if index not in credited and judgment.matches(result):
                credited.add(index)
                best = max(best, judgment.grade)
        output.append(best)
    return output


def recall_at(case: EvalCase, results: Sequence[EvidenceResult], k: int) -> float:
    found = {
        index
        for index, judgment in enumerate(case.judgments)
        if any(judgment.matches(result) for result in results[:k])
    }
    return len(found) / len(case.judgments)


def reciprocal_rank_at(case: EvalCase, results: Sequence[EvidenceResult], k: int) -> float:
    for rank, result in enumerate(results[:k], start=1):
        if any(judgment.matches(result) for judgment in case.judgments):
            return 1.0 / rank
    return 0.0


def ndcg_at(case: EvalCase, results: Sequence[EvidenceResult], k: int) -> float:
    def dcg(values: Iterable[int]) -> float:
        return sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(values, 1))

    ideal = dcg(sorted((j.grade for j in case.judgments), reverse=True)[:k])
    return dcg(gains(case, results[:k])) / ideal if ideal else 0.0


@dataclass
class CaseResult:
    case: EvalCase
    mode: str
    response: SearchResponse | None
    error: str | None = None
    recall_5: float | None = None
    mrr_10: float | None = None
    ndcg_10: float | None = None
    first_relevant_rank: int | None = None
    leaked: bool = False
    citation_errors: list[str] = field(default_factory=list)
    unanswerable_ok: bool | None = None


def citation_problems(results: Sequence[EvidenceResult], bid_id: UUID) -> tuple[bool, list[str]]:
    leaked = False
    problems = []
    for result in results:
        for citation in (result.citation, *result.additional_citations):
            leaked = leaked or citation.bid_id != bid_id
            pdf = citation.relative_path.lower().endswith(".pdf")
            if pdf and not (citation.page_start and citation.page_start >= 1):
                problems.append(f"rank {result.rank}: PDF citation without one-based page")
            if pdf and citation.page_end and citation.page_end < (citation.page_start or 0):
                problems.append(f"rank {result.rank}: page_end precedes page_start")
            if not pdf and (citation.page_start is not None or not citation.html_locators):
                problems.append(f"rank {result.rank}: HTML citation without locator/null page")
    return leaked, problems


def evaluate_case(
    case: EvalCase, bid_id: UUID, mode: str, search: Callable[[EvalCase], SearchResponse]
) -> CaseResult:
    try:
        response = search(case)
    except Exception as exc:  # recorded per case; one failure must not hide the others
        return CaseResult(case, mode, None, error=f"{type(exc).__name__}: {exc}")
    results = response.results
    outcome = CaseResult(case, mode, response)
    outcome.leaked, outcome.citation_errors = citation_problems(results, bid_id)
    if case.answerable:
        outcome.recall_5 = recall_at(case, results, 5)
        outcome.mrr_10 = reciprocal_rank_at(case, results, 10)
        outcome.ndcg_10 = ndcg_at(case, results, 10)
        rr = outcome.mrr_10
        outcome.first_relevant_rank = round(1 / rr) if rr else None
    elif case.kind == "unknown_identifier":
        outcome.unanswerable_ok = (
            not results and "identifier_evidence_not_found" in response.warnings
        )
    return outcome


def summarize(results: Sequence[CaseResult]) -> dict[str, float | int | None]:
    scored = [r for r in results if r.case.answerable and r.response is not None]

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    unknown = [r for r in results if r.case.kind == "unknown_identifier"]
    return {
        "cases": len(results),
        "scored_cases": len(scored),
        "errors": sum(r.error is not None for r in results),
        "recall@5": mean([r.recall_5 for r in scored if r.recall_5 is not None]),
        "mrr@10": mean([r.mrr_10 for r in scored if r.mrr_10 is not None]),
        "ndcg@10": mean([r.ndcg_10 for r in scored if r.ndcg_10 is not None]),
        "hit@5": mean([float((r.first_relevant_rank or 99) <= 5) for r in scored]),
        "unknown_identifier_ok": sum(bool(r.unanswerable_ok) for r in unknown),
        "unknown_identifier_cases": len(unknown),
        "cross_bid_leaks": sum(r.leaked for r in results),
        "citation_errors": sum(len(r.citation_errors) for r in results),
    }


def snippet_pages(page_texts: dict[int, str], needles: Sequence[str]) -> list[int]:
    """Pages whose whitespace-normalized text contains every needle (label verification)."""
    wanted = [normalize_text(n) for n in needles]
    return [
        number
        for number, text in sorted(page_texts.items())
        if all(n in normalize_text(text) for n in wanted)
    ]
