"""Apply an addendum only when its text establishes a change. A higher number is not enough."""

import re
from dataclasses import dataclass
from uuid import UUID

from rfp.agents.revisions import (
    EXCLUSIVE,
    RequirementHistory,
    Revision,
    describe,
    reconcile,
    to_changes,
)
from rfp.agents.schemas import AddendumChange, DraftField, EvidenceHit
from rfp.agents.validator import contained, date_phrases, normalize
from rfp.schemas.parsed import DocumentType

# Subject terms per extraction field. A term must appear in an addendum statement (or in the
# vendor question it answers) for that statement to count as a change to the field.
FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "Bid Submission Type": (
        "sealed",
        "electronic submission",
        "submitted electronically",
        "responding electronically",
        "online portal",
        "hard copy",
        "hard copies",
        "pdf version",
    ),
    "Term of Bid": (
        "contract term",
        "initial term",
        "renewal",
        "term of the contract",
        "contract period",
    ),
    "Pre Bid Meeting": ("pre-bid", "pre-proposal", "prebid", "pre bid"),
    "Installation": (
        "installation",
        "imaging",
        "white glove",
        "deployment",
        "etching",
        "etch",
        "asset tag",
        "decal",
        "autopilot",
    ),
    "Bid Bond Requirement": (
        "bid bond",
        "bid security",
        "performance bond",
        "payment bond",
        "surety",
    ),
    "Delivery Date": ("deliver", "delivery", "deliveries", "lead time"),
    "Payment Terms": ("payment", "invoice", "invoicing", "net 30", "net 45", "net 60"),
    "Any Additional Documentation Required": (
        "affidavit",
        "form",
        "certificate",
        "certification",
        "w-9",
    ),
    "MFG for Registration": (
        "authorized reseller",
        "reseller",
        "manufacturer authorization",
        "authorized dealer",
    ),
    "Contract or Cooperative to use": (
        "cooperative",
        "co-op",
        "interlocal",
        "piggyback",
    ),
    "Product": ("quantity", "quantities"),
    "Product Specification": (
        "warranty",
        "warranties",
        "specification",
        "usb",
        "battery",
        "processor",
        "memory",
        "storage",
        "webcam",
        "accidental damage",
    ),
}

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
    changes: tuple[AddendumChange, ...] = ()
    history: RequirementHistory | None = None

    @property
    def all_changes(self) -> tuple[AddendumChange, ...]:
        if self.changes:
            return self.changes
        return (self.change,) if self.change is not None else ()


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
                status="conflict",
                orderable=False,
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
        status="applied",
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


def _reflects(draft: DraftField, revision: Revision) -> bool:
    if revision.evidence_ids & set(draft.evidence_ids):
        return True
    return bool(draft.value) and contained(revision.statement, draft.value or "")


def _requirement_reject(name: str, draft: DraftField, history: RequirementHistory) -> str | None:
    conflicts = [r for r in history.material if r.status == "conflict"]
    if conflicts:
        listed = "; ".join(describe(r) for r in conflicts)
        return f"conflicting addendum changes for {name}: {listed}"
    for revision in history.material:
        if revision.change_type not in EXCLUSIVE or _reflects(draft, revision):
            continue
        verb = "replaces" if revision.change_type == "replace" else "removes"
        label = (
            f"Addendum {revision.addendum_number}"
            if revision.addendum_number is not None
            else "An addendum"
        )
        return (
            f'{label} {verb} this requirement: "{revision.statement[:200]}"; '
            "the value must reflect the addendum"
        )
    return None


def reconcile_field(
    name: str,
    draft: DraftField,
    evidence: list[EvidenceHit],
    bid_uuid: UUID | None = None,
) -> ReconciliationResult:
    """Due Date keeps its date-specific rule; other listed fields use requirement histories.

    Additions and clarifications are recorded without rejecting the value. A replacement or
    removal that the value ignores, or competing changes that cannot be ordered, reject it.
    """
    if name == "Due Date":
        return reconcile_due_date(draft, evidence)
    terms = FIELD_TERMS.get(name)
    if not terms:
        return ReconciliationResult()
    history = reconcile(name, terms, evidence, bid_uuid)
    changes = tuple(to_changes(name, history))
    return ReconciliationResult(
        change=changes[0] if changes else None,
        reject_reason=_requirement_reject(name, draft, history),
        changes=changes,
        history=history,
    )
