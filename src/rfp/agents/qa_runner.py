"""CLI execution for `ask` and `ask-samples`. Read-only against loopback Qdrant."""

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import Field, ValidationError

from rfp.agents.errors import ExtractionError
from rfp.agents.llm import LanguageModel, ModelRequest, ModelResponse
from rfp.agents.qa import NOT_FOUND, QAConfig, QAResponse, QATrace, ask, dump
from rfp.agents.runner import assert_output_dir, model_for, resolve_bid
from rfp.agents.safety import assert_local_qdrant
from rfp.schemas.parsed import Contract
from rfp.schemas.search import SearchRequest, SearchResponse

Search = Callable[[SearchRequest], SearchResponse]


class AbstainModel:
    """Explicit test model. It always abstains and never produces an answer."""

    provider = "fake"
    model = "abstain"

    def complete(self, request: ModelRequest) -> ModelResponse:
        text = json.dumps({"status": "not_found", "answer": NOT_FOUND, "citations": []})
        return ModelResponse(text, self.provider, self.model, 0, 0, 0.0)


class SampleQuestion(Contract):
    bid_id: str = Field(min_length=1)
    question: str = Field(min_length=3)
    purpose: str = ""


def qa_model(mode: str, fixture: Path | None) -> LanguageModel:
    if mode == "test" and fixture is None:
        return AbstainModel()
    return model_for(mode, fixture)


def local_search(qdrant_url: str, collection: str | None = None) -> Search:
    safe_url = assert_local_qdrant(qdrant_url)
    from rfp.cli import local_settings
    from rfp.search.wiring import search_service

    settings = local_settings(safe_url)
    if collection:
        settings.qdrant_collection = collection
    return search_service(settings).search


def write_trace(trace_dir: Path, trace: QATrace) -> Path:
    directory = assert_output_dir(trace_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"qa_{trace.trace_id}.json"
    path.write_text(dump(trace), encoding="utf-8")
    return path


def execute_ask(
    *,
    bid: str,
    question: str,
    qdrant_url: str,
    llm_mode: str,
    fixture: Path | None,
    trace_dir: Path | None,
    output: Path | None,
    search: Search | None = None,
    config: QAConfig | None = None,
    collection: str | None = None,
    model: LanguageModel | None = None,
) -> tuple[QAResponse, Path | None]:
    safe_url = assert_local_qdrant(qdrant_url)
    label, bid_uuid = resolve_bid(bid)
    if output is not None:
        assert_output_dir(output.parent)
    model = model or qa_model(llm_mode, fixture)
    if search is None:
        search = local_search(safe_url, collection) if collection else local_search(safe_url)
    response, trace = ask(label, bid_uuid, question, search, model, config)
    trace_path = write_trace(trace_dir, trace) if trace_dir is not None else None
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(dump(response), encoding="utf-8")
    return response, trace_path


def load_questions(path: Path) -> list[SampleQuestion]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        questions = [SampleQuestion.model_validate(item) for item in payload]
    except (OSError, json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise ExtractionError(
            "invalid_questions", "Questions file must be a JSON list of {bid_id, question}"
        ) from exc
    if not questions:
        raise ExtractionError("invalid_questions", "Questions file is empty")
    return questions


def render_markdown(responses: list[QAResponse]) -> str:
    lines = ["# Cited Q&A examples", ""]
    first = responses[0].model if responses else None
    if first is not None:
        lines += [f"Model: `{first.provider}` / `{first.model}`", ""]
    for index, item in enumerate(responses, start=1):
        lines += [
            f"## {index}. {item.bid_id}: {item.question}",
            "",
            f"**Status:** {item.status}  ",
            f"**Answer:** {item.answer}",
            "",
        ]
        for citation in item.citations:
            where = (
                f"page {citation.page}"
                if citation.page is not None
                else f"html #{citation.html_element_id}"
            )
            lines.append(f'- {citation.document}, {where}: "{citation.excerpt}"')
        if not item.validation.passed:
            lines.append(f"- Validation: {'; '.join(item.validation.reasons)}")
        lines += [f"- Trace: `{item.trace_id}`", ""]
    return "\n".join(lines)


def generate_samples(
    *,
    questions: list[SampleQuestion],
    output_dir: Path,
    search: Search,
    model: LanguageModel,
    config: QAConfig | None = None,
) -> dict:
    destination = assert_output_dir(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    responses: list[QAResponse] = []
    for item in questions:
        label, bid_uuid = resolve_bid(item.bid_id)
        response, trace = ask(label, bid_uuid, item.question, search, model, config)
        write_trace(destination / "traces", trace)
        responses.append(response)
    samples = destination / "qa_samples.json"
    samples.write_text(
        json.dumps([r.model_dump(mode="json") for r in responses], indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (destination / "qa_samples.md").write_text(render_markdown(responses), encoding="utf-8")
    statuses = [r.status for r in responses]
    return {
        "output": str(samples),
        "questions": len(responses),
        "bids": sorted({r.bid_id for r in responses}),
        "answered": statuses.count("answered"),
        "conflict": statuses.count("conflict"),
        "not_found": statuses.count("not_found"),
        "model": responses[0].model.model_dump() if responses else None,
    }
