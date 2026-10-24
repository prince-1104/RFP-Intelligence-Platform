"""Apply an addendum only when its text establishes a change. A higher number is not enough."""

import re
from dataclasses import dataclass

from rfp.agents.schemas import AddendumChange, DraftField, EvidenceHit
from rfp.agents.validator import contained, date_phrases, normalize
from rfp.schemas.parsed import DocumentType

DUE_DATE_CUES = (
    "new due date",
    "due date will be",
    "extend the due date",
    "deadline is extended",
    "submission deadline is extended",
    "is amended to",
    "supersedes",
)
BASE_DUE_RE = re.compile(
    r"\b(?:solicitation due|proposals? due|bids? due|due date|closing date|submission deadline"
    r"|due no later than)\b",
    re.IGNORECASE,
)
LABEL_WINDOW = 60


@dataclass(frozen=True)
class ReconciliationResult:
    change: AddendumChange | None = None
    reject_reason: str | None = None


def _cue_hits(evidence: list[EvidenceHit]) -> list[EvidenceHit]:
    found = []
    for hit in evidence:
        if hit.document_type != DocumentType.addendum:
            continue
        text = hit.text.casefold()
        if any(cue in text for cue in DUE_DATE_CUES):
            found.append(hit)
    return found


def _phrase_after_cue(text: str) -> str | None:
    lowered = text.casefold()
    indexes = [lowered.find(cue) for cue in DUE_DATE_CUES if cue in lowered]
    if not indexes:
        return None
    start = min(indexes)
    phrases = date_phrases(text[start:]) or date_phrases(text)
    return phrases[0] if phrases else None


def _labelled_due_dates(text: str) -> list[str]:
    """Dates that directly follow a due-date label; other dates in the RFP are not the deadline."""
    flat = " ".join(text.split())
    found = []
    for label in BASE_DUE_RE.finditer(flat):
        phrases = date_phrases(flat[label.end() : label.end() + LABEL_WINDOW])
        if phrases:
            found.append(phrases[0])
    return found


def reconcile_due_date(draft: DraftField, evidence: list[EvidenceHit]) -> ReconciliationResult:
    overriding = _cue_hits(evidence)
    if not overriding:
        return ReconciliationResult()
    phrases: list[tuple[EvidenceHit, str]] = []
    for hit in overriding:
        phrase = _phrase_after_cue(hit.text)
        if phrase:
            phrases.append((hit, phrase))
    unique: list[tuple[EvidenceHit, str]] = []
    for hit, phrase in phrases:
        if not any(normalize(phrase) == normalize(existing) for _, existing in unique):
            unique.append((hit, phrase))
    base_phrases = [
        phrase
        for hit in evidence
        if hit.document_type == DocumentType.rfp
        for phrase in _labelled_due_dates(hit.text)
    ]
    if len(unique) > 1:
        listed = "; ".join(f"addendum {hit.addendum_number}: {phrase}" for hit, phrase in unique)
        return ReconciliationResult(
            change=AddendumChange(
                field="Due Date",
                original_value=base_phrases[0] if base_phrases else None,
                revised_value=None,
                addendum_number=None,
                sources=[hit.sources[0].output() for hit, _ in unique],
                explanation=(
                    f"Addenda state conflicting due dates ({listed}). "
                    "No addendum number is treated as the winner."
                ),
                conflict=True,
            ),
            reject_reason="conflicting addendum due dates",
        )
    if not unique:
        return ReconciliationResult(
            reject_reason="An addendum mentions a due-date change but states no replacement date",
        )
    hit, revised = unique[0]
    original = next((phrase for phrase in base_phrases if not contained(phrase, revised)), None)
    matches = draft.value is not None and (
        contained(revised, draft.value) or contained(draft.value, revised)
    )
    change = AddendumChange(
        field="Due Date",
        original_value=original,
        revised_value=revised,
        addendum_number=hit.addendum_number,
        sources=[hit.sources[0].output()]
        + ([source.output() for source in _base_sources(evidence, original)] if original else []),
        explanation=(
            f"Addendum {hit.addendum_number} states a new due date. "
            "The addendum text, not the addendum number alone, is the reason for the change."
        ),
        conflict=False,
    )
    if not matches:
        return ReconciliationResult(
            change=change,
            reject_reason="addendum states a different due date than the draft",
        )
    return ReconciliationResult(change=change)


def _base_sources(evidence: list[EvidenceHit], original: str | None) -> list:
    if not original:
        return []
    return [
        hit.sources[0]
        for hit in evidence
        if hit.document_type == DocumentType.rfp and contained(original, hit.text)
    ]


def reconcile_field(
    name: str, draft: DraftField, evidence: list[EvidenceHit]
) -> ReconciliationResult:
    if name != "Due Date":
        return ReconciliationResult()
    return reconcile_due_date(draft, evidence)
