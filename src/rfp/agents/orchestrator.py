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
from uuid import UUID

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
from rfp.agents.reconciliation import reconcile_field
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
from rfp.agents.semantics import grounded_notes
from rfp.agents.state import ExtractionState
from rfp.agents.trace import Tracer, clock
from rfp.agents.validator import contained, validate_field
from rfp.schemas.search import SearchRequest, SearchResponse
from rfp.search.errors import SearchServiceError


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


def _confidence(status: str) -> float:
    """Fixed value per validation status, required by the output schema; not a probability."""
    if status == "accepted":
        return 0.86
    if status == "not_found":
        return 0.74
    return 0.35


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
        self._round(state, tracer, [["Bid Summary"]], attempt=0)
        for attempt in range(1, self.config.max_retries + 1):
            if state.fields["Bid Summary"].status != "rejected":
                break
            self._round(state, tracer, [["Bid Summary"]], attempt=attempt)
        self._finalize(state, skip_summary=False)
        if state.search_ok == 0 and state.search_failed > 0:
            state.status = "failed"
            state.error = "retrieval_unavailable"
        else:
            state.status = "completed"
        output = self._output(state)
        return output, state, tracer._events

    def _round(
        self,
        state: ExtractionState,
        tracer: Tracer,
        units: list[list[str]],
        attempt: int,
    ) -> None:
        reasons: dict[str, list[str]] = {}
        for unit in units:
            for name in unit:
                validation = state.fields[name].validation
                reasons[name] = list(validation.reasons) if validation is not None else []

        def worker(names: list[str]) -> AttemptResult:
            return self._attempt(
                state.bid_uuid, names, attempt, {name: reasons[name] for name in names}
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
        if asked:
            group = CATALOG[asked[0]].group
            request = extraction_request(group, evidence, asked, attempt, reasons, self.config)
            started, stamp = clock(), time.perf_counter()
            completion: ModelResponse | None = None
            try:
                completion = self.model.complete(request)
                account(result.tokens, completion)
                result.provider = completion.provider
                result.model_name = completion.model
                drafts.update(parse_drafts(asked, completion.text))
                _borrow_shown(evidence, drafts, asked)
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
                    decision="model omitted or failed" if model_error else "drafts returned",
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
                decision = reconcile_field(name, drafts[name], evidence[name])
                if decision.change is not None:
                    result.changes.append(decision.change)
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
            if not validation.passed and evidence[name]:
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
            if attempt > 0:
                slot.retry_count = attempt
            state.remember_documents(slot.evidence)
        for change in result.changes:
            state.addendum_changes = [
                item for item in state.addendum_changes if item.field != change.field
            ]
            state.addendum_changes.append(change)
            if change.conflict and change.explanation not in state.conflicts:
                state.conflicts.append(change.explanation)

    def _finalize(self, state: ExtractionState, skip_summary: bool) -> None:
        for name in REQUIRED_FIELDS:
            if skip_summary and name == "Bid Summary":
                continue
            slot = state.fields[name]
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

    def _output(self, state: ExtractionState) -> BidExtraction:
        fields: dict[str, FieldOutput] = {}
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
                    fields[name] = FieldOutput(
                        value=None,
                        sources=[],
                        confidence=_confidence("unresolved"),
                        notes="Validation failed: citation could not be rendered",
                    )
                    continue
                passed += 1
                cited = [hit.text for hit in slot.evidence if hit.evidence_id in draft.evidence_ids]
                change = next((item for item in state.addendum_changes if item.field == name), None)
                notes = grounded_notes(draft.notes, cited, _change_note(change))
                fields[name] = FieldOutput(
                    value=draft.value,
                    sources=sources,
                    confidence=_confidence(status),
                    notes=notes or "Supported by the cited passage.",
                )
            elif status == "not_found":
                not_found += 1
                fields[name] = FieldOutput(
                    value=None,
                    sources=[],
                    confidence=_confidence(status),
                    notes=draft.notes.strip() or "Not found in documents",
                )
            else:
                unresolved += 1
                fields[name] = FieldOutput(
                    value=None,
                    sources=[],
                    confidence=_confidence("unresolved"),
                    notes=draft.notes.strip() or "Validation failed",
                )
        return BidExtraction(
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


def _change_note(change: AddendumChange | None) -> str | None:
    if change is None or change.conflict or not change.revised_value:
        return None
    addendum = f"Addendum {change.addendum_number}" if change.addendum_number else "An addendum"
    if change.original_value:
        return (
            f"{addendum} states {change.revised_value}; the base document stated "
            f"{change.original_value}."
        )
    return f"{addendum} states {change.revised_value}."


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
