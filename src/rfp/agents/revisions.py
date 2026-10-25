"""Requirement-level addendum reconciliation shared by extraction and question answering.

The base solicitation is the baseline. An addendum passage bears on a requirement only when its own
text names the requirement's subject, and the change type comes from explicit wording, never from a
model. Question-and-answer addenda are read one numbered answer at a time; there, a negative answer
("not required") is a clarification unless the addendum also uses amendment wording, because the
question may concern something the solicitation never asked for.

Chronology: an addendum number is a label, not a date. A change wins over a competing one only
when its text names the addendum it supersedes, or when both documents carry distinct issue dates
in their metadata. Competing changes from different addenda that cannot be ordered stay a conflict,
with every source kept. Additions and clarifications coexist; changes within one addendum are
treated as separate requirements. Sentences saying that something remains unchanged are not
changes, and a later document does not erase requirements it never mentions.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from rfp.agents.quotes import verbatim
from rfp.agents.retrieval import cited_pages
from rfp.agents.schemas import AddendumChange, ChangeStatus, ChangeType, EvidenceHit, OutputSource
from rfp.schemas.parsed import Contract, DocumentType

RequirementStatus = Literal["unchanged", "revised", "amended", "conflict"]

MAX_STATEMENT_CHARS = 500
MAX_BASELINE = 4
MIN_REPEAT_CHARS = 20
ITEM = re.compile(r"(?m)^[ \t]*\d{1,3}[.)][ \t]+(?=\S)")
ANSWER = re.compile(r"\banswer\s*[:\-]", re.IGNORECASE)
# A page-number line ("Page 3 | 5", "Page 2 of 4") ends an answer; what follows is page furniture.
PAGE_MARKER = re.compile(r"(?im)^[ \t]*page\s+\d+\s*(?:\||of)\s*\d+|\bpage\s+\d+\s*\|\s*\d+")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"\u201c(])")
NO_CHANGE = re.compile(
    r"\b(?:remains?|stays?|continues? to be)\s+(?:unchanged|the same|in effect)\b"
    r"|\bas previously stated\b|\bno changes?\b|\bnot (?:being )?changed\b"
    r"|\bdoes not anticipate (?:changing|extending|revising)\b"
    r"|\bwill not be (?:changed|extended|revised)\b",
    re.IGNORECASE,
)
_VERB = r"(?:is|are|has been|have been|shall be|will be|hereby)\s+"
STRONG_REMOVE = re.compile(
    _VERB + r"(?:deleted|removed|struck|eliminated|withdrawn|rescinded)\b"
    r"|\bno longer (?:required|applies|apply|needed|necessary|applicable)\b"
    r"|\b(?:delete|strike|remove)\s+(?:the|this|section|item|paragraph|requirement)\b",
    re.IGNORECASE,
)
WEAK_REMOVE = re.compile(
    r"\b(?:is|are|will be)\s+not\s+required\b|\bnot\s+be\s+required\b"
    r"|\bwill\s+not\s+(?:ask|require)\b|\bis\s+not\s+(?:needed|necessary)\b",
    re.IGNORECASE,
)
REPLACE = re.compile(
    r"\bnew\s+(?:\w+\s+){0,2}(?:date|deadline|time|location|value|quantity|requirement)\b"
    r"|" + _VERB + r"(?:amended|changed|revised|replaced|extended|modified|corrected|updated"
    r"|postponed|moved|rescheduled)\b"
    r"|\binstead of\b|\bin lieu of\b|\breplaces\b|\bsupersedes?\b|\bshall now\b|\bwill now be\b"
    r"|\bshould read\b|\bnow reads?\b",
    re.IGNORECASE,
)
ADD = re.compile(
    _VERB + r"added\b|\badd(?:s|ed)?\s+the following\b|\bnew requirement\b"
    r"|\badditional requirement\b|\b(?:must|shall)\s+also\b|\b(?:is|are)\s+also\s+required\b",
    re.IGNORECASE,
)
CLARIFY = re.compile(
    r"\bclarif(?:y|ies|ied|ication)\b|\bminimum requirement\b|\bfor the purposes? of\b",
    re.IGNORECASE,
)
REQUIREMENT = re.compile(
    r"\b(?:must|shall|required|requires|require|requirement|mandatory|will ask|is asking"
    r"|are asking|will need to|needs? to|should be (?:submitted|included|provided|made))\b",
    re.IGNORECASE,
)
SUPERSEDE = re.compile(r"\b(?:supersed|replac|revis|amend|correct|rescind|void)\w*", re.IGNORECASE)
ADDENDUM_REF = re.compile(r"\baddend(?:um|a)\s*(?:no\.?\s*|number\s*|#\s*)?(\d+)\b", re.IGNORECASE)
EXCLUSIVE: frozenset[str] = frozenset({"replace", "remove"})
QUESTION_STOP = frozenset(
    """about above after again against also among answer applies apply asked asks being below
    between both compare comparison could does doing each either every from have having here
    include included includes including information into itself list listed many more most must
    other ours provide provided required requirement requirements require requires same should
    some such than that their them then there these they this those through under until upon
    what when where which while whom whose will with within would your bid bids bidder bidders
    proposal proposals vendor vendors offer offers rfp solicitation document documents state
    states stated terms term need needs needed both differ different difference versus
    device devices product products item items equipment service services district laptop
    laptops computer computers""".split()
)


class RevisionSource(Contract):
    evidence_id: str
    file: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    html_element_id: str | None = None
    document_type: DocumentType
    addendum_number: int | None = None
    quote: str = Field(min_length=1)

    def output(self) -> OutputSource:
        if self.page is not None:
            return OutputSource(file=self.file, page=self.page)
        return OutputSource(file=self.file, html_element_id=self.html_element_id)


class Revision(Contract):
    subject: str
    change_type: ChangeType
    status: ChangeStatus
    reason: str
    addendum_number: int | None = None
    # Issue date of the addendum from document metadata; deadlines inside the text are not it.
    addendum_date: date | None = None
    orderable: bool
    material: bool
    terms: list[str]
    # Addenda this passage says it supersedes, revises or amends, by number.
    supersedes: list[int] = Field(default_factory=list)
    statement: str = Field(min_length=1)
    question: str | None = None
    sources: list[RevisionSource] = Field(min_length=1)

    @property
    def evidence_ids(self) -> set[str]:
        return {source.evidence_id for source in self.sources}


class RequirementHistory(Contract):
    subject: str
    status: RequirementStatus
    reason: str
    baseline: list[RevisionSource] = Field(default_factory=list)
    # Baseline passages that an applied replacement or removal on the same terms retires.
    retired: list[str] = Field(default_factory=list)
    revisions: list[Revision] = Field(default_factory=list)

    @property
    def material(self) -> list[Revision]:
        return [r for r in self.revisions if r.material and r.status in {"applied", "conflict"}]

    @property
    def active_baseline(self) -> list[RevisionSource]:
        return [source for source in self.baseline if source.evidence_id not in self.retired]


def _term_pattern(term: str) -> re.Pattern[str]:
    words = term.casefold().split()
    if len(words) == 1 and len(words[0]) <= 4:
        return re.compile(rf"(?<![\w-]){re.escape(words[0])}s?(?![\w-])", re.IGNORECASE)
    last = words[-1]
    stem = last[:-1] if last.endswith("y") and len(last) > 4 else last
    body = r"\s+".join([*(re.escape(w) for w in words[:-1]), re.escape(stem)])
    return re.compile(rf"(?<![\w-]){body}\w*", re.IGNORECASE)


def matched_terms(terms: Iterable[str], text: str) -> list[str]:
    return [term for term in terms if _term_pattern(term).search(text)]


def question_terms(question: str) -> list[str]:
    """Content words of a question that can name a requirement; generic words are skipped."""
    words = re.findall(r"[a-z][a-z0-9-]{3,}", question.casefold())
    return list(dict.fromkeys(word for word in words if word not in QUESTION_STOP))


def classify(statement: str, qa: bool) -> ChangeType:
    if STRONG_REMOVE.search(statement):
        return "remove"
    if REPLACE.search(statement):
        return "replace"
    if ADD.search(statement):
        return "add"
    if WEAK_REMOVE.search(statement):
        return "clarify" if qa else "remove"
    if qa or CLARIFY.search(statement):
        return "clarify"
    return "unknown"


def _units(text: str, qa: bool) -> list[tuple[str | None, str]]:
    """(question, answer) pairs of a Q&A passage, or (None, paragraph) for other addendum text.

    In a Q&A passage a numbered item without its answer (cut off by the chunk boundary) is a
    vendor question, not an addendum statement, so it is skipped.
    """
    starts = sorted({0, *(m.start() for m in ITEM.finditer(text)), len(text)})
    pieces = [text[a:b] for a, b in zip(starts, starts[1:], strict=False) if text[a:b].strip()]
    units: list[tuple[str | None, str]] = []
    for piece in pieces:
        found = list(ANSWER.finditer(piece))
        if found:
            answer = piece[found[-1].end() :]
            marker = PAGE_MARKER.search(answer)
            units.append(
                (piece[: found[-1].start()], answer[: marker.start()] if marker else answer)
            )
        elif not (qa and ITEM.match(piece)):
            marker = PAGE_MARKER.search(piece)
            units.append((None, piece[: marker.start()] if marker else piece))
    return units


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= MAX_STATEMENT_CHARS:
        return flat
    words: list[str] = []
    for word in flat.split():
        if len(" ".join([*words, word])) > MAX_STATEMENT_CHARS:
            break
        words.append(word)
    return " ".join(words)


def _statement(
    question: str | None, answer: str, terms: list[str]
) -> tuple[str, list[str], bool] | None:
    """The answer sentences naming the subject, their terms, and whether the answer names it.

    An answer that names the subject only through the vendor question is kept as context but is
    never material on its own.
    """
    sentences = [s for s in SENTENCE.split(answer.strip()) if s.strip()]
    indexes = [
        index
        for index, sentence in enumerate(sentences)
        if not NO_CHANGE.search(sentence) and matched_terms(terms, sentence)
    ]
    if indexes:
        span = " ".join(sentences[indexes[0] : indexes[-1] + 1])
        return _clip(span), matched_terms(terms, span), True
    kept = [s for s in sentences if not NO_CHANGE.search(s)]
    if question and kept and len(" ".join(kept).split()) >= 4:
        found = matched_terms(terms, question)
        if found:
            return _clip(" ".join(kept)), found, False
    return None


def _in_bid(hit: EvidenceHit, bid_uuid: UUID | None) -> bool:
    if any(source.bid_id != hit.bid_id for source in hit.sources):
        return False
    return bid_uuid is None or hit.bid_id == bid_uuid


def _source(hit: EvidenceHit, quote: str) -> RevisionSource | None:
    excerpt = verbatim(quote, hit.text)
    if excerpt is None:
        return None
    ref = hit.sources[0]
    pages = cited_pages(ref, hit.text, excerpt) if ref.spans else []
    return RevisionSource(
        evidence_id=hit.evidence_id,
        file=ref.file,
        page=(pages[0] if pages else ref.page) if ref.page is not None else None,
        html_element_id=None if ref.page is not None else ref.html_element_id,
        document_type=ref.document_type,
        addendum_number=ref.addendum_number,
        quote=excerpt,
    )


def _key(text: str) -> str:
    return " ".join(re.findall(r"[0-9a-z]+", text.casefold()))


def _with_source(revision: Revision, source: RevisionSource) -> Revision:
    if any(s.evidence_id == source.evidence_id for s in revision.sources):
        return revision
    return revision.model_copy(update={"sources": [*revision.sources, source]})


def _merge_repeats(revisions: list[Revision]) -> list[Revision]:
    """One record per statement: a statement repeated inside a longer one from the same addendum
    is the same change, kept with the shorter wording and every source."""
    ordered = sorted(revisions, key=lambda r: len(r.statement))
    kept: list[Revision] = []
    for revision in ordered:
        key = _key(revision.statement)
        host = next(
            (
                index
                for index, other in enumerate(kept)
                if other.addendum_number == revision.addendum_number
                and other.change_type == revision.change_type
                and len(_key(other.statement)) >= MIN_REPEAT_CHARS
                and _key(other.statement) in key
            ),
            None,
        )
        if host is None:
            kept.append(revision)
            continue
        merged = kept[host]
        for source in revision.sources:
            merged = _with_source(merged, source)
        kept[host] = merged.model_copy(
            update={
                "material": merged.material or revision.material,
                "terms": list(dict.fromkeys([*merged.terms, *revision.terms])),
            }
        )
    return kept


def detect(
    subject: str,
    terms: Iterable[str],
    hits: list[EvidenceHit],
    bid_uuid: UUID | None = None,
    dates: Mapping[str, date] | None = None,
) -> list[Revision]:
    """Unresolved revision candidates: addendum statements that name the subject."""
    terms = list(dict.fromkeys(terms))
    if not terms:
        return []
    found: dict[tuple[int | None, str], Revision] = {}
    for hit in hits:
        if hit.document_type != DocumentType.addendum or not _in_bid(hit, bid_uuid):
            continue
        qa = bool(ANSWER.search(hit.text))
        for question, answer in _units(hit.text, qa):
            picked = _statement(question, answer, terms)
            if picked is None:
                continue
            statement, matched, own = picked
            source = _source(hit, statement)
            if source is None:
                continue
            key = (hit.addendum_number, _key(statement))
            if key in found:
                found[key] = _with_source(found[key], source)
                continue
            change_type = classify(statement, qa)
            found[key] = Revision(
                subject=subject,
                change_type=change_type,
                status="not_applied",
                reason="",
                addendum_number=hit.addendum_number,
                addendum_date=(dates or {}).get(hit.sources[0].relative_path),
                orderable=False,
                material=own
                and (
                    change_type in {"replace", "remove", "add"}
                    or (change_type == "clarify" and bool(REQUIREMENT.search(statement)))
                ),
                terms=matched,
                supersedes=_superseded_numbers(answer, hit.addendum_number),
                statement=source.quote,
                question=" ".join(question.split())[:300] if question else None,
                sources=[source],
            )
    return sorted(
        _merge_repeats(list(found.values())),
        key=lambda r: (
            r.addendum_number is None,
            r.addendum_number or 0,
            r.sources[0].file,
            r.sources[0].page or 0,
            r.statement,
        ),
    )


def _superseded_numbers(text: str, own: int | None) -> list[int]:
    """Addendum numbers named in a sentence that also says it supersedes or amends them."""
    found: set[int] = set()
    for sentence in SENTENCE.split(text):
        if SUPERSEDE.search(sentence):
            found.update(int(n) for n in ADDENDUM_REF.findall(sentence))
    return sorted(found - {own} if own is not None else found)


def supersedes(later: Revision, earlier: Revision) -> bool:
    """True only when wording or issue dates establish that `later` comes after `earlier`."""
    if (
        earlier.addendum_number is not None
        and later.addendum_number != earlier.addendum_number
        and earlier.addendum_number in later.supersedes
    ):
        return True
    return (
        later.addendum_date is not None
        and earlier.addendum_date is not None
        and later.addendum_date > earlier.addendum_date
    )


def _competing(a: Revision, b: Revision) -> bool:
    if not (a.material and b.material) or a.addendum_number == b.addendum_number:
        return False
    if not (a.change_type in EXCLUSIVE or b.change_type in EXCLUSIVE):
        return False
    return bool(set(a.terms) & set(b.terms))


def resolve(revisions: list[Revision]) -> list[Revision]:
    """Statuses for each candidate: applied, superseded, conflict or not_applied."""
    resolved: list[Revision] = []
    for revision in revisions:
        if revision.change_type == "unknown":
            resolved.append(
                revision.model_copy(
                    update={
                        "status": "not_applied",
                        "reason": "The addendum names the subject but states no change.",
                    }
                )
            )
            continue
        rivals = [
            other for other in revisions if other is not revision and _competing(revision, other)
        ]
        later = [other for other in rivals if supersedes(other, revision)]
        unordered = [
            other
            for other in rivals
            if not supersedes(other, revision) and not supersedes(revision, other)
        ]
        if later:
            update = {
                "status": "superseded",
                "orderable": True,
                "reason": "Superseded by addendum "
                + ", ".join(str(other.addendum_number) for other in later)
                + ", which states that it revises this one.",
            }
        elif unordered:
            update = {
                "status": "conflict",
                "orderable": False,
                "reason": "Competing change in addendum "
                + ", ".join(str(other.addendum_number) for other in unordered)
                + "; no metadata or wording establishes which came later.",
            }
        else:
            update = {
                "status": "applied",
                "orderable": bool(rivals),
                "reason": _applied_reason(revision, bool(rivals)),
            }
        resolved.append(revision.model_copy(update=update))
    return resolved


def _applied_reason(revision: Revision, ordered: bool) -> str:
    label = (
        f"Addendum {revision.addendum_number}"
        if revision.addendum_number is not None
        else "An addendum"
    )
    verb = {
        "replace": "replaces",
        "remove": "removes",
        "add": "adds",
        "clarify": "clarifies",
    }[revision.change_type]
    reason = f"{label} {verb} this requirement in its own words"
    if ordered:
        reason += " and explicitly supersedes the competing change"
    return reason + "."


def _baseline(
    terms: list[str], hits: list[EvidenceHit], bid_uuid: UUID | None
) -> list[RevisionSource]:
    found: list[RevisionSource] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.document_type == DocumentType.addendum or not _in_bid(hit, bid_uuid):
            continue
        for line in hit.text.splitlines():
            flat = " ".join(line.split())
            if not flat or len(flat) > 240 or not matched_terms(terms, flat):
                continue
            if _key(flat) in seen:
                continue
            source = _source(hit, flat)
            if source is None:
                continue
            seen.add(_key(flat))
            found.append(source)
            if len(found) >= MAX_BASELINE:
                return found
    return found


def reconcile(
    subject: str,
    terms: Iterable[str],
    hits: list[EvidenceHit],
    bid_uuid: UUID | None = None,
    dates: Mapping[str, date] | None = None,
) -> RequirementHistory:
    """Baseline, every addendum candidate with its status, and the requirement's overall state."""
    terms = list(dict.fromkeys(terms))
    revisions = resolve(detect(subject, terms, hits, bid_uuid, dates))
    baseline = _baseline(terms, hits, bid_uuid)
    material = [r for r in revisions if r.material]
    retiring = [r for r in material if r.status == "applied" and r.change_type in EXCLUSIVE]
    retired = [
        source.evidence_id
        for source in baseline
        if any(matched_terms(r.terms, source.quote) for r in retiring)
    ]
    if any(r.status == "conflict" for r in material):
        status: RequirementStatus = "conflict"
        reason = "Addenda make competing changes that cannot be ordered."
    elif retiring:
        status = "revised"
        reason = "An addendum replaces or removes part of the original requirement."
    elif any(r.status == "applied" for r in material):
        status = "amended"
        reason = "An addendum adds to or clarifies the original requirement."
    else:
        status = "unchanged"
        reason = "No retrieved addendum changes this requirement."
    return RequirementHistory(
        subject=subject,
        status=status,
        reason=reason,
        baseline=baseline,
        retired=retired,
        revisions=revisions,
    )


def describe(revision: Revision) -> str:
    label = (
        f"Addendum {revision.addendum_number}"
        if revision.addendum_number is not None
        else "An addendum"
    )
    ids = ", ".join(sorted(revision.evidence_ids))
    return f'{label} ({revision.change_type}, {revision.status}) [{ids}]: "{revision.statement}"'


def to_changes(field: str, history: RequirementHistory) -> list[AddendumChange]:
    """Material revisions as output change records, citing the addendum and the baseline."""
    original = history.baseline[0] if history.baseline else None
    changes: list[AddendumChange] = []
    for revision in history.revisions:
        if not revision.material or revision.status == "not_applied":
            continue
        sources = [source.output() for source in revision.sources]
        if original is not None and revision.change_type in EXCLUSIVE:
            sources.append(original.output())
        unique = list({(s.file, s.page, s.html_element_id): s for s in sources}.values())
        revised = None
        if revision.status != "conflict" and revision.change_type != "remove":
            revised = revision.statement
        changes.append(
            AddendumChange(
                field=field,
                original_value=original.quote
                if original is not None and revision.change_type in EXCLUSIVE
                else None,
                revised_value=revised,
                addendum_number=revision.addendum_number,
                sources=unique,
                explanation=revision.reason,
                conflict=revision.status == "conflict",
                change_type=revision.change_type,
                status=revision.status,
                addendum_date=revision.addendum_date,
                orderable=revision.orderable,
                quote=revision.statement,
            )
        )
    return changes
