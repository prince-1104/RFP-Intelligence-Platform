"""Citation-grounded question answering over one bid.

Retrieval goes through `SearchService.search` with a single-bid filter. The model sees only the
question and the retrieved passages. Its reply is accepted only after every citation is matched
to a retrieved passage, every quote is found in that passage, and every date, number and
identifier in the answer appears in the cited text. Otherwise one bounded correction is
requested, and if that also fails the answer is an abstention.
"""

import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from rfp.agents.errors import ModelError
from rfp.agents.llm import LanguageModel, ModelRequest, ModelResponse, account, parse_json_object
from rfp.agents.reconciliation import reconcile_due_date
from rfp.agents.retrieval import query_hash, retrieve
from rfp.agents.schemas import DraftField, EvidenceHit, ModelMetadata, TokenUsage, TraceEvent
from rfp.agents.trace import clock
from rfp.agents.validator import contained, date_phrases, locator_problem
from rfp.schemas.parsed import Contract, DocumentType
from rfp.schemas.search import RetrievalMode, SearchRequest, SearchResponse

NOT_FOUND = "Not found in documents"
QAStatus = Literal["answered", "not_found", "conflict"]
DEADLINE_TERMS = re.compile(
    r"\b(deadline|due|submission|submit|closing|extend|extended|extension)\b", re.IGNORECASE
)
SUBMISSION_TERMS = re.compile(
    r"\b(proposal|proposals|bid|bids|response|responses|submission|offer|rfp|solicitation)\b",
    re.IGNORECASE,
)
ADDENDUM_NUMBER = re.compile(r"\baddend(?:um|a)\s*(?:no\.?\s*|#\s*)?(\d+)\b", re.IGNORECASE)
FACT = re.compile(r"\b[A-Z0-9]*\d[A-Z0-9]*(?:[-/.:,][A-Z0-9]+)*\b", re.IGNORECASE)


class QAConfig(Contract):
    top_k: int = Field(default=8, ge=1, le=20)
    addendum_top_k: int = Field(default=5, ge=1, le=20)
    mode: RetrievalMode = RetrievalMode.hybrid_rerank
    max_evidence: int = Field(default=10, ge=1, le=20)
    max_corrections: int = Field(default=1, ge=0, le=2)
    excerpt_chars: int = Field(default=1200, ge=100, le=4000)
    max_question_chars: int = Field(default=1000, ge=10, le=4000)


class QACitation(Contract):
    evidence_id: str
    document: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    html_element_id: str | None = None
    document_type: DocumentType
    addendum_number: int | None = None
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def locator(self) -> "QACitation":
        if (self.page is not None) == bool(self.html_element_id):
            raise ValueError("A citation needs a PDF page or an HTML locator, not both")
        return self


class CitationCheck(Contract):
    evidence_id: str
    valid: bool
    reason: str | None = None


class QAValidation(Contract):
    passed: bool
    attempts: int = Field(ge=0)
    checks: list[CitationCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class QAResponse(Contract):
    bid_id: str
    question: str
    answer: str = Field(min_length=1)
    status: QAStatus
    citations: list[QACitation]
    # No calibrated method exists for answers, so no numeric score is reported.
    confidence: float | None = None
    validation: QAValidation
    trace_id: str
    model: ModelMetadata
    evidence_count: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> "QAResponse":
        if self.status == "not_found":
            if self.answer != NOT_FOUND or self.citations:
                raise ValueError("not_found answers must be the abstention text with no citations")
        elif not self.citations:
            raise ValueError("answered and conflict responses require citations")
        if self.status == "conflict" and len(_distinct(self.citations)) < 2:
            raise ValueError("conflict responses must cite at least two different sources")
        if self.status != "not_found" and not self.validation.passed:
            raise ValueError("only validated answers may be returned")
        return self


class QATrace(Contract):
    trace_id: str
    bid_id: str
    bid_uuid: UUID
    question_hash: str
    question_chars: int
    events: list[TraceEvent]
    status: QAStatus
    validation: QAValidation
    token_usage: TokenUsage
    model: ModelMetadata


class ModelCitation(Contract):
    evidence_id: str
    quote: str


class ModelReply(Contract):
    status: QAStatus
    answer: str
    citations: list[ModelCitation] = Field(default_factory=list)


def _distinct(citations: list[QACitation]) -> set[tuple[str, int | None, str | None]]:
    return {(c.document, c.page, c.html_element_id) for c in citations}


QueryPlan = tuple[str, RetrievalMode, list[DocumentType], list[int], int]


def plan_queries(question: str, config: QAConfig) -> list[QueryPlan]:
    """The question itself first; an addendum-scoped lexical query when revisions can matter."""
    plan: list[QueryPlan] = [(question, config.mode, [], [], config.top_k)]
    numbers = sorted({int(n) for n in ADDENDUM_NUMBER.findall(question)})
    if numbers:
        plan.append(
            (
                question,
                RetrievalMode.sparse,
                [DocumentType.addendum],
                numbers,
                config.addendum_top_k,
            )
        )
    elif DEADLINE_TERMS.search(question):
        plan.append(
            (question, RetrievalMode.sparse, [DocumentType.addendum], [], config.addendum_top_k)
        )
    return plan


def _evidence_block(hits: list[EvidenceHit], limit: int) -> str:
    lines = []
    for hit in hits:
        source = hit.sources[0]
        where = f"page {source.page}" if source.page else f"html #{source.html_element_id}"
        addendum = f" addendum {source.addendum_number}" if source.addendum_number else ""
        text = " ".join(hit.text.split())
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        lines.append(
            f"[{hit.evidence_id}] {source.file} {where} ({source.document_type.value}{addendum})"
        )
        lines.append(text)
    return "\n".join(lines)


SYSTEM = (
    "You answer questions about one procurement bid using only the numbered passages. "
    'Reply with JSON only: {"status": "answered" | "not_found" | "conflict", '
    '"answer": "...", "citations": [{"evidence_id": "E1", "quote": "..."}]}. '
    "Each quote must be copied exactly from the cited passage. Every date, number and "
    "identifier in the answer must appear in a quote. Keep the answer to 1-3 sentences. "
    f'If the passages do not answer the question, use status not_found and answer "{NOT_FOUND}" '
    "with no citations. When an addendum explicitly revises an earlier value, answer with the "
    "revised value and cite the addendum. When sources disagree and no passage states which one "
    "controls, use status conflict, cite both sources and say what each says. Never invent "
    "documents, pages, quotes or facts."
)


def answer_request(question: str, hits: list[EvidenceHit], config: QAConfig) -> ModelRequest:
    return ModelRequest(
        purpose="qa:answer",
        system=SYSTEM,
        user=f"Question: {question}\n\nPassages:\n{_evidence_block(hits, config.excerpt_chars)}",
    )


def correction_request(
    question: str, hits: list[EvidenceHit], reasons: list[str], config: QAConfig
) -> ModelRequest:
    listed = "\n".join(f"- {reason}" for reason in reasons)
    return ModelRequest(
        purpose="qa:correct",
        system=SYSTEM,
        user=(
            f"Question: {question}\n\nYour previous reply was rejected:\n{listed}\n"
            "Fix the citations or quotes, or answer not_found.\n\n"
            f"Passages:\n{_evidence_block(hits, config.excerpt_chars)}"
        ),
    )


def parse_reply(text: str) -> ModelReply:
    payload = parse_json_object(text)
    try:
        return ModelReply.model_validate(payload)
    except ValueError as exc:
        raise ModelError("llm_malformed", "Model reply did not match the answer schema") from exc


def _facts(answer: str) -> list[str]:
    found = list(date_phrases(answer))
    for match in FACT.finditer(answer):
        token = match.group(0).strip(".,:")
        if sum(ch.isdigit() for ch in token) >= 1 and len(token) >= 2:
            found.append(token)
    return found


def check_reply(
    question: str, reply: ModelReply, hits: list[EvidenceHit]
) -> tuple[list[QACitation], list[CitationCheck], list[str]]:
    """Deterministic grounding. Returns rendered citations, per-citation checks and reasons."""
    if reply.status == "not_found":
        return [], [], []
    by_id = {hit.evidence_id: hit for hit in hits}
    citations: list[QACitation] = []
    checks: list[CitationCheck] = []
    reasons: list[str] = []
    if not reply.answer.strip() or reply.answer.strip() == NOT_FOUND:
        reasons.append("A factual status needs a factual answer")
    if not reply.citations:
        reasons.append("A factual answer has no citation")
    cited: list[EvidenceHit] = []
    for item in reply.citations:
        hit = by_id.get(item.evidence_id)
        if hit is None:
            checks.append(
                CitationCheck(evidence_id=item.evidence_id, valid=False, reason="fabricated")
            )
            reasons.append(f"Citation {item.evidence_id} is not a retrieved passage")
            continue
        source = hit.sources[0]
        quote = item.quote.strip().removesuffix("...").strip()
        problem = locator_problem(source)
        if problem is None and source.bid_id != hit.bid_id:
            problem = "citation belongs to another bid"
        if problem is None and not quote:
            problem = "empty quote"
        if problem is None and not contained(quote, hit.text):
            problem = "quote is not in the cited passage"
        checks.append(
            CitationCheck(evidence_id=item.evidence_id, valid=problem is None, reason=problem)
        )
        if problem:
            reasons.append(f"Citation {item.evidence_id}: {problem}")
            continue
        cited.append(hit)
        citations.append(
            QACitation(
                evidence_id=hit.evidence_id,
                document=source.file,
                page=source.page,
                html_element_id=source.html_element_id,
                document_type=source.document_type,
                addendum_number=source.addendum_number,
                excerpt=" ".join(quote.split()),
            )
        )
    support = "\n".join(hit.text for hit in cited)
    for fact in _facts(reply.answer):
        if cited and not contained(fact, support):
            reasons.append(f"'{fact}' does not appear in the cited passages")
    if reply.status == "conflict" and len(_distinct(citations)) < 2:
        reasons.append("A conflict needs at least two different cited sources")
    if reply.status == "answered" and asks_due_date(question) and date_phrases(reply.answer):
        draft = DraftField(name="Due Date", value=reply.answer, notes="qa")
        decision = reconcile_due_date(draft, hits)
        if decision.change is not None and decision.reject_reason:
            reasons.append(f"Addendum check: {decision.reject_reason}")
    return citations, checks, reasons


def asks_due_date(question: str) -> bool:
    return bool(DEADLINE_TERMS.search(question) and SUBMISSION_TERMS.search(question))


def _event(agent: str, task: str, started: str, stamp: float, **payload: object) -> TraceEvent:
    return TraceEvent.model_validate(
        {
            "sequence": 0,
            "agent": agent,
            "task": task,
            "started_at": started,
            "ended_at": clock(),
            "duration_ms": round((time.perf_counter() - stamp) * 1000, 2),
            **payload,
        }
    )


def _refs(hits: list[EvidenceHit]) -> list[dict[str, str | int | None]]:
    return [
        {
            "evidence_id": hit.evidence_id,
            "file": hit.sources[0].file,
            "page": hit.sources[0].page,
            "html_element_id": hit.sources[0].html_element_id,
        }
        for hit in hits
    ]


def gather_evidence(
    question: str,
    bid_uuid: UUID,
    search: Callable[[SearchRequest], SearchResponse],
    config: QAConfig,
    events: list[TraceEvent],
) -> list[EvidenceHit]:
    collected: list[EvidenceHit] = []
    lock = threading.Lock()
    for query, mode, doc_types, addenda, top_k in plan_queries(question, config):
        started, stamp = clock(), time.perf_counter()
        hits, response = retrieve(
            search,
            bid_uuid,
            query,
            mode=mode,
            top_k=top_k,
            doc_types=doc_types,
            addendum_numbers=addenda,
            evidence_prefix="raw",
            lock=lock,
        )
        collected.extend(hits)
        events.append(
            _event(
                "retrieval",
                "search",
                started,
                stamp,
                query_hash=query_hash(query),
                mode=response.mode.value,
                top_k=top_k,
                doc_types=[item.value for item in doc_types],
                result_count=len(hits),
                evidence=_refs(hits[:10]),
                decision=", ".join(response.warnings) or None,
            )
        )
    seen: set[tuple[str, int | None, str | None, str]] = set()
    unique: list[EvidenceHit] = []
    for hit in collected:
        source = hit.sources[0]
        key = (source.relative_path, source.page, source.html_element_id, hit.text)
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return [
        hit.model_copy(update={"evidence_id": f"E{index}"})
        for index, hit in enumerate(unique[: config.max_evidence], start=1)
    ]


def ask(
    bid_label: str,
    bid_uuid: UUID,
    question: str,
    search: Callable[[SearchRequest], SearchResponse],
    model: LanguageModel,
    config: QAConfig | None = None,
) -> tuple[QAResponse, QATrace]:
    config = config or QAConfig()
    text = " ".join(question.split())
    if not text or len(text) > config.max_question_chars:
        raise ValueError("invalid_question")
    trace_id = str(uuid.uuid4())
    events: list[TraceEvent] = []
    tokens = TokenUsage()
    provider = str(getattr(model, "provider", "unknown"))
    model_name = str(getattr(model, "model", "unknown"))
    hits = gather_evidence(text, bid_uuid, search, config, events)

    def finish(
        status: QAStatus,
        answer: str,
        citations: list[QACitation],
        validation: QAValidation,
    ) -> tuple[QAResponse, QATrace]:
        metadata = ModelMetadata(provider=provider, model=model_name)
        response = QAResponse(
            bid_id=bid_label,
            question=text,
            answer=answer,
            status=status,
            citations=citations,
            validation=validation,
            trace_id=trace_id,
            model=metadata,
            evidence_count=len(hits),
        )
        trace = QATrace(
            trace_id=trace_id,
            bid_id=bid_label,
            bid_uuid=bid_uuid,
            question_hash=query_hash(text),
            question_chars=len(text),
            events=[
                event.model_copy(update={"sequence": index}) for index, event in enumerate(events)
            ],
            status=status,
            validation=validation,
            token_usage=tokens,
            model=metadata,
        )
        return response, trace

    if not hits:
        return finish(
            "not_found",
            NOT_FOUND,
            [],
            QAValidation(passed=True, attempts=0, reasons=["No evidence retrieved for this bid"]),
        )

    request = answer_request(text, hits, config)
    reasons: list[str] = []
    checks: list[CitationCheck] = []
    citations: list[QACitation] = []
    attempts = 0
    while attempts <= config.max_corrections:
        attempts += 1
        started, stamp = clock(), time.perf_counter()
        completion: ModelResponse = model.complete(request)
        account(tokens, completion)
        provider, model_name = completion.provider, completion.model
        try:
            reply = parse_reply(completion.text)
        except ModelError as exc:
            reply = None
            reasons = [f"{exc.code}: {exc.message}"]
            checks = []
        if reply is not None:
            citations, checks, reasons = check_reply(text, reply, hits)
        valid_ids = {check.evidence_id for check in checks if check.valid}
        events.append(
            _event(
                "qa",
                "answer" if attempts == 1 else "correct",
                started,
                stamp,
                retry=attempts - 1,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                provider=completion.provider,
                model=completion.model,
                decision=None if reply is None else reply.status,
                reasons=reasons,
                evidence=_refs([hit for hit in hits if hit.evidence_id in valid_ids]),
            )
        )
        if reply is not None and not reasons:
            if reply.status == "not_found":
                return finish(
                    "not_found",
                    NOT_FOUND,
                    [],
                    QAValidation(passed=True, attempts=attempts, reasons=["Model abstained"]),
                )
            return finish(
                reply.status,
                " ".join(reply.answer.split()),
                citations,
                QAValidation(passed=True, attempts=attempts, checks=checks),
            )
        request = correction_request(text, hits, reasons, config)
    return finish(
        "not_found",
        NOT_FOUND,
        [],
        QAValidation(
            passed=False,
            attempts=attempts,
            checks=checks,
            reasons=["Abstained after failed citation validation", *reasons],
        ),
    )


def dump(model: Contract) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
