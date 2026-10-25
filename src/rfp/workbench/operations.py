"""Calls the existing local runners. Nothing here reimplements retrieval or extraction."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rfp.agents.errors import ExtractionError, ModelError
from rfp.agents.qa import QAConfig
from rfp.agents.qa_runner import execute_ask, execute_compare
from rfp.agents.runner import execute
from rfp.agents.schemas import REQUIRED_FIELDS, ExtractionConfig
from rfp.cli import LOCAL_QDRANT, local_settings
from rfp.pipeline import PipelineError, process_bid
from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest
from rfp.search.wiring import local_indexing_service, search_service
from rfp.workbench.errors import WorkbenchError
from rfp.workbench.guards import workspace_root

TRACE_DIRS = (
    "artifacts/workbench/traces",
    "artifacts/extraction_v4/traces",
    "artifacts/phaseE_reliability/extraction_r3/traces",
)


def _fail(exc: ExtractionError | PipelineError, status: int = 400) -> WorkbenchError:
    return WorkbenchError(exc.code, exc.message, status)


def ask(bid: str, question: str, collection: str, mode: str, top_k: int | None) -> dict[str, Any]:
    config: dict[str, Any] = {"mode": mode}
    if top_k is not None:
        config["top_k"] = top_k
    try:
        response, trace_path = execute_ask(
            bid=bid,
            question=question,
            qdrant_url=LOCAL_QDRANT,
            llm_mode="live",
            fixture=None,
            trace_dir=workspace_root() / "artifacts" / "workbench" / "traces",
            output=None,
            config=QAConfig.model_validate(config),
            collection=collection,
        )
    except (ExtractionError, ModelError) as exc:
        raise _fail(exc, 422 if exc.code in {"invalid_bid_id", "unknown_bid"} else 502) from exc
    except ValueError as exc:
        if str(exc) == "invalid_question":
            raise WorkbenchError("invalid_question", "Question must be 1-1000 characters.") from exc
        raise
    payload = response.model_dump(mode="json")
    payload["trace_id_path"] = trace_path.name if trace_path else None
    return payload


def compare(bids: list[str], question: str, collection: str) -> dict[str, Any]:
    try:
        response, trace_path = execute_compare(
            bids=bids,
            question=question,
            qdrant_url=LOCAL_QDRANT,
            llm_mode="live",
            fixture=None,
            trace_dir=workspace_root() / "artifacts" / "workbench" / "traces",
            output=None,
            collection=collection,
        )
    except (ExtractionError, ModelError) as exc:
        status = (
            422 if exc.code in {"invalid_bid_id", "invalid_comparison", "duplicate_bid"} else 502
        )
        raise _fail(exc, status) from exc
    except ValueError as exc:
        if str(exc) == "invalid_question":
            raise WorkbenchError("invalid_question", "Question must be 1-1000 characters.") from exc
        raise
    payload = response.model_dump(mode="json")
    payload["trace_id_path"] = trace_path.name if trace_path else None
    return payload


def search(query: str, collection: str, mode: str, bid_ids: list[str], top_k: int | None) -> dict:
    from rfp.agents.runner import resolve_bid

    settings = local_settings(LOCAL_QDRANT)
    settings.qdrant_collection = collection
    filters = SearchFilters(bid_ids=[resolve_bid(bid)[1] for bid in bid_ids])
    request = SearchRequest(query=query, top_k=top_k, mode=RetrievalMode(mode), filters=filters)
    try:
        return search_service(settings).search(request).model_dump(mode="json")
    except ExtractionError as exc:
        raise _fail(exc) from exc


def extract(bid: str, collection: str) -> dict[str, Any]:
    destination = workspace_root() / "artifacts" / "workbench" / "extractions" / collection
    try:
        code, summary = execute(
            bid=bid,
            output=destination,
            trace_dir=destination / "traces",
            qdrant_url=LOCAL_QDRANT,
            llm_mode="live",
            fixture=None,
            search=None,
            config=ExtractionConfig.model_validate({"critic_enabled": True}),
            collection=collection,
        )
    except (ExtractionError, ModelError) as exc:
        raise _fail(exc, 422 if exc.code == "invalid_bid_id" else 502) from exc
    return {"exit_code": code, "summary": summary, "fields": list(REQUIRED_FIELDS)}


def ingest(source: Path, bid: str, collection: str) -> dict[str, Any]:
    destination = workspace_root() / "artifacts" / "workbench" / "runs" / collection / bid
    settings = local_settings(LOCAL_QDRANT)
    settings.qdrant_collection = collection

    def services(current):
        return local_indexing_service(current, None), search_service(current).search

    try:
        code, summary = process_bid(
            source,
            destination,
            settings=settings,
            services=services,
            bid=bid,
            llm_mode="live",
            config=ExtractionConfig(critic_enabled=True),
        )
    except (PipelineError, ExtractionError, ModelError) as exc:
        raise _fail(exc, 422) from exc
    return {"exit_code": code, "summary": summary}


def _script(name: str):
    path = workspace_root() / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"workbench_{name}", path)
    if spec is None or spec.loader is None:
        raise WorkbenchError("operation_failed", "The requested script is not available.", 500)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(collection: str) -> dict[str, Any]:
    module = _script("evaluate_retrieval")
    destination = workspace_root() / "artifacts" / "workbench" / "evaluation"
    destination.mkdir(parents=True, exist_ok=True)
    out = destination / f"{collection}.json"
    if out.exists():
        raise WorkbenchError(
            "output_exists",
            "An evaluation file for that collection already exists and was not overwritten.",
            409,
        )
    args = SimpleNamespace(
        dataset=module.DATASET,
        modes=",".join(module.MODES),
        rerank_depth=None,
        out=out,
        collection=collection,
    )
    code = module.run(args)
    return {"exit_code": code, "source": out.relative_to(workspace_root()).as_posix()}


def audit() -> dict[str, Any]:
    module = _script("audit_submission")
    try:
        extractions = []
        for run, folder, parsed in module.EXTRACTIONS:
            for bid in ("Bid1", "Bid2"):
                output = module.ROOT / folder.format(bid=bid) / f"{bid}.json"
                if not output.is_file():
                    continue
                trace = output.parent / "traces" / f"extraction_{bid}.json"
                extractions.append(
                    module.check_extraction(
                        run, output, trace, module.ROOT / parsed.format(bid=bid)
                    )
                )
        result: dict[str, Any] = {
            "available": True,
            "extractions": extractions,
            "qa": module.check_qa(module.ROOT / module.QA_EXAMPLES),
            "retrieval": module.check_retrieval(),
        }
    except FileNotFoundError:
        return {
            "available": False,
            "problem_count": None,
            "detail": "Saved submission artifacts are not all present.",
        }
    problems = sum(len(item["problems"]) for item in extractions)
    problems += len(result["qa"]["problems"]) + len(result["retrieval"]["problems"])
    result["problem_count"] = problems
    return result


def find_trace(trace_id: str) -> dict[str, Any] | None:
    import json
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", trace_id):
        raise WorkbenchError("invalid_trace", "Trace ids use letters, digits, _ and -.")
    root = workspace_root()
    names = (f"qa_{trace_id}.json", f"qa_compare_{trace_id}.json", f"extraction_{trace_id}.json")
    folders = [root / item for item in TRACE_DIRS]
    folders += [path for path in (root / "artifacts").glob("*/*/traces") if path.is_dir()]
    folders += [
        path
        for path in (root / "artifacts" / "workbench").glob("extractions/*/traces")
        if path.is_dir()
    ]
    for folder in folders:
        if not folder.is_dir():
            continue
        for name in names:
            path = folder / name
            if path.is_file() and not path.is_symlink():
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["source"] = path.relative_to(root).as_posix()
                return payload
    return None
