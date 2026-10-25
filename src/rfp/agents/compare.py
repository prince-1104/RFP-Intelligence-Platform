"""Cross-bid comparison over two or more explicitly selected bids.

Evidence is gathered separately for each bid with the single-bid retrieval plan, so every search
carries exactly one bid filter. Passages are shown to the model in one section per bid, with
evidence ids that name the section (A1, A2 for the first bid, B1 for the second). The model returns
claims per bid; each bid's claims are checked with `check_reply` against that bid's passages only,
and a claim citing another bid's passage is dropped. Addendum revisions are detected per bid from
that bid's passages only, and a material one the bid's claims do not cite triggers a correction and
then a limitation. Bids without validated claims are reported as
"Not found in documents". Aspect relations (identical, different, one_sided) are computed
lexically from the validated claims, never taken from the model.
"""

import re
import string
import time
import uuid
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from rfp.agents.checklist import candidates, list_keyword
from rfp.agents.errors import ExtractionError, ModelError
from rfp.agents.grounding import facts
from rfp.agents.llm import LanguageModel, ModelRequest, ModelResponse, account, parse_json_object
from rfp.agents.qa import (
    NOT_FOUND,
    Checked,
    CitationCheck,
    Completeness,
    ModelClaim,
    ModelPart,
    ModelReply,
    ModelUnresolved,
    QACitation,
    QAClaim,
    QAConfig,
    QAStatus,
    QAValidation,
    _event,
    _evidence_block,
    _items_block,
    answer_limitations,
    check_reply,
    gather_evidence,
    kept_validation,
    question_revisions,
    revisions_block,
    usable,
)
from rfp.agents.retrieval import query_hash, retrieve
from rfp.agents.revisions import Revision
from rfp.agents.runner import resolve_bid
from rfp.agents.schemas import EvidenceHit, ModelMetadata, TokenUsage, TraceEvent
from rfp.agents.trace import clock
from rfp.agents.validator import normalize
from rfp.schemas.parsed import Contract
from rfp.schemas.search import RetrievalMode, SearchRequest, SearchResponse

MAX_BIDS = 4
GENERAL = "general"
Relation = Literal["identical", "different", "one_sided"]
ComparisonStatus = Literal["answered", "partial", "not_found"]
COMPARE_WORDS = re.compile(
    r"\b(compare|comparing|comparison|contrast|differences?|similarities)\b"
    r"|\b(?:of|for|in|across|between|among)\s+(?:both|the two|all|these|each of the)\s+bids\b"
    r"|\b(?:both|each|the two|all)\s+bids\b",
    re.IGNORECASE,
)


class BidFinding(Contract):
    bid_id: str
    status: Literal["answered", "not_found"]
    answer: str = Field(min_length=1)
    claims: list[QAClaim] = Field(default_factory=list)
    citations: list[QACitation] = Field(default_factory=list)
    completeness: Completeness | None = None
    limitations: list[str] = Field(default_factory=list)
    validation: QAValidation
    evidence_count: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> "BidFinding":
        if self.status == "not_found":
            if self.answer != NOT_FOUND or self.claims or self.citations:
                raise ValueError("not_found findings must be the abstention text with no citations")
            return self
        if not self.claims or not self.validation.passed:
            raise ValueError("answered findings need validated, cited claims")
        known = {citation.citation_id for citation in self.citations}
        if any(cid not in known for claim in self.claims for cid in claim.citation_ids):
            raise ValueError("every claim must reference a listed citation")
        return self


class ComparisonAspect(Contract):
    aspect: str
    relation: Relation
    claims: dict[str, list[str]]


class ComparisonResponse(Contract):
    mode: Literal["comparison"] = "comparison"
    question: str
    bid_ids: list[str] = Field(min_length=2)
    status: ComparisonStatus
    answer: str = Field(min_length=1)
    findings: list[BidFinding]
    aspects: list[ComparisonAspect] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    validation: QAValidation
    trace_id: str
    model: ModelMetadata
    evidence_count: dict[str, int]

    @model_validator(mode="after")
    def consistent(self) -> "ComparisonResponse":
        if [finding.bid_id for finding in self.findings] != self.bid_ids:
            raise ValueError("findings must follow the selected bid order")
        missing = [f.bid_id for f in self.findings if f.status == "not_found"]
        if missing != self.missing_evidence:
            raise ValueError("missing_evidence must list every bid without a finding")
        expected = (
            "not_found"
            if len(missing) == len(self.findings)
            else "partial"
            if missing
            else "answered"
        )
        if self.status != expected:
            raise ValueError("status does not match the per-bid findings")
        if self.status == "not_found" and self.answer != NOT_FOUND:
            raise ValueError("a comparison with no findings must be the abstention text")
        return self


class ComparisonTrace(Contract):
    trace_id: str
    mode: Literal["comparison"] = "comparison"
    bid_ids: list[str]
    bid_uuids: list[UUID]
    question_hash: str
    question_chars: int
    events: list[TraceEvent]
    status: ComparisonStatus
    validation: QAValidation
    token_usage: TokenUsage
    model: ModelMetadata


class ModelAspectClaim(ModelClaim):
    aspect: str = ""


class ModelBidReply(ModelPart):
    bid_id: str
    status: QAStatus = "answered"
    claims: list[ModelAspectClaim] = Field(default_factory=list)
    unresolved: list[ModelUnresolved] = Field(default_factory=list)


class ModelComparison(ModelPart):
    bids: list[ModelBidReply] = Field(default_factory=list)


class Section(Contract):
    letter: str
    label: str
    bid_uuid: UUID
    hits: list[EvidenceHit] = Field(default_factory=list)
    items: dict[str, list[str]] = Field(default_factory=dict)
    revisions: list[Revision] = Field(default_factory=list)


def resolve_bids(values: list[str]) -> list[tuple[str, UUID]]:
    """Explicit bid labels or UUIDs; at least two, at most MAX_BIDS, no duplicates."""
    if len(values) < 2:
        raise ExtractionError("invalid_comparison", "Select at least two bids to compare")
    if len(values) > MAX_BIDS:
        raise ExtractionError("invalid_comparison", f"Compare at most {MAX_BIDS} bids at a time")
    resolved = [resolve_bid(value) for value in values]
    labels = [label.casefold() for label, _ in resolved]
    uuids = [bid_uuid for _, bid_uuid in resolved]
    if len(set(labels)) != len(labels) or len(set(uuids)) != len(uuids):
        raise ExtractionError("duplicate_bid", "Each bid may be selected only once")
    return resolved


def focus_query(question: str) -> str:
    """The question without comparison wording, used for each bid's retrieval."""
    focused = " ".join(COMPARE_WORDS.sub(" ", question).split()).strip(string.punctuation + " ")
    focused = re.sub(r"^(?:the|of|and)\s+", "", focused, flags=re.IGNORECASE)
    return focused if len(focused) >= 3 else question


SYSTEM = (
    "You compare procurement bids using only the numbered passages. Passages are grouped in one "
    "section per bid; an evidence id starts with its section letter (A1 belongs to bid section "
    "A). Reply with JSON only:\n"
    '{"bids": [{"bid_id": "<bid label>", "status": "answered" | "not_found",\n'
    '           "claims": [{"aspect": "short topic, e.g. warranty period",\n'
    '                       "text": "one short factual sentence about this bid",\n'
    '                       "citations": [{"evidence_id": "A1",\n'
    '                                      "quote": "exact text from A1"}]}],\n'
    '           "unresolved": [{"item": "...", "reason": "..."}]}]}\n'
    "Rules:\n"
    "- Include one entry per bid, in the order given. A claim about a bid may only cite passages "
    "from that bid's section. Never use one bid's passages for another bid.\n"
    "- Do not name the bid in claim text; the bid_id field says which bid a claim is about.\n"
    "- Use the same aspect label for the same topic in every bid so they can be compared.\n"
    "- Each quote must be copied character for character from one passage: a single contiguous "
    "span, no ellipses, no rewording, no added punctuation.\n"
    "- Each claim may only state what its own quotes say. Every date, number, name and qualifier "
    "in a claim must appear in its quotes. Words such as must, shall or required may only appear "
    "in a claim when its quote says so.\n"
    "- When a label and its value are separated in a passage (for example table cells), cite "
    "them as two separate quotes.\n"
    f'- If a bid\'s passages do not answer the question, give that bid status "not_found" with '
    f'no claims (it will be reported as "{NOT_FOUND}"). Do not guess from the other bid.\n'
    "- Do not state differences or similarities in claim text; they are derived from the "
    "per-bid claims.\n"
    "Never invent documents, pages, quotes or facts."
)


def _sections_block(sections: list[Section], config: QAConfig) -> str:
    blocks = []
    for section in sections:
        heading = f"=== Section {section.letter}: bid {section.label} ==="
        body = (
            _evidence_block(section.hits, config.excerpt_chars)
            if section.hits
            else "(No passages were retrieved for this bid.)\n"
        )
        items = _items_block(section.items).strip()
        revisions = revisions_block(section.revisions).strip()
        blocks.append("\n".join(part for part in (heading, items, revisions, body) if part))
    return "\n".join(blocks)


def comparison_request(
    question: str, sections: list[Section], config: QAConfig, reasons: list[str] | None = None
) -> ModelRequest:
    bids = ", ".join(f"{s.label} (section {s.letter})" for s in sections)
    rejected = ""
    if reasons:
        listed = "\n".join(f"- {reason}" for reason in reasons)
        rejected = (
            f"\n\nYour previous reply was rejected:\n{listed}\n"
            "Copy quotes exactly from the right bid's section, drop any detail your quotes do "
            "not state, or answer not_found for that bid."
        )
    return ModelRequest(
        purpose="qa:compare" if not reasons else "qa:compare_correct",
        system=SYSTEM,
        user=(
            f"Question: {question}\nBids: {bids}{rejected}\n\n"
            f"Passages:\n{_sections_block(sections, config)}"
        ),
    )


def parse_comparison(text: str) -> ModelComparison:
    payload = parse_json_object(text)
    try:
        return ModelComparison.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise ModelError(
            "llm_malformed", "Model reply did not match the comparison schema"
        ) from exc


def _aspect(value: str) -> str:
    label = " ".join(value.split()).strip(string.punctuation + " ").casefold()[:60]
    return label if label and not facts(label) else GENERAL


def check_bid(
    question: str, section: Section, entry: ModelBidReply | None, owners: dict[str, str]
) -> tuple[Checked, dict[str, str], ModelReply | None]:
    """Validate one bid's claims against its own passages. Returns claim text -> aspect."""
    if entry is None:
        return Checked(reply_reasons=["The reply has no entry for this bid"]), {}, None
    retained: list[ModelClaim] = []
    aspects: dict[str, str] = {}
    foreign: list[CitationCheck] = []
    reasons: list[str] = []
    dropped: list[str] = []
    for claim in entry.claims:
        text = " ".join(claim.text.split())
        crossed = [
            c.evidence_id
            for c in claim.citations
            if owners.get(c.evidence_id) not in (None, section.label)
        ]
        if crossed:
            for eid in crossed:
                foreign.append(CitationCheck(evidence_id=eid, valid=False, reason="cross_bid"))
            reasons.append(
                f"Claim '{text[:80]}': citation {', '.join(crossed)} belongs to bid "
                f"{owners[crossed[0]]}, not {section.label}"
            )
            dropped.append(text)
            continue
        retained.append(ModelClaim(text=claim.text, citations=claim.citations))
        aspects[text] = _aspect(claim.aspect)
    reply = ModelReply(status=entry.status, claims=retained, unresolved=entry.unresolved)
    checked = check_reply(question, reply, section.hits, section.items, section.revisions)
    checked.checks = foreign + checked.checks
    checked.reasons = reasons + checked.reasons
    checked.dropped = dropped + checked.dropped
    if entry.status == "conflict":
        checked.reply_reasons.append("Comparison findings are answered or not_found per bid")
        checked.reasons.append("Comparison findings are answered or not_found per bid")
    return checked, aspects, reply


def _relation(
    per_bid: dict[str, list[str]], citations: dict[str, list[str]], total: int
) -> Relation:
    if len(per_bid) < 2:
        return "one_sided" if total > 1 else "identical"
    texts = {frozenset(normalize(t) for t in claims) for claims in per_bid.values()}
    quotes = {frozenset(normalize(q) for q in citations[bid]) for bid in per_bid}
    return "identical" if len(texts) == 1 or len(quotes) == 1 else "different"


def build_aspects(
    findings: list[BidFinding], aspects: dict[str, dict[str, str]]
) -> list[ComparisonAspect]:
    grouped: dict[str, dict[str, list[str]]] = {}
    excerpts: dict[str, dict[str, list[str]]] = {}
    for finding in findings:
        by_id = {c.citation_id: c.excerpt for c in finding.citations}
        for claim in finding.claims:
            name = aspects.get(finding.bid_id, {}).get(claim.text, GENERAL)
            grouped.setdefault(name, {}).setdefault(finding.bid_id, []).append(claim.text)
            excerpts.setdefault(name, {}).setdefault(finding.bid_id, []).extend(
                by_id[cid] for cid in claim.citation_ids
            )
    total = len(findings)
    return [
        ComparisonAspect(
            aspect=name,
            relation=_relation(per_bid, excerpts[name], total),
            claims=per_bid,
        )
        for name, per_bid in grouped.items()
    ]


def _readable(findings: list[BidFinding], aspects: list[ComparisonAspect]) -> str:
    if all(finding.status == "not_found" for finding in findings):
        return NOT_FOUND
    lines = [f"{finding.bid_id}: {finding.answer}" for finding in findings]
    labels = [finding.bid_id for finding in findings]
    for item in aspects:
        present = list(item.claims)
        if item.relation == "one_sided":
            absent = ", ".join(label for label in labels if label not in item.claims)
            lines.append(
                f"{item.aspect}: stated only for {', '.join(present)}; "
                f"no validated statement for {absent}."
            )
        elif item.relation == "identical":
            lines.append(f"{item.aspect}: same cited statement for {', '.join(present)}.")
        else:
            lines.append(f"{item.aspect}: cited statements differ between {', '.join(present)}.")
    return "\n".join(lines)


def _finding(
    section: Section, checked: Checked | None, validation: QAValidation, list_question: bool
) -> BidFinding:
    if checked is None:
        return BidFinding(
            bid_id=section.label,
            status="not_found",
            answer=NOT_FOUND,
            validation=validation,
            evidence_count=len(section.hits),
        )
    return BidFinding(
        bid_id=section.label,
        status="answered",
        answer=" ".join(claim.text for claim in checked.claims),
        claims=checked.claims,
        citations=checked.citations,
        completeness=checked.completeness,
        limitations=answer_limitations(checked, list_question),
        validation=validation,
        evidence_count=len(section.hits),
    )


def compare(
    bids: list[tuple[str, UUID]],
    question: str,
    search: Callable[[SearchRequest], SearchResponse],
    model: LanguageModel,
    config: QAConfig | None = None,
) -> tuple[ComparisonResponse, ComparisonTrace]:
    config = config or QAConfig()
    text = " ".join(question.split())
    if not text or len(text) > config.max_question_chars:
        raise ValueError("invalid_question")
    if len(bids) < 2 or len({bid_uuid for _, bid_uuid in bids}) != len(bids):
        raise ExtractionError("invalid_comparison", "Select at least two different bids")
    trace_id = str(uuid.uuid4())
    events: list[TraceEvent] = []
    tokens = TokenUsage()
    provider = str(getattr(model, "provider", "unknown"))
    model_name = str(getattr(model, "model", "unknown"))
    focus = focus_query(text)
    list_question = list_keyword(text) is not None
    sections: list[Section] = []
    for index, (label, bid_uuid) in enumerate(bids):
        started, stamp = clock(), time.perf_counter()
        probe, _ = retrieve(
            search,
            bid_uuid,
            focus,
            mode=RetrievalMode.dense,
            top_k=1,
            doc_types=[],
            addendum_numbers=[],
            evidence_prefix="probe",
        )
        events.append(
            _event(
                "retrieval",
                "bid_probe",
                started,
                stamp,
                field=label,
                query_hash=query_hash(focus),
                mode=RetrievalMode.dense.value,
                top_k=1,
                result_count=len(probe),
            )
        )
        if not probe:
            raise ExtractionError(
                "unknown_bid", f"Bid {label} has no indexed passages in the local collection"
            )
        bid_events: list[TraceEvent] = []
        hits = gather_evidence(focus, bid_uuid, search, config, bid_events)
        events.extend(event.model_copy(update={"field": label}) for event in bid_events)
        letter = chr(ord("A") + index)
        hits = [
            hit.model_copy(update={"evidence_id": f"{letter}{n}"})
            for n, hit in enumerate(hits, start=1)
        ]
        sections.append(
            Section(
                letter=letter,
                label=label,
                bid_uuid=bid_uuid,
                hits=hits,
                items=candidates(text, hits),
                revisions=question_revisions(focus, hits, bid_uuid),
            )
        )
    owners = {hit.evidence_id: s.label for s in sections for hit in s.hits}

    final: dict[str, tuple[Checked | None, QAValidation]] = {}
    best: dict[str, tuple[int, Checked]] = {}
    pending = [s for s in sections if s.hits]
    for section in sections:
        if not section.hits:
            final[section.label] = (
                None,
                QAValidation(
                    passed=True, attempts=0, reasons=["No evidence retrieved for this bid"]
                ),
            )
    request = comparison_request(text, sections, config)
    aspects_by_bid: dict[str, dict[str, str]] = {}
    attempts = 0
    last_reasons: dict[str, list[str]] = {}
    last_checks: dict[str, list[CitationCheck]] = {}
    while pending and attempts <= config.max_corrections:
        attempts += 1
        last = attempts > config.max_corrections
        started, stamp = clock(), time.perf_counter()
        completion: ModelResponse = model.complete(request)
        account(tokens, completion)
        provider, model_name = completion.provider, completion.model
        entries: dict[str, ModelBidReply] = {}
        error: str | None = None
        try:
            parsed = parse_comparison(completion.text)
            for entry in parsed.bids:
                entries.setdefault(entry.bid_id.strip().casefold(), entry)
        except ModelError as exc:
            error = f"{exc.code}: {exc.message}"
        retry: list[str] = []
        quotes: list[dict[str, str | int | None]] = []
        still: list[Section] = []
        for section in pending:
            aspects_map: dict[str, str] = {}
            reply: ModelReply | None = None
            if error:
                checked = Checked(reasons=[error])
            else:
                found = entries.get(section.label.casefold()) or entries.get(
                    section.letter.casefold()
                )
                checked, aspects_map, reply = check_bid(text, section, found, owners)
            aspects_by_bid.setdefault(section.label, {}).update(aspects_map)
            omitted = checked.completeness.omitted if checked.completeness else []
            reasons = (
                checked.reasons
                + [f"Item not addressed: {name}" for name in omitted]
                + [f"Addendum not addressed: {item}" for item in checked.unaddressed]
            )
            quotes.extend({**q, "bid": section.label} for q in checked.quotes)
            last_reasons[section.label] = reasons
            last_checks[section.label] = checked.checks
            if usable(reply, checked):
                best[section.label] = (attempts, checked)
            complete = not omitted and not checked.unaddressed
            if reply is not None and not checked.reasons and (complete or last):
                if reply.status == "not_found" and section.label in best:
                    final[section.label] = kept(best[section.label], attempts)
                elif reply.status == "not_found":
                    final[section.label] = (
                        None,
                        QAValidation(passed=True, attempts=attempts, reasons=["Model abstained"]),
                    )
                else:
                    final[section.label] = (
                        checked,
                        QAValidation(passed=True, attempts=attempts, checks=checked.checks),
                    )
                continue
            still.append(section)
            retry.extend(f"{section.label}: {reason}" for reason in reasons)
        events.append(
            _event(
                "qa",
                "compare" if attempts == 1 else "compare_correct",
                started,
                stamp,
                retry=attempts - 1,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                provider=completion.provider,
                model=completion.model,
                decision="malformed"
                if error
                else ", ".join(f"{e.bid_id}={e.status}" for e in entries.values()),
                reasons=retry,
                evidence=quotes,
                error=error,
            )
        )
        pending = still
        if pending and not last:
            request = comparison_request(text, sections, config, retry)
    for section in pending:
        if section.label in best:
            final[section.label] = kept(best[section.label], attempts)
        else:
            final[section.label] = (
                None,
                QAValidation(
                    passed=False,
                    attempts=attempts,
                    checks=last_checks.get(section.label, []),
                    reasons=[
                        "Abstained after failed citation validation",
                        *last_reasons.get(section.label, []),
                    ],
                ),
            )

    findings = [
        _finding(section, *final[section.label], list_question=list_question)
        for section in sections
    ]
    aspects = build_aspects(findings, aspects_by_bid)
    missing = [f.bid_id for f in findings if f.status == "not_found"]
    status: ComparisonStatus = (
        "not_found" if len(missing) == len(findings) else "partial" if missing else "answered"
    )
    limitations = [
        f"{label}: {NOT_FOUND}; no comparison is made for this bid." for label in missing
    ]
    if any(a.relation != "one_sided" for a in aspects):
        limitations.append(
            "Aspect relations compare the validated claim and quote text; they are lexical, not "
            "a judgement of legal equivalence."
        )
    validation = QAValidation(
        passed=True,
        attempts=attempts,
        checks=[check for f in findings for check in f.validation.checks],
        reasons=[f"{f.bid_id}: {reason}" for f in findings for reason in f.validation.reasons],
    )
    metadata = ModelMetadata(provider=provider, model=model_name)
    response = ComparisonResponse(
        question=text,
        bid_ids=[s.label for s in sections],
        status=status,
        answer=_readable(findings, aspects),
        findings=findings,
        aspects=aspects,
        missing_evidence=missing,
        limitations=limitations,
        validation=validation,
        trace_id=trace_id,
        model=metadata,
        evidence_count={s.label: len(s.hits) for s in sections},
    )
    trace = ComparisonTrace(
        trace_id=trace_id,
        bid_ids=[s.label for s in sections],
        bid_uuids=[s.bid_uuid for s in sections],
        question_hash=query_hash(text),
        question_chars=len(text),
        events=[event.model_copy(update={"sequence": i}) for i, event in enumerate(events)],
        status=status,
        validation=validation,
        token_usage=tokens,
        model=metadata,
    )
    return response, trace


def kept(best: tuple[int, Checked], attempts: int) -> tuple[Checked, QAValidation]:
    return best[1], kept_validation(best, attempts)
