"""Deterministic checks against retrieved evidence. Model confidence is not evidence."""

import re

from rfp.agents.schemas import DraftField, EvidenceHit, FieldValidation, SourceRef

DATE_FIELDS = {"Due Date", "Pre Bid Meeting", "Delivery Date"}
IDENTIFIER_FIELDS = {"Bid Number", "Model_no", "Part_no"}
ABSENCE = ("not found", "no evidence", "not stated", "not in the documents", "no requirement")
DATE_RE = re.compile(
    r"\d{1,2}-[A-Za-z]{3}-\d{4}(?:\s+\d{2}:\d{2}:\d{2})?"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s*[AP]M(?:\s*[A-Z]{2,5})?)?"
    r"|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\s*[A-Z]{2,5})?)?"
    r"|\d{1,2}/\d{1,2}/\d{4}",
    re.IGNORECASE,
)
MONTH_YEAR_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+of)?\s+\d{4}\b",
    re.IGNORECASE,
)
STOP = frozenset({"the", "and", "for", "with", "this", "that", "from", "are", "was", "were"})


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def contained(value: str, text: str) -> bool:
    needle = normalize(value)
    haystack = normalize(text)
    if not needle:
        return False
    if len(needle) <= 6:
        return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None
    return needle in haystack


def date_phrases(text: str) -> list[str]:
    return [match.group(0).strip() for match in DATE_RE.finditer(text)]


def _support(value: str, texts: list[str]) -> bool:
    blob = "\n".join(texts)
    if contained(value, blob):
        return True
    tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9./+-]{2,}", normalize(value))
        if token not in STOP
    ]
    if not tokens:
        return False
    haystack = normalize(blob)
    return all(token in haystack for token in tokens)


def locator_problem(source: SourceRef) -> str | None:
    pdf = source.relative_path.lower().endswith(".pdf")
    if source.bid_id is None:
        return "citation is missing a bid"
    if pdf and source.page is None:
        return "PDF citation is missing a one-based page"
    if not pdf and source.page is not None:
        return "HTML citation invented a page number"
    if not pdf and not source.html_element_id:
        return "HTML citation is missing a locator"
    return None


def _absence_ok(notes: str, evidence_text: str) -> bool:
    lowered = notes.casefold()
    if not any(phrase in lowered for phrase in ABSENCE):
        return False
    for phrase in date_phrases(notes):
        if not contained(phrase, evidence_text):
            return False
    return True


def validate_field(
    draft: DraftField,
    evidence: list[EvidenceHit],
    *,
    search_errors: list[str] | None = None,
) -> FieldValidation:
    by_id = {hit.evidence_id: hit for hit in evidence}
    reasons: list[str] = []
    if search_errors and draft.value is None and not draft.evidence_ids:
        return FieldValidation(
            name=draft.name,
            passed=False,
            status="rejected",
            reasons=[f"Retrieval failed: {search_errors[-1]}"],
            support="unsupported",
        )
    if draft.value is None:
        notes = draft.notes.strip() or "Not found in documents"
        blob = "\n".join(hit.text for hit in evidence)
        if _absence_ok(notes, blob):
            return FieldValidation(
                name=draft.name, passed=True, status="not_found", support="null_ok"
            )
        return FieldValidation(
            name=draft.name,
            passed=False,
            status="rejected",
            reasons=["Null value needs the reason 'Not found in documents'"],
            support="unsupported",
        )
    if not draft.evidence_ids:
        reasons.append("Non-null value has no citation")
    chosen: list[EvidenceHit] = []
    for evidence_id in draft.evidence_ids:
        hit = by_id.get(evidence_id)
        if hit is None:
            reasons.append(f"Fabricated citation {evidence_id}")
            continue
        chosen.append(hit)
        for source in hit.sources:
            problem = locator_problem(source)
            if problem:
                reasons.append(problem)
            if source.bid_id != hit.bid_id:
                reasons.append("Cross-bid citation")
    texts = [hit.text for hit in chosen]
    supported = bool(chosen) and _support(draft.value, texts)
    if chosen and not supported:
        reasons.append("Cited evidence does not support the value")
    if (
        draft.name in DATE_FIELDS
        and not date_phrases(draft.value)
        and not MONTH_YEAR_RE.search(draft.value)
        and draft.value.casefold()
        not in {
            "none",
            "not required",
        }
    ):
        reasons.append("Date field is not a date stated in the evidence")
    if (
        draft.name in IDENTIFIER_FIELDS
        and chosen
        and not any(contained(draft.value, hit.text) for hit in chosen)
    ):
        reasons.append("Identifier is not copied from the cited evidence")
    if draft.name == "Bid Summary" and chosen:
        for number in re.findall(r"\d[\d,./:-]*", draft.value):
            if not any(number in hit.text for hit in chosen):
                reasons.append(f"Summary number {number} is not in the cited evidence")
                break
    if reasons:
        return FieldValidation(
            name=draft.name,
            passed=False,
            status="rejected",
            reasons=reasons,
            support="unsupported",
        )
    return FieldValidation(name=draft.name, passed=True, status="accepted", support="contained")
