"""Bounded extraction workflow.

The project does not depend on an agent framework. This orchestrator is the
coordinator: it plans field groups, calls the existing search service, asks a
specialist prompt per group, reconciles addenda, and retries failed fields at
most `max_retries` times after the first attempt. Search calls on one client
are serialized. Model calls for independent groups run concurrently.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from rfp.agents.confidence import Signals, Support, band, score
from rfp.agents.errors import ModelError
from rfp.agents.extractors import (
    CATALOG,
    critic_request,
    evidence_prefix,
    extraction_request,
    fields_in_group,
    parse_drafts,
    steps_for,
)
from rfp.agents.llm import LanguageModel, ModelResponse, account, parse_json_object
from rfp.agents.reconciliation import FIELD_TERMS, reconcile_field
from rfp.agents.retrieval import cited_pages, retrieve
from rfp.agents.schemas import (
    REQUIRED_FIELDS,
    AddendumChange,
    BidExtraction,
    DraftField,
    EvidenceHit,
    ExtractionConfig,
    FieldOutput,
    FieldValidation,
    OutputSource,
    TokenUsage,
    TraceEvent,
    ValidationCounts,
)
from rfp.agents.semantics import (
    grounded_notes,
    identifier_aliases,
    join_split_emails,
    normalize,
    only_incomplete,
)
from rfp.agents.state import ExtractionState
from rfp.agents.trace import Tracer, clock
from rfp.agents.validator import contained, support_kind, validate_field, value_tokens
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import SearchRequest, SearchResponse
from rfp.search.errors import SearchServiceError

ADDENDUM_CHECK_TOP_K = 5


def _borrow_shown(
    evidence: dict[str, list[EvidenceHit]], drafts: dict[str, DraftField], asked: list[str]
) -> None:
    """Let a field cite any passage shown in the same prompt.

    Evidence ids are unique per field, and the prompt shows every asked field's
    passages together, so the model may cite a passage listed under a sibling
    field. Ids that were never shown stay fabricated.
    """
    shown = {hit.evidence_id: hit for name in asked for hit in evidence[name][:8]}
    for name in asked:
        own = {hit.evidence_id for hit in evidence[name]}
        borrowed = [
            shown[evidence_id]
            for evidence_id in dict.fromkeys(drafts[name].evidence_ids)
            if evidence_id not in own and evidence_id in shown
        ]
        evidence[name] = evidence[name] + borrowed


def _repair_citations(
    evidence: dict[str, list[EvidenceHit]], drafts: dict[str, DraftField], asked: list[str]
) -> list[str]:
    """Point a verbatim value at the shown passage that contains it.

    When none of the cited passages contains the value but a passage shown in the same
    prompt does, the model copied the value and mislabelled its source. The first such
    passage in prompt order becomes the citation; the validator still checks it.
    """
    shown = [hit for name in asked for hit in evidence[name][:8]]
    repaired: list[str] = []
    for name in asked:
        draft = drafts[name]
        # Short values such as a bare brand name occur in too many passages to relocate.
        if not draft.value or len(draft.value.strip()) < 6:
            continue
        by_id = {hit.evidence_id: hit for hit in evidence[name]}
        cited = [by_id[item] for item in draft.evidence_ids if item in by_id]
        if any(contained(draft.value, hit.text) for hit in cited):
            continue
        source = next((hit for hit in shown if contained(draft.value, hit.text)), None)
        if source is None:
            added = _complete_citations(draft.value, cited, shown) if cited else []
            if not added:
                continue
            evidence[name] = evidence[name] + [hit for hit in added if hit.evidence_id not in by_id]
            ids = [*draft.evidence_ids, *(hit.evidence_id for hit in added)]
            drafts[name] = draft.model_copy(update={"evidence_ids": ids})
            repaired.append(name)
            continue
        if source.evidence_id not in by_id:
            evidence[name] = evidence[name] + [source]
        drafts[name] = draft.model_copy(update={"evidence_ids": [source.evidence_id]})
        repaired.append(name)
    return repaired


def _complete_citations(
    value: str, cited: list[EvidenceHit], shown: list[EvidenceHit]
) -> list[EvidenceHit]:
    """Shown passages that, added to the cited ones, contain every token of a list value.

    A value assembled from several passages is often cited with only some of them. The
    fewest extra passages are chosen greedily; if the value's tokens cannot all be
    covered, nothing is added and the validator rejects the value as before.
    """
    haystack = normalize("\n".join(join_split_emails(hit.text) for hit in cited))
    missing = {token for token in value_tokens(value) if token not in haystack}
    if not missing:
        return []
    cited_ids = {hit.evidence_id for hit in cited}
    pool = [hit for hit in shown if hit.evidence_id not in cited_ids]
    added: list[EvidenceHit] = []
    while missing:
        texts = {hit.evidence_id: normalize(join_split_emails(hit.text)) for hit in pool}
        best = max(
            pool, key=lambda hit: sum(t in texts[hit.evidence_id] for t in missing), default=None
        )
        if best is None:
            return []
        covered = {token for token in missing if token in texts[best.evidence_id]}
        if not covered:
            return []
        added.append(best)
        missing -= covered
        pool = [hit for hit in pool if hit.evidence_id != best.evidence_id]
    return added


def _dedupe(hits: list[EvidenceHit], prefix: str) -> list[EvidenceHit]:
    seen: set[tuple[str, int | None, str | None, str]] = set()
    chosen: list[EvidenceHit] = []
    for hit in hits:
        source = hit.sources[0]
        key = (source.relative_path, source.page, source.html_element_id, hit.text)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(hit)
    return [
        hit.model_copy(update={"evidence_id": f"{prefix}-{index}"})
        for index, hit in enumerate(chosen, start=1)
    ]


def _interleave(per_step: list[list[EvidenceHit]]) -> list[EvidenceHit]:
    """Round-robin by rank so every query step reaches the model's first evidence slots."""
    merged: list[EvidenceHit] = []
    for rank in range(max((len(hits) for hits in per_step), default=0)):
        merged.extend(hits[rank] for hits in per_step if rank < len(hits))
    return merged


def _evidence_refs(hits: list[EvidenceHit]) -> list[dict[str, str | int | None]]:
    refs: list[dict[str, str | int | None]] = []
    for hit in hits[:8]:
        source = hit.sources[0]
        refs.append(
            {
                "evidence_id": hit.evidence_id,
                "file": source.file,
                "page": source.page,
                "html_element_id": source.html_element_id,
            }
        )
    return refs


@dataclass
class AttemptResult:
    evidence: dict[str, list[EvidenceHit]]
    drafts: dict[str, DraftField]
    validations: dict[str, FieldValidation]
    changes: list[AddendumChange]
    events: list[TraceEvent]
    tokens: TokenUsage
    search_ok: int = 0
    search_failed: int = 0
    provider: str = ""
    model_name: str = ""


@dataclass
class WorkflowResult:
    output: BidExtraction
    trace_events: list[TraceEvent]
    state: ExtractionState
    outcome: str
    tokens: TokenUsage = field(default_factory=TokenUsage)


def _event(agent: str, task: str, started: str, started_at: float, **payload: object) -> TraceEvent:
    return TraceEvent.model_validate(
        {
            "sequence": 0,
            "agent": agent,
            "task": task,
            "started_at": started,
            "ended_at": clock(),
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            **payload,
        }
    )


def _reject_copied_identifiers(state: ExtractionState) -> None:
    """A product identifier that repeats the accepted solicitation number is a mismatch."""
    bid = state.fields["Bid Number"]
    if bid.status != "accepted" or bid.draft is None or not bid.draft.value:
        return
    for name in ("Model_no", "Part_no"):
        slot = state.fields[name]
        value = slot.draft.value if slot.draft else None
        if slot.status != "accepted" or not value or not contained(bid.draft.value, value):
            continue
        slot.status = "rejected"
        slot.validation = FieldValidation(
            name=name,
            passed=False,
            status="rejected",
            reasons=[f"{name} repeats the solicitation number, not a product identifier"],
            support="conflict",
        )


SUMMARY_FACT_SKIP = frozenset({"Bid Summary", "contact_info"})


def _summary_facts(state: ExtractionState) -> list[tuple[str, str, list[EvidenceHit]]]:
    """Accepted field values with the cited passages that validated each one."""
    facts: list[tuple[str, str, list[EvidenceHit]]] = []
    for name in REQUIRED_FIELDS:
        slot = state.fields[name]
        if name in SUMMARY_FACT_SKIP or slot.status != "accepted" or slot.draft is None:
            continue
        value = slot.draft.value
        cited = [hit for hit in slot.evidence if hit.evidence_id in slot.draft.evidence_ids]
        if value and cited:
            facts.append((name, value[:300], cited))
    return facts


def _expand_facts(drafts: dict[str, DraftField], groups: dict[str, list[str]]) -> None:
    """A cited validated value stands for every passage that validated it."""
    draft = drafts.get("Bid Summary")
    if draft is None or not groups:
        return
    ids = [item for cited in draft.evidence_ids for item in groups.get(cited, [cited])]
    drafts["Bid Summary"] = draft.model_copy(update={"evidence_ids": list(dict.fromkeys(ids))})


def _signals(state: ExtractionState, name: str, status: str) -> Signals:
    slot = state.fields[name]
    conflict = any(item.conflict for item in state.addendum_changes if item.field == name)
    if status != "accepted" or slot.draft is None or not slot.draft.value:
        return Signals(status=status, retries=slot.retry_count, conflict=conflict)
    value = slot.draft.value
    cited = [hit for hit in slot.evidence if hit.evidence_id in slot.draft.evidence_ids]
    agreeing = {
        (hit.sources[0].file, hit.sources[0].page, hit.sources[0].html_element_id)
        for hit in cited
        if hit.sources and support_kind(name, value, [hit.text]) != "none"
    }
    return Signals(
        status=status,
        support=cast(Support, support_kind(name, value, [hit.text for hit in cited])),
        agreeing_sources=len(agreeing),
        retries=slot.retry_count,
        incomplete=bool(slot.incomplete),
        conflict=conflict,
    )


def _run_units(
    units: list[list[str]],
    worker: Callable[[list[str]], AttemptResult],
    concurrency: int,
) -> list[AttemptResult]:
    if len(units) <= 1 or concurrency == 1:
        return [worker(unit) for unit in units]
    with ThreadPoolExecutor(max_workers=min(concurrency, len(units))) as pool:
        futures = [pool.submit(worker, unit) for unit in units]
        return [future.result() for future in futures]


class Orchestrator:
    def __init__(
        self,
        search: Callable[[SearchRequest], SearchResponse],
        model: LanguageModel,
        config: ExtractionConfig | None = None,
    ):
        self.search = search
        self.model = model
        self.config = config or ExtractionConfig()
        self._search_lock = threading.Lock()

    def run(
        self,
        bid_label: str,
        bid_uuid: UUID,
        *,
        workflow_id: UUID | None = None,
        documents: list[str] | None = None,
    ) -> tuple[BidExtraction, ExtractionState, list[TraceEvent]]:
        state = ExtractionState.create(bid_label, bid_uuid, workflow_id)
        if documents:
            state.documents = list(documents)
        state.provider = str(getattr(self.model, "provider", "fake"))
        state.model_name = str(getattr(self.model, "model", "fake"))
        tracer = Tracer()
        groups = [
            fields_in_group(name) for name in ("identity", "logistics", "commercial", "product")
        ]
        self._round(state, tracer, [[spec.name for spec in group] for group in groups], attempt=0)
        _reject_copied_identifiers(state)
        for attempt in range(1, self.config.max_retries + 1):
            pending = [name for name in state.pending() if CATALOG[name].group != "summary"]
            if not pending:
                break
            self._round(state, tracer, [[name] for name in pending], attempt=attempt)
            _reject_copied_identifiers(state)
        self._finalize(state, skip_summary=True)
        facts = _summary_facts(state)
        self._round(state, tracer, [["Bid Summary"]], attempt=0, facts=facts)
        for attempt in range(1, self.config.max_retries + 1):
            if state.fields["Bid Summary"].status != "rejected":
                break
            self._round(state, tracer, [["Bid Summary"]], attempt=attempt, facts=facts)
        self._finalize(state, skip_summary=False)
        if state.search_ok == 0 and state.search_failed > 0:
            state.status = "failed"
            state.error = "retrieval_unavailable"
        else:
            state.status = "completed"
        output, signals = self._output(state)
        for name, item in signals.items():
            value = output.fields[name].confidence
            tracer.extend(
                [
                    _event(
                        "confidence",
                        "score",
                        clock(),
                        time.perf_counter(),
                        field=name,
                        decision=f"{value} ({band(value)})",
                        reasons=item.reasons(),
                    )
                ]
            )
        return output, state, tracer._events

    def _round(
        self,
        state: ExtractionState,
        tracer: Tracer,
        units: list[list[str]],
        attempt: int,
        facts: list[tuple[str, str, list[EvidenceHit]]] | None = None,
    ) -> None:
        reasons: dict[str, list[str]] = {}
        rejected: dict[str, str] = {}
        for unit in units:
            for name in unit:
                slot = state.fields[name]
                validation = slot.validation
                reasons[name] = list(validation.reasons) if validation is not None else []
                if slot.status == "rejected" and slot.draft is not None and slot.draft.value:
                    rejected[name] = slot.draft.value

        def worker(names: list[str]) -> AttemptResult:
            return self._attempt(
                state.bid_uuid,
                names,
                attempt,
                {name: reasons[name] for name in names},
                {name: rejected[name] for name in names if name in rejected},
                facts,
            )

        for result in _run_units(units, worker, self.config.concurrency):
            self._merge(state, result, attempt)
            tracer.extend(result.events)

    def _attempt(
        self,
        bid_id: UUID,
        names: list[str],
        attempt: int,
        reasons: dict[str, list[str]],
        rejected: dict[str, str] | None = None,
        facts: list[tuple[str, str, list[EvidenceHit]]] | None = None,
    ) -> AttemptResult:
        events: list[TraceEvent] = []
        evidence: dict[str, list[EvidenceHit]] = {}
        errors: dict[str, list[str]] = {name: [] for name in names}
        result = AttemptResult(
            evidence={},
            drafts={},
            validations={},
            changes=[],
            events=events,
            tokens=TokenUsage(),
        )
        for name in names:
            spec = CATALOG[name]
            collected: list[list[EvidenceHit]] = []
            for step in steps_for(spec, attempt, reasons.get(name, [])):
                started, stamp = clock(), time.perf_counter()
                try:
                    hits, response = retrieve(
                        self.search,
                        bid_id,
                        step.query,
                        mode=step.mode,
                        top_k=self.config.hard_top_k if step.hard else self.config.top_k,
                        doc_types=list(step.doc_types),
                        addendum_numbers=list(step.addendum_numbers),
                        evidence_prefix=evidence_prefix(name),
                        lock=self._search_lock,
                    )
                except SearchServiceError as exc:
                    result.search_failed += 1
                    errors[name].append(exc.code)
                    events.append(
                        _event(
                            "retrieval",
                            "search",
                            started,
                            stamp,
                            field=name,
                            query=step.query[:500],
                            mode=step.mode.value,
                            top_k=self.config.hard_top_k if step.hard else self.config.top_k,
                            doc_types=[item.value for item in step.doc_types],
                            result_count=0,
                            retry=attempt,
                            error=exc.code,
                        )
                    )
                    continue
                result.search_ok += 1
                collected.append(hits)
                events.append(
                    _event(
                        "retrieval",
                        "search",
                        started,
                        stamp,
                        field=name,
                        query=step.query[:500],
                        mode=response.mode.value,
                        top_k=response.top_k,
                        doc_types=[item.value for item in step.doc_types],
                        result_count=len(hits),
                        evidence=_evidence_refs(hits),
                        retry=attempt,
                        decision=", ".join(response.warnings) or None,
                    )
                )
            evidence[name] = _dedupe(_interleave(collected), evidence_prefix(name))
        fact_lines: list[tuple[str, str, str]] = []
        fact_groups: dict[str, list[str]] = {}
        if facts and "Bid Summary" in evidence:
            prefix = evidence_prefix("Bid Summary")
            for index, (field_name, value, hits) in enumerate(facts, start=1):
                ids = [
                    f"{prefix}-fact-{index}" + (f"-{part}" if part else "")
                    for part in range(len(hits))
                ]
                evidence["Bid Summary"].extend(
                    hit.model_copy(update={"evidence_id": item})
                    for hit, item in zip(hits, ids, strict=True)
                )
                fact_groups[ids[0]] = ids
                fact_lines.append((field_name, value, ids[0]))
        result.evidence = evidence
        asked = [name for name in names if evidence[name]]
        drafts: dict[str, DraftField] = {}
        for name in names:
            if evidence[name]:
                continue
            if errors[name]:
                drafts[name] = DraftField(name=name, value=None, notes="")
            else:
                drafts[name] = DraftField(
                    name=name, value=None, notes="Not found in documents", status="not_found"
                )
        model_error: str | None = None
        repaired: list[str] = []
        if asked:
            group = CATALOG[asked[0]].group
            request = extraction_request(
                group, evidence, asked, attempt, reasons, self.config, rejected, fact_lines
            )
            started, stamp = clock(), time.perf_counter()
            completion: ModelResponse | None = None
            try:
                completion = self.model.complete(request)
                account(result.tokens, completion)
                result.provider = completion.provider
                result.model_name = completion.model
                drafts.update(parse_drafts(asked, completion.text))
                _expand_facts(drafts, fact_groups)
                _borrow_shown(evidence, drafts, asked)
                repaired = _repair_citations(evidence, drafts, asked)
            except (ModelError, ValueError) as exc:
                model_error = exc.code if isinstance(exc, ModelError) else "llm_malformed"
                for name in asked:
                    drafts[name] = DraftField(name=name, value=None, notes="")
            events.append(
                _event(
                    group,
                    "extract",
                    started,
                    stamp,
                    field=",".join(asked),
                    retry=attempt,
                    input_tokens=None if completion is None else completion.input_tokens,
                    output_tokens=None if completion is None else completion.output_tokens,
                    provider=None if completion is None else completion.provider,
                    model=None if completion is None else completion.model,
                    error=model_error,
                    decision="model omitted or failed"
                    if model_error
                    else "drafts returned"
                    + (f"; citation repaired for {', '.join(repaired)}" if repaired else ""),
                )
            )
        result.drafts = drafts
        for name in names:
            if model_error and name in asked:
                validation = FieldValidation(
                    name=name,
                    passed=False,
                    status="rejected",
                    reasons=[model_error],
                    support="unsupported",
                )
            elif drafts[name].status == "not_found" and not evidence[name]:
                validation = FieldValidation(
                    name=name, passed=True, status="not_found", support="null_ok"
                )
            else:
                validation = validate_field(
                    drafts[name],
                    evidence[name],
                    search_errors=errors[name] if not evidence[name] else [],
                )
            if validation.status == "accepted":
                validation = self._critic(
                    name, drafts[name], evidence[name], validation, result, events, attempt
                )
            if validation.status == "accepted":
                extra = self._addendum_evidence(bid_id, name, attempt, result, events)
                decision = reconcile_field(name, drafts[name], evidence[name] + extra, bid_id)
                result.changes.extend(decision.all_changes)
                if decision.reject_reason:
                    validation = FieldValidation(
                        name=name,
                        passed=False,
                        status="rejected",
                        reasons=[decision.reject_reason],
                        support="conflict",
                    )
            result.validations[name] = validation
            excerpt = None
            rejected_value = drafts[name].value
            if not validation.passed and rejected_value:
                excerpt = "rejected value: " + rejected_value[:240]
            elif not validation.passed and evidence[name]:
                excerpt = " ".join(evidence[name][0].text.split())[:240]
            events.append(
                _event(
                    "validator",
                    "check",
                    clock(),
                    time.perf_counter(),
                    field=name,
                    retry=attempt,
                    reasons=validation.reasons,
                    decision=excerpt or validation.status,
                    evidence=_evidence_refs(
                        [
                            hit
                            for hit in evidence[name]
                            if hit.evidence_id in drafts[name].evidence_ids
                        ]
                    ),
                )
            )
        return result

    def _addendum_evidence(
        self,
        bid_id: UUID,
        name: str,
        attempt: int,
        result: AttemptResult,
        events: list[TraceEvent],
    ) -> list[EvidenceHit]:
        """Addendum passages on the field's subject, used only to check for revisions.

        They are not shown to the extraction model; a replacement they reveal rejects the value,
        and the retry then retrieves addenda for the model.
        """
        terms = FIELD_TERMS.get(name)
        if not terms:
            return []
        query = " ".join([name, *terms])
        started, stamp = clock(), time.perf_counter()
        try:
            hits, response = retrieve(
                self.search,
                bid_id,
                query,
                mode=self.config.mode,
                top_k=ADDENDUM_CHECK_TOP_K,
                doc_types=[DocumentType.addendum],
                addendum_numbers=[],
                evidence_prefix=f"{evidence_prefix(name)}-addendum-",
                lock=self._search_lock,
            )
        except SearchServiceError as exc:
            result.search_failed += 1
            events.append(
                _event(
                    "reconciliation",
                    "search",
                    started,
                    stamp,
                    field=name,
                    query=query[:500],
                    mode=self.config.mode.value,
                    top_k=ADDENDUM_CHECK_TOP_K,
                    doc_types=[DocumentType.addendum.value],
                    result_count=0,
                    retry=attempt,
                    error=exc.code,
                )
            )
            return []
        result.search_ok += 1
        events.append(
            _event(
                "reconciliation",
                "search",
                started,
                stamp,
                field=name,
                query=query[:500],
                mode=response.mode.value,
                top_k=response.top_k,
                doc_types=[DocumentType.addendum.value],
                result_count=len(hits),
                evidence=_evidence_refs(hits),
                retry=attempt,
            )
        )
        return hits

    def _critic(
        self,
        name: str,
        draft: DraftField,
        evidence: list[EvidenceHit],
        validation: FieldValidation,
        result: AttemptResult,
        events: list[TraceEvent],
        attempt: int,
    ) -> FieldValidation:
        if not self.config.critic_enabled or draft.value is None:
            return validation
        request = critic_request(name, draft, evidence, self.config)
        started, stamp = clock(), time.perf_counter()
        try:
            response = self.model.complete(request)
        except ModelError as exc:
            events.append(
                _event(
                    "critic",
                    "review",
                    started,
                    stamp,
                    field=name,
                    retry=attempt,
                    error=exc.code,
                    decision="deterministic validation kept",
                )
            )
            return validation
        account(result.tokens, response)
        result.provider = response.provider
        result.model_name = response.model
        rejected = False
        reason = "evidence does not support the value"
        try:
            body = parse_json_object(response.text)
        except ModelError:
            body = {}
        if body.get("supported") is False:
            rejected = True
            if isinstance(body.get("reason"), str):
                reason = body["reason"][:300]
        events.append(
            _event(
                "critic",
                "review",
                started,
                stamp,
                field=name,
                retry=attempt,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                provider=response.provider,
                model=response.model,
                decision="rejected by critic" if rejected else "critic did not override",
                reasons=[reason] if rejected else [],
            )
        )
        if not rejected:
            return validation
        return FieldValidation(
            name=name,
            passed=False,
            status="rejected",
            reasons=[f"Critic rejected the value: {reason}"],
            support="unsupported",
        )

    def _merge(self, state: ExtractionState, result: AttemptResult, attempt: int) -> None:
        state.search_ok += result.search_ok
        state.search_failed += result.search_failed
        state.tokens.calls += result.tokens.calls
        state.tokens.input_tokens += result.tokens.input_tokens
        state.tokens.output_tokens += result.tokens.output_tokens
        if result.provider:
            state.provider = result.provider
            state.model_name = result.model_name
        for name, validation in result.validations.items():
            slot = state.fields[name]
            slot.evidence = result.evidence.get(name, [])
            slot.draft = result.drafts[name]
            slot.validation = validation
            slot.status = validation.status
            if validation.status == "rejected" and only_incomplete(validation.reasons):
                slot.fallback = (slot.draft, slot.evidence, list(validation.reasons))
            if attempt > 0:
                slot.retry_count = attempt
            state.remember_documents(slot.evidence)
        renewed = {change.field for change in result.changes}
        kept = [item for item in state.addendum_changes if item.field not in renewed]
        for change in result.changes:
            if change not in kept:
                kept.append(change)
            if change.conflict and change.explanation not in state.conflicts:
                state.conflicts.append(change.explanation)
        state.addendum_changes = sorted(kept, key=_change_order)

    def _finalize(self, state: ExtractionState, skip_summary: bool) -> None:
        for name in REQUIRED_FIELDS:
            if skip_summary and name == "Bid Summary":
                continue
            slot = state.fields[name]
            if slot.status in {"rejected", "not_found"} and slot.fallback is not None:
                slot.draft, slot.evidence, slot.incomplete = slot.fallback
                slot.status = "accepted"
                slot.validation = FieldValidation(
                    name=name, passed=True, status="accepted", support="contained"
                )
                continue
            if slot.status != "rejected":
                continue
            reasons = slot.validation.reasons if slot.validation else ["Validation failed"]
            slot.status = "unresolved"
            slot.draft = DraftField(
                name=name,
                value=None,
                notes="Validation failed: " + "; ".join(reasons),
                status="unresolved",
            )
            slot.validation = FieldValidation(
                name=name,
                passed=False,
                status="unresolved",
                reasons=reasons,
                support="unsupported",
            )

    def _output(self, state: ExtractionState) -> tuple[BidExtraction, dict[str, Signals]]:
        fields: dict[str, FieldOutput] = {}
        signals: dict[str, Signals] = {}
        passed = not_found = unresolved = 0
        for name in REQUIRED_FIELDS:
            slot = state.fields[name]
            status = slot.status
            draft = slot.draft or DraftField(name=name, value=None, notes="Not found in documents")
            if status == "accepted" and draft.value is not None:
                try:
                    sources = _sources(draft, slot.evidence)
                except ValueError:
                    unresolved += 1
                    signals[name] = _signals(state, name, "unresolved")
                    fields[name] = FieldOutput(
                        value=None,
                        sources=[],
                        confidence=score(signals[name]),
                        notes="Validation failed: citation could not be rendered",
                    )
                    continue
                passed += 1
                cited = [hit.text for hit in slot.evidence if hit.evidence_id in draft.evidence_ids]
                changes = [item for item in state.addendum_changes if item.field == name]
                notes = grounded_notes(draft.notes, cited, _change_notes(changes))
                if name == "Bid Number":
                    notes = " ".join(filter(None, [notes, _alias_note(draft.value, slot.evidence)]))
                if slot.incomplete:
                    warning = "Possibly incomplete: " + "; ".join(slot.incomplete) + "."
                    notes = " ".join(filter(None, [warning, _change_notes(changes)]))
                signals[name] = _signals(state, name, status)
                fields[name] = FieldOutput(
                    value=draft.value,
                    sources=sources,
                    confidence=score(signals[name]),
                    notes=notes or "Supported by the cited passage.",
                )
            elif status == "not_found":
                not_found += 1
                signals[name] = _signals(state, name, status)
                fields[name] = FieldOutput(
                    value=None,
                    sources=[],
                    confidence=score(signals[name]),
                    notes=draft.notes.strip() or "Not found in documents",
                )
            else:
                unresolved += 1
                signals[name] = _signals(state, name, "unresolved")
                fields[name] = FieldOutput(
                    value=None,
                    sources=[],
                    confidence=score(signals[name]),
                    notes=draft.notes.strip() or "Validation failed",
                )
        output = BidExtraction(
            bid_id=state.bid_label,
            fields=fields,
            addendum_changes=state.addendum_changes,
            validation=ValidationCounts(
                passed=passed,
                failed=unresolved,
                unresolved=unresolved,
                not_found=not_found,
            ),
        )
        return output, signals


def _change_order(change: AddendumChange) -> tuple[int, bool, int, str]:
    position = REQUIRED_FIELDS.index(change.field) if change.field in REQUIRED_FIELDS else 99
    number = change.addendum_number
    return position, number is None, number or 0, change.quote or change.revised_value or ""


def _change_note(change: AddendumChange | None) -> str | None:
    if change is None or change.conflict or change.status == "superseded":
        return None
    addendum = f"Addendum {change.addendum_number}" if change.addendum_number else "An addendum"
    if change.change_type == "remove" and change.quote:
        return f'{addendum} removes: "{change.quote}".'
    if change.change_type in {"add", "clarify"} and change.quote:
        verb = "adds" if change.change_type == "add" else "clarifies"
        return f'{addendum} {verb}: "{change.quote}".'
    if not change.revised_value:
        return None
    if change.original_value:
        return (
            f"{addendum} states {change.revised_value}; the base document stated "
            f"{change.original_value}."
        )
    return f"{addendum} states {change.revised_value}."


def _change_notes(changes: list[AddendumChange]) -> str | None:
    notes = [note for note in (_change_note(change) for change in changes) if note]
    return " ".join(notes) or None


def _alias_note(value: str, evidence: list[EvidenceHit]) -> str | None:
    """Other labelled identifiers for the same solicitation, each with its locator."""
    hits = [hit for hit in evidence if hit.sources]
    parts = []
    for index, matched in identifier_aliases(value, [hit.text for hit in hits]):
        source = hits[index].sources[0]
        pages = cited_pages(source, hits[index].text, matched.split()[-1].lstrip("#"))
        if source.page is not None:
            where = f"{source.file}, page {pages[0] if pages else source.page}"
        else:
            where = f"{source.file}, #{source.html_element_id}"
        parts.append(f"{matched} ({where})")
    if not parts:
        return None
    return "The documents also identify this solicitation as " + "; ".join(parts) + "."


def _sources(draft: DraftField, evidence: list[EvidenceHit]) -> list[OutputSource]:
    by_id = {hit.evidence_id: hit for hit in evidence}
    sources: list[OutputSource] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for evidence_id in draft.evidence_ids:
        hit = by_id.get(evidence_id)
        if hit is None:
            continue
        for source in hit.sources:
            pages: list[int | None] = [*cited_pages(source, hit.text, draft.value)] or [None]
            for page in pages:
                key = (source.file, page, source.html_element_id)
                if key in seen:
                    continue
                seen.add(key)
                sources.append(source.output(page))
    if not sources:
        raise ValueError(f"{draft.name} was accepted without a usable citation")
    return sources


def run_extraction(
    bid_label: str,
    bid_uuid: UUID,
    search: Callable[[SearchRequest], SearchResponse],
    model: LanguageModel,
    config: ExtractionConfig | None = None,
    workflow_id: UUID | None = None,
    documents: list[str] | None = None,
) -> tuple[BidExtraction, ExtractionState, list[TraceEvent]]:
    return Orchestrator(search, model, config).run(
        bid_label, bid_uuid, workflow_id=workflow_id, documents=documents
    )
