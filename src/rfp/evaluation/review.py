"""Human review packet for the labeled retrieval dataset.

The packet presents each case, its current labels and the evidence the search system actually
returned, with blank reviewer columns. Reviewer decisions are validated and merged into separate
proposed artifacts; the canonical dataset is never rewritten here.

Current labels identify evidence (document, one-based page or HTML element, required text); the
dataset has no expected-answer field, so reviewers supply expected answers.
"""

import csv
import hashlib
import io
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from rfp.evaluation.retrieval import EvalCase, EvalDataset, Judgment
from rfp.schemas.search import Citation, EvidenceResult, SearchResponse

Answerability = Literal["answerable", "unanswerable", "unknown_identifier"]
ANSWERABILITY = ("answerable", "unanswerable", "unknown_identifier")
# Blank status means unreviewed. Only "approved" and "corrected" change the proposed labels.
REVIEW_STATUSES = ("approved", "corrected", "rejected", "needs_discussion")
ANSWERABLE_KINDS = (
    "identifier",
    "specification",
    "deadline",
    "addendum_change",
    "affidavit",
    "requirement",
)
EXCERPT_CHARS = 700
MATCH_NOTE = "automatic text+locator match against current labels; not human-confirmed"

CASE_COLUMNS = [
    "case_id",
    "bid",
    "bid_id",
    "scope_doc_types",
    "query",
    "current_kind",
    "current_answerability",
    "current_expected_answer",
    "current_expected_evidence",
    "current_notes",
    "listed_in_needs_human_labeling",
    "rerank_first_matching_rank",
    "modes_with_match_in_top_k",
    "reviewer_answerability",
    "reviewer_kind",
    "reviewer_expected_answer",
    "reviewer_evidence",
    "reviewer_notes",
    "review_status",
    "reviewer_id",
]
REVIEWER_COLUMNS = [c for c in CASE_COLUMNS if c.startswith("review")]

CANDIDATE_COLUMNS = [
    "case_id",
    "mode",
    "rank",
    "bid_id",
    "document_id",
    "relative_path",
    "document_type",
    "chunk_type",
    "page_start",
    "page_end",
    "html_element_ids",
    "html_headings",
    "html_element_indexes",
    "locator_detail_missing",
    "additional_citations",
    "final_score",
    "reranker_score",
    "identifier_bonus",
    "rrf_score",
    "dense_rank",
    "sparse_rank",
    "identifier_rank",
    "matches_current_judgments",
    "excerpt",
    "text_truncated",
    "occurrence_id",
]


def classification(case: EvalCase) -> Answerability:
    if case.kind == "unknown_identifier":
        return "unknown_identifier"
    return "answerable" if case.answerable else "unanswerable"


def flagged_items(dataset: EvalDataset) -> dict[str, list[int]]:
    """Case IDs named verbatim in ``needs_human_labeling`` items (1-based item numbers).
    Items that do not name a case ID are not linked to any case."""
    linked: dict[str, list[int]] = {}
    for number, item in enumerate(dataset.needs_human_labeling, 1):
        for case in dataset.cases:
            if re.search(rf"(?<![\w-]){re.escape(case.id)}(?![\w-])", item):
                linked.setdefault(case.id, []).append(number)
    return linked


def format_locator(document: str, page: int | None, element: str | None) -> str:
    if page is not None:
        return f"{document} | page={page}"
    if element is not None:
        return f"{document} | html={element}"
    return f"{document} | (no locator)"


def judgment_summary(index: int, judgment: Judgment) -> str:
    texts = " && ".join(judgment.text)
    locator = format_locator(judgment.document, judgment.page, judgment.html_element_id)
    return f"[J{index}] grade={judgment.grade} {locator} | text={texts}"


def citation_fields(citation: Citation) -> dict[str, Any]:
    """Locator fields exactly as emitted by search; missing values stay blank."""
    locators = citation.html_locators
    ids = [loc.element_id for loc in locators if loc.element_id]
    headings = [loc.heading for loc in locators if loc.heading]
    indexes = [str(loc.element_index) for loc in locators if loc.element_index is not None]
    html = not citation.relative_path.lower().endswith(".pdf")
    detail_missing = html and not any(
        loc.element_id or loc.heading or loc.element_index for loc in locators
    )
    return {
        "page_start": "" if citation.page_start is None else citation.page_start,
        "page_end": "" if citation.page_end is None else citation.page_end,
        "html_element_ids": ";".join(ids),
        "html_headings": ";".join(headings),
        "html_element_indexes": ";".join(indexes),
        "locator_detail_missing": "yes" if detail_missing else "",
    }


def _score(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def _locator_text(citation: Citation) -> str:
    fields = citation_fields(citation)
    if fields["page_start"] != "":
        where = f"pages={fields['page_start']}-{fields['page_end']}"
    elif fields["html_element_ids"]:
        where = f"html={fields['html_element_ids']}"
    elif fields["html_element_indexes"]:
        where = f"html_index={fields['html_element_indexes']}"
    else:
        where = "no locator"
    return f"{citation.relative_path} | {where}"


def candidate_row(case: EvalCase, mode: str, result: EvidenceResult) -> dict[str, Any]:
    citation = result.citation
    matched = [i for i, j in enumerate(case.judgments, 1) if j.matches(result)]
    text = result.text
    return {
        "case_id": case.id,
        "mode": mode,
        "rank": result.rank,
        "bid_id": str(citation.bid_id),
        "document_id": str(citation.document_id),
        "relative_path": citation.relative_path,
        "document_type": citation.document_type.value,
        "chunk_type": result.chunk_type,
        **citation_fields(citation),
        "additional_citations": " ;; ".join(_locator_text(c) for c in result.additional_citations),
        "final_score": _score(result.scores.final_score),
        "reranker_score": _score(result.scores.reranker_score),
        "identifier_bonus": _score(result.scores.identifier_bonus),
        "rrf_score": _score(result.scores.rrf_score),
        "dense_rank": result.scores.dense_rank or "",
        "sparse_rank": result.scores.sparse_rank or "",
        "identifier_rank": result.scores.identifier_rank or "",
        "matches_current_judgments": ",".join(f"J{i}" for i in matched) if matched else "no",
        "excerpt": text[:EXCERPT_CHARS],
        "text_truncated": "yes" if len(text) > EXCERPT_CHARS else "",
        "occurrence_id": str(result.occurrence_id),
    }


@dataclass
class ModeEvidence:
    mode: str
    top_k: int
    candidates: list[dict[str, Any]] = field(default_factory=list)
    full_texts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def first_match(self) -> int | None:
        for row in self.candidates:
            if row["matches_current_judgments"] != "no":
                return int(row["rank"])
        return None


Search = Callable[[EvalCase, str, int], SearchResponse]


def collect_evidence(
    dataset: EvalDataset, search: Search, modes: dict[str, int]
) -> dict[str, list[ModeEvidence]]:
    """Run every case through each mode (mode -> top_k). Errors are recorded, never hidden."""
    evidence: dict[str, list[ModeEvidence]] = {}
    for case in dataset.cases:
        entries = []
        for mode, top_k in modes.items():
            entry = ModeEvidence(mode, top_k)
            try:
                response = search(case, mode, top_k)
            except Exception as exc:  # recorded per case/mode
                entry.error = f"{type(exc).__name__}: {exc}"
            else:
                entry.warnings = list(response.warnings)
                for result in response.results[:top_k]:
                    entry.candidates.append(candidate_row(case, mode, result))
                    entry.full_texts[str(result.occurrence_id)] = result.text
            entries.append(entry)
        evidence[case.id] = entries
    return evidence


def case_rows(
    dataset: EvalDataset, evidence: dict[str, list[ModeEvidence]], primary_mode: str
) -> list[dict[str, Any]]:
    flagged = flagged_items(dataset)
    rows = []
    for case in dataset.cases:
        entries = evidence.get(case.id, [])
        primary = next((e for e in entries if e.mode == primary_mode), None)
        with_match = [e.mode for e in entries if e.first_match is not None]
        row = {
            "case_id": case.id,
            "bid": case.bid,
            "bid_id": str(dataset.bids[case.bid]),
            "scope_doc_types": ",".join(t.value for t in case.doc_types) or "(all)",
            "query": case.query,
            "current_kind": case.kind,
            "current_answerability": classification(case),
            # The dataset has no expected-answer field; never synthesized from evidence text.
            "current_expected_answer": "",
            "current_expected_evidence": "\n".join(
                judgment_summary(i, j) for i, j in enumerate(case.judgments, 1)
            ),
            "current_notes": case.notes or "",
            "listed_in_needs_human_labeling": ",".join(
                f"item {n}" for n in flagged.get(case.id, [])
            ),
            "rerank_first_matching_rank": ""
            if primary is None or primary.error
            else (primary.first_match or "none"),
            "modes_with_match_in_top_k": ",".join(with_match),
        }
        row.update({column: "" for column in REVIEWER_COLUMNS})
        rows.append(row)
    return rows


def to_csv(rows: Iterable[dict[str, Any]], columns: Sequence[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return buffer.getvalue()


def write_text(path: Path, text: str, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig lets spreadsheet tools detect UTF-8; "\n" keeps bytes identical across OSes.
    path.write_bytes(text.encode("utf-8-sig" if bom else "utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- Reviewer import -------------------------------------------------------------------------


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceRef:
    document: str
    page: int | None
    html_element_id: str | None
    text: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    case_id: str
    status: str
    reviewer_id: str
    answerability: str
    kind: str
    expected_answer: str
    evidence: tuple[EvidenceRef, ...]
    notes: str


EVIDENCE_LINE = re.compile(
    r"^(?P<document>[^|]+?)\s*\|\s*(?:page=(?P<page>\d+)|html=(?P<html>[^|\s]+))"
    r"(?:\s*\|\s*text=(?P<text>.+))?$"
)


def parse_evidence(value: str) -> list[EvidenceRef]:
    """One reference per line: ``<document> | page=<n>`` or ``<document> | html=<element id>``,
    optionally ``| text=<snippet> && <snippet>`` (required for references not in current labels)."""
    refs = []
    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue
        match = EVIDENCE_LINE.match(line)
        if not match:
            raise ReviewError(f"invalid evidence reference {line!r}")
        page = int(match["page"]) if match["page"] else None
        if page is not None and page < 1:
            raise ReviewError(f"page must be one-based in {line!r}")
        texts = tuple(t.strip() for t in (match["text"] or "").split("&&") if t.strip())
        refs.append(EvidenceRef(match["document"].strip(), page, match["html"], texts))
    return refs


def read_reviews(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ReviewError(f"{path} is not UTF-8; save it as 'CSV UTF-8'") from None
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ("case_id", *REVIEWER_COLUMNS) if c not in (reader.fieldnames or [])]
    if missing:
        raise ReviewError(f"review file lacks columns: {', '.join(missing)}")
    return [{k: (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


def _match_judgment(case: EvalCase, ref: EvidenceRef) -> Judgment | None:
    for judgment in case.judgments:
        if (judgment.document, judgment.page, judgment.html_element_id) == (
            ref.document,
            ref.page,
            ref.html_element_id,
        ):
            return judgment
    return None


def validate_reviews(
    rows: list[dict[str, str]],
    dataset: EvalDataset,
    bid_documents: dict[str, list[str]],
) -> tuple[dict[str, Decision], list[str]]:
    """Every dataset case must appear exactly once. Returns decisions for reviewed rows and
    all problems found; callers must refuse to merge when problems exist."""
    cases = {case.id: case for case in dataset.cases}
    problems: list[str] = []
    seen: dict[str, int] = {}
    decisions: dict[str, Decision] = {}
    for number, row in enumerate(rows, 2):
        case_id = row.get("case_id", "")
        seen[case_id] = seen.get(case_id, 0) + 1
        if case_id not in cases:
            problems.append(f"row {number}: unknown case_id {case_id!r}")
            continue
        if seen[case_id] > 1:
            problems.append(f"row {number}: duplicate case_id {case_id!r}")
            continue
        case = cases[case_id]
        status = row["review_status"]
        filled = [c for c in REVIEWER_COLUMNS if c != "review_status" and row.get(c)]
        if not status:
            if filled:
                problems.append(
                    f"{case_id}: reviewer fields {filled} filled but review_status is blank "
                    "(partially reviewed cases are never treated as approved)"
                )
            continue
        errors = []
        if status not in REVIEW_STATUSES:
            errors.append(f"review_status {status!r} not in {REVIEW_STATUSES}")
        if not row["reviewer_id"]:
            errors.append("reviewer_id is required")
        answerability = row["reviewer_answerability"]
        kind = row["reviewer_kind"] or case.kind
        try:
            evidence = parse_evidence(row["reviewer_evidence"])
        except ReviewError as exc:
            errors.append(str(exc))
            evidence = []
        if status in ("approved", "corrected"):
            if answerability not in ANSWERABILITY:
                errors.append(f"reviewer_answerability must be one of {ANSWERABILITY}")
            if status == "approved" and answerability != classification(case):
                errors.append("approved answerability differs from current; use 'corrected'")
            if status == "approved" and row["reviewer_kind"] not in ("", case.kind):
                errors.append("approved kind differs from current; use 'corrected'")
            if answerability == "answerable":
                if kind not in ANSWERABLE_KINDS:
                    errors.append(f"answerable cases need reviewer_kind in {ANSWERABLE_KINDS}")
                if not row["reviewer_expected_answer"]:
                    errors.append("reviewer_expected_answer is required for answerable cases")
                if not evidence:
                    errors.append("reviewer_evidence is required for answerable cases")
            elif answerability in ANSWERABILITY:
                if evidence or row["reviewer_expected_answer"]:
                    errors.append(f"{answerability} cases must have no evidence or answer")
                if row["reviewer_kind"] not in ("", answerability):
                    errors.append(f"reviewer_kind must be blank or {answerability!r}")
                kind = answerability
            documents = set(bid_documents.get(case.bid, []))
            for ref in evidence:
                if ref.document not in documents:
                    errors.append(f"evidence document not in {case.bid}: {ref.document!r}")
                    continue
                pdf = ref.document.lower().endswith(".pdf")
                if pdf != (ref.page is not None):
                    errors.append(f"{ref.document!r}: PDFs need page=, HTML needs html=")
                if not ref.text and _match_judgment(case, ref) is None:
                    errors.append(f"new evidence {ref.document!r} needs text=<required snippet>")
        if status != "approved" and not row["reviewer_notes"]:
            errors.append(f"reviewer_notes (rationale) are required for {status!r}")
        if errors:
            problems.extend(f"{case_id}: {error}" for error in errors)
            continue
        decisions[case_id] = Decision(
            case_id,
            status,
            row["reviewer_id"],
            answerability,
            kind,
            row["reviewer_expected_answer"],
            tuple(evidence),
            row["reviewer_notes"],
        )
    missing = [case_id for case_id in cases if case_id not in seen]
    problems.extend(f"missing case_id {case_id!r}" for case_id in missing)
    return decisions, problems


def _judgment_for(case: EvalCase, ref: EvidenceRef) -> Judgment:
    """Reviewer text replaces the required snippets; otherwise the matching current judgment
    (same document and locator) is kept with its text and grade. New references get grade 2."""
    existing = _match_judgment(case, ref)
    if existing is not None and not ref.text:
        return existing
    return Judgment(
        document=ref.document,
        page=ref.page,
        html_element_id=ref.html_element_id,
        text=list(ref.text),
        grade=existing.grade if existing is not None else 2,
    )


def _label(judgment: Judgment) -> str:
    locator = format_locator(judgment.document, judgment.page, judgment.html_element_id)
    return f"grade={judgment.grade} {locator} | text={' && '.join(judgment.text)}"


def merge(
    dataset: EvalDataset, decisions: dict[str, Decision], require_complete: bool = False
) -> tuple[EvalDataset, dict[str, Any], list[str]]:
    """Proposed dataset, approved-label record and change summary. Unreviewed and
    needs_discussion cases keep their current labels and are reported as not approved."""
    pending = [
        c.id
        for c in dataset.cases
        if c.id not in decisions or decisions[c.id].status == "needs_discussion"
    ]
    if require_complete and pending:
        raise ReviewError(f"{len(pending)} case(s) not approved/corrected/rejected: {pending}")
    cases = []
    changes = []
    labels = []
    for case in dataset.cases:
        decision = decisions.get(case.id)
        if decision is None or decision.status == "needs_discussion":
            cases.append(case)
            entry: dict[str, Any] = {"case_id": case.id, "status": "unreviewed"}
            if decision is not None:
                entry.update(
                    status=decision.status, reviewer_id=decision.reviewer_id, notes=decision.notes
                )
            labels.append(entry)
            continue
        record: dict[str, Any] = {
            "case_id": case.id,
            "status": decision.status,
            "reviewer_id": decision.reviewer_id,
            "notes": decision.notes,
            "original": case.model_dump(mode="json", exclude_none=True),
        }
        if decision.status == "rejected":
            changes.append(f"{case.id}: REMOVED (rejected by {decision.reviewer_id})")
            labels.append(record)
            continue
        record.update(
            answerability=decision.answerability,
            kind=decision.kind,
            expected_answer=decision.expected_answer,
            evidence=[
                {
                    "document": r.document,
                    "page": r.page,
                    "html_element_id": r.html_element_id,
                    "text": list(r.text),
                }
                for r in decision.evidence
            ],
        )
        labels.append(record)
        if decision.status == "approved":
            cases.append(case)
            continue
        judgments = [_judgment_for(case, ref) for ref in decision.evidence]
        updated = EvalCase.model_validate(
            {
                **case.model_dump(mode="json"),
                "kind": decision.kind,
                "answerable": decision.answerability == "answerable",
                "judgments": [j.model_dump(mode="json") for j in judgments],
            }
        )
        cases.append(updated)
        before = [_label(j) for j in case.judgments]
        after = [_label(j) for j in judgments]
        detail = []
        if classification(case) != decision.answerability:
            detail.append(f"answerability {classification(case)} -> {decision.answerability}")
        if case.kind != decision.kind:
            detail.append(f"kind {case.kind} -> {decision.kind}")
        detail += [f"- {line}" for line in before if line not in after]
        detail += [f"+ {line}" for line in after if line not in before]
        lines = detail or ["(labels unchanged; expected answer and notes recorded)"]
        changes.append(
            f"{case.id}: CORRECTED by {decision.reviewer_id}"
            + "".join(f"\n    {line}" for line in lines)
        )
    proposed = EvalDataset.model_validate(
        {
            **dataset.model_dump(mode="json"),
            "version": f"{dataset.version}-proposed",
            "cases": [c.model_dump(mode="json") for c in cases],
        }
    )
    summary = {
        "dataset_version": dataset.version,
        "counts": {
            status: sum(1 for item in labels if item["status"] == status)
            for status in ("unreviewed", *REVIEW_STATUSES)
        },
        "not_approved": pending,
        "labels": labels,
    }
    return proposed, summary, changes


def dumps(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


@dataclass(frozen=True)
class KnownMiss:
    """A retrieval miss reported in project context (VERIFICATION.md), checked against this run."""

    description: str
    case_ids: tuple[str, ...]


def render_summary(
    dataset: EvalDataset,
    evidence: dict[str, list[ModeEvidence]],
    manifest: dict[str, Any],
    primary_mode: str,
    known_misses: Sequence[KnownMiss],
) -> str:
    flagged = flagged_items(dataset)
    by_class: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for case in dataset.cases:
        by_class[classification(case)] = by_class.get(classification(case), 0) + 1
        by_kind[case.kind] = by_kind.get(case.kind, 0) + 1
    no_locator = [
        f"{case.id} J{i}"
        for case in dataset.cases
        for i, j in enumerate(case.judgments, 1)
        if j.page is None and j.html_element_id is None
    ]
    lines = [
        "# Retrieval review packet summary",
        "",
        "Generated by `scripts/build_retrieval_review_packet.py`. Matches below are automatic",
        f"({MATCH_NOTE}).",
        "",
        "## Inputs",
        "",
    ]
    lines += [f"- {key}: `{value}`" for key, value in manifest.items()]
    lines += [
        "",
        "## Dataset facts (from `evaluation/assessment_retrieval.json`)",
        "",
        f"- Cases: {len(dataset.cases)}; by current classification: "
        + ", ".join(f"{k} {v}" for k, v in sorted(by_class.items())),
        "- By kind: " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())),
        f"- Judgments: {sum(len(c.judgments) for c in dataset.cases)}",
        "- No case has a human-approved label yet: all "
        f"{len(dataset.cases)} cases require review (review status blank).",
        f"- `needs_human_labeling` is a dataset-level list of {len(dataset.needs_human_labeling)} "
        "notes, not a per-case flag. Notes that name a case ID verbatim are linked to it:",
    ]
    for number, item in enumerate(dataset.needs_human_labeling, 1):
        linked = sorted(case_id for case_id, items in flagged.items() if number in items)
        target = ", ".join(linked) if linked else "no case ID named (not linked; reviewer to map)"
        lines.append(f"  {number}. {item} -> **{target}**")
    lines += [
        "- Expected answers: the dataset has no expected-answer field, so "
        f"`current_expected_answer` is blank for all {len(dataset.cases)} cases. Judgments give "
        "the expected evidence (document, locator, required text), not an answer.",
        f"- Answerable judgments without a page or HTML element locator: {len(no_locator)}"
        + (f" ({', '.join(no_locator)})" if no_locator else ""),
        "- Unanswerable and unknown-identifier cases have no expected evidence by design: "
        + ", ".join(c.id for c in dataset.cases if not c.answerable),
        "- `doc_types` scope: "
        + (
            ", ".join(c.id for c in dataset.cases if c.doc_types)
            or "no case sets a doc-type filter; every case is scoped to its bid only"
        ),
        "",
        "## Observations from this run",
        "",
    ]
    modes = [e.mode for e in next(iter(evidence.values()), [])]
    errors = [
        f"{case_id} [{e.mode}]: {e.error}"
        for case_id, entries in evidence.items()
        for e in entries
        if e.error
    ]
    lines.append(f"- Search errors: {len(errors)}")
    lines += [f"  - {error}" for error in errors]
    for mode in modes:
        missing = [
            case.id
            for case in dataset.cases
            if case.answerable
            for e in evidence[case.id]
            if e.mode == mode and not e.error and e.first_match is None
        ]
        top_k = next(e.top_k for e in evidence[dataset.cases[0].id] if e.mode == mode)
        lines.append(
            f"- `{mode}` (top {top_k}): answerable cases with no matching candidate: "
            f"{len(missing)}" + (f" ({', '.join(missing)})" if missing else "")
        )
    returned = [
        f"{case.id} [{e.mode}] {len(e.candidates)} result(s)"
        for case in dataset.cases
        if not case.answerable
        for e in evidence[case.id]
        if e.candidates
    ]
    lines.append(
        "- Unanswerable/unknown-identifier cases that returned candidates (expected for "
        "natural-language unanswerable queries; reviewer must confirm none answers): "
        + (", ".join(returned) if returned else "none")
    )
    detail_missing = sorted(
        {
            f"{row['relative_path']} occurrence {row['occurrence_id']}"
            for entries in evidence.values()
            for e in entries
            for row in e.candidates
            if row["locator_detail_missing"]
        }
    )
    lines.append(
        f"- Retrieved HTML candidates without element ID, heading or non-zero index: "
        f"{len(detail_missing)}"
    )
    lines += [f"  - {item}" for item in detail_missing]
    lines += ["", "## Known retrieval misses (project context) vs this run", ""]
    for miss in known_misses:
        ranks = []
        for case_id in miss.case_ids:
            entry = next((e for e in evidence.get(case_id, []) if e.mode == primary_mode), None)
            if entry is None:
                ranks.append(f"{case_id}: not run")
            elif entry.error:
                ranks.append(f"{case_id}: error")
            else:
                ranks.append(f"{case_id}: first match rank {entry.first_match or 'none'}")
        lines.append(f"- {miss.description} — `{primary_mode}`: " + "; ".join(ranks))
    lines += [
        "- W-9 OCR table reconstruction (main RFP pages 54-59): out of scope; no cases labeled.",
        "",
    ]
    return "\n".join(lines)


def write_packet(
    out_dir: Path,
    dataset: EvalDataset,
    evidence: dict[str, list[ModeEvidence]],
    manifest: dict[str, Any],
    bid_documents: dict[str, list[str]],
    primary_mode: str,
    known_misses: Sequence[KnownMiss],
) -> list[Path]:
    """Write review artifacts. Output depends only on the inputs (no timestamps or timings)."""
    rows = case_rows(dataset, evidence, primary_mode)
    candidates = [row for case in dataset.cases for e in evidence[case.id] for row in e.candidates]
    packet = {
        "manifest": manifest,
        "match_note": MATCH_NOTE,
        "bid_documents": bid_documents,
        "cases": [{k: v for k, v in row.items() if k not in REVIEWER_COLUMNS} for row in rows],
        "evidence": {
            case_id: [
                {
                    "mode": e.mode,
                    "top_k": e.top_k,
                    "error": e.error,
                    "warnings": e.warnings,
                    "candidates": [
                        {**row, "text": e.full_texts[row["occurrence_id"]]} for row in e.candidates
                    ],
                }
                for e in entries
            ]
            for case_id, entries in evidence.items()
        },
    }
    outputs = {
        "cases.csv": (to_csv(rows, CASE_COLUMNS), True),
        "evidence.csv": (to_csv(candidates, CANDIDATE_COLUMNS), True),
        "packet.json": (dumps(packet), False),
        "SUMMARY.md": (
            render_summary(dataset, evidence, manifest, primary_mode, known_misses),
            False,
        ),
    }
    written = []
    for name, (text, bom) in outputs.items():
        write_text(out_dir / name, text, bom)
        written.append(out_dir / name)
    return written
