"""CLI execution for extract-bid. This module does not index, enqueue, or write Qdrant."""

import json
import re
import unicodedata
import uuid
from pathlib import Path

from rfp.agents.errors import ExtractionError, ModelError
from rfp.agents.llm import LanguageModel, ModelRequest, ModelResponse, build_live_model
from rfp.agents.orchestrator import run_extraction
from rfp.agents.safety import assert_local_qdrant
from rfp.agents.schemas import REQUIRED_FIELDS, BidExtraction, ExtractionConfig, ExtractionTrace
from rfp.ingestion.discovery import NAMESPACE


def resolve_bid(value: str) -> tuple[str, uuid.UUID]:
    label = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", label):
        raise ExtractionError("invalid_bid_id", "Bid id must be a short label or UUID")
    try:
        return label, uuid.UUID(label)
    except ValueError:
        normalized = unicodedata.normalize("NFC", label)
        return label, uuid.uuid5(NAMESPACE, normalized)


def assert_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    root = Path.cwd().resolve()
    for item in (root / "evaluation", root / "data" / "assessment"):
        if resolved == item or item in resolved.parents:
            raise ExtractionError(
                "unsafe_output_path",
                "Refusing to write extraction output inside evaluation or assessment inputs",
            )
    return resolved


def source_inventory(label: str) -> list[str]:
    root = Path("data/assessment") / label
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.lower() in {".pdf", ".html", ".htm"}
    )


class NullModel:
    """Explicit test model. It never invents a field value."""

    provider = "fake"
    model = "not-found"

    def complete(self, request: ModelRequest) -> ModelResponse:
        names = re.findall(r"(?m)^Field: (.+)$", request.user)
        if request.purpose.startswith("critic"):
            text = json.dumps({"supported": True, "reason": "not used"})
        else:
            text = json.dumps(
                {
                    "fields": {
                        name: {
                            "value": None,
                            "evidence_ids": [],
                            "notes": "Not found in documents",
                        }
                        for name in names
                    }
                }
            )
        return ModelResponse(text, self.provider, self.model, 0, 0, 0.0)


class FixtureModel:
    provider = "fake"
    model = "fixture"

    def __init__(self, responses: list[str]):
        if not responses or not all(isinstance(item, str) for item in responses):
            raise ExtractionError(
                "invalid_fixture", "The fixture must be a JSON list of response strings"
            )
        self._responses = list(responses)

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self._responses:
            raise ModelError("llm_malformed", "The fixture ran out of scripted responses")
        return ModelResponse(self._responses.pop(0), self.provider, self.model, 1, 1, 0.0)


def model_for(mode: str, fixture: Path | None) -> LanguageModel:
    if mode == "test":
        if fixture is None:
            return NullModel()
        try:
            payload = json.loads(fixture.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExtractionError(
                "invalid_fixture", "The fixture file is not readable JSON"
            ) from exc
        if not isinstance(payload, list):
            raise ExtractionError(
                "invalid_fixture", "The fixture must be a JSON list of response strings"
            )
        return FixtureModel(payload)
    if mode != "live":
        raise ExtractionError("invalid_llm_mode", "LLM mode must be live or test")
    return build_live_model()


def write_outputs(
    output_dir: Path,
    trace_dir: Path,
    bid_label: str,
    document: BidExtraction,
    trace: ExtractionTrace,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    bid_path = output_dir / f"{bid_label}.json"
    trace_path = trace_dir / f"extraction_{bid_label}.json"
    bid_text = json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    trace_text = json.dumps(trace.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    BidExtraction.model_validate_json(bid_text)
    ExtractionTrace.model_validate_json(trace_text)
    if set(json.loads(bid_text)["fields"]) != set(REQUIRED_FIELDS):
        raise ExtractionError("invalid_output", "Extraction JSON is missing required fields")
    bid_path.write_text(bid_text, encoding="utf-8")
    trace_path.write_text(trace_text, encoding="utf-8")
    return bid_path, trace_path


def execute(
    *,
    bid: str,
    output: Path,
    trace_dir: Path | None,
    qdrant_url: str,
    llm_mode: str,
    fixture: Path | None,
    search,
    config: ExtractionConfig | None = None,
    model: LanguageModel | None = None,
    documents: list[str] | None = None,
    collection: str | None = None,
) -> tuple[int, dict]:
    safe_url = assert_local_qdrant(qdrant_url)
    label, bid_uuid = resolve_bid(bid)
    destination = assert_output_dir(output)
    traces = assert_output_dir(trace_dir or destination.parent / "traces")
    model = model or model_for(llm_mode, fixture)
    if search is None:
        from rfp.cli import local_settings
        from rfp.search.wiring import search_service

        settings = local_settings(safe_url)
        if collection:
            settings.qdrant_collection = collection
        search = search_service(settings).search
    document, state, events = run_extraction(
        label,
        bid_uuid,
        search,
        model,
        config,
        documents=source_inventory(label) if documents is None else documents,
    )
    trace = ExtractionTrace.model_validate(
        {
            "workflow_id": str(state.workflow_id),
            "bid_id": label,
            "bid_uuid": bid_uuid,
            "documents": state.documents,
            "events": [event.model_dump() for event in events],
            "final_statuses": {name: state.fields[name].status for name in REQUIRED_FIELDS},
            "outcome": "failed" if state.status == "failed" else "completed",
            "token_usage": state.tokens.model_dump(),
            "model": {"provider": state.provider or "none", "model": state.model_name or "none"},
            "error": state.error,
        }
    )
    bid_path, trace_path = write_outputs(destination, traces, label, document, trace)
    summary = {
        "bid_id": label,
        "bid_uuid": str(bid_uuid),
        "output": str(bid_path),
        "trace": str(trace_path),
        "outcome": trace.outcome,
        "validation": document.validation.model_dump(),
        "model": trace.model.model_dump(),
    }
    return (1 if trace.outcome == "failed" else 0), summary
