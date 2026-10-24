"""process-bid: one local bid folder through parse, chunk, index, extract and verify.

Each stage calls the existing service (``parse_folder``, ``chunk_folder``,
``load_local_artifacts`` + ``IndexingService``, ``runner.execute``, ``check_bid``). This module
only sequences them, checks that every artifact belongs to the requested bid, and writes a
machine-readable run summary. Qdrant must be loopback; nothing is written to R2 or PostgreSQL.
"""

import json
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qdrant_client import models

from rfp.agents.errors import ExtractionError
from rfp.agents.llm import LanguageModel
from rfp.agents.runner import assert_output_dir, execute, resolve_bid
from rfp.agents.safety import assert_local_qdrant
from rfp.agents.schemas import ExtractionConfig, ExtractionTrace
from rfp.core.config import Settings
from rfp.evaluation.extraction_check import check_bid, leaks, pages_from_parsed, secret_values
from rfp.ingestion.discovery import DiscoveryError, DiscoveryResult, discover
from rfp.schemas.parsed import ParsedDocument, ParseStatus

PIPELINE_VERSION = "1"
EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_PARTIAL = 0, 1, 2, 3
REJECTED_INPUTS = {
    "unsupported_type",
    "symbolic_link",
    "not_regular_file",
    "hidden_or_link_directory",
}
SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{2,80}")

# (indexing service, search callable) for the given settings.
Services = Callable[[Settings], tuple[Any, Callable[[Any], Any]]]


class PipelineError(Exception):
    """A request the pipeline refuses before writing anything."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


class StageFailed(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass
class Stage:
    name: str
    status: str = "skipped"
    duration_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            **self.detail,
        }
        if self.error:
            result["error"] = self.error
        return result


def _code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and SAFE_CODE.fullmatch(code):
        return code
    # Exception text can contain document content; only bare error codes are reported.
    text = str(exc)
    return text if SAFE_CODE.fullmatch(text) else "stage_failed"


def _run(stage: Stage, action: Callable[[Stage], None]) -> bool:
    started = time.monotonic()
    try:
        action(stage)
    except Exception as exc:
        stage.status = "failed"
        stage.error = {"code": _code(exc)}
        message = getattr(exc, "message", None)
        if isinstance(message, str) and message:
            stage.error["message"] = message
    stage.duration_ms = (time.monotonic() - started) * 1000
    return stage.status != "failed"


def _prepare(
    input_dir: Path, output: Path, bid: str | None, settings: Settings
) -> tuple[Path, Path, str, uuid.UUID, str, DiscoveryResult]:
    root = input_dir.expanduser().resolve()
    if not root.is_dir():
        raise PipelineError("input_directory_not_found", "The input bid folder does not exist")
    try:
        label, bid_uuid = resolve_bid(bid or root.name)
    except ExtractionError as exc:
        raise PipelineError(
            exc.code, "Pass --bid-id as a short label (letters, digits, '.', '_' or '-')"
        ) from exc
    try:
        uuid.UUID(label)
    except ValueError:
        pass
    else:
        raise PipelineError("invalid_bid_id", "Use a short label; the bid UUID is derived from it")
    safe_url = assert_local_qdrant(settings.qdrant_url)
    destination = assert_output_dir(output)
    if destination == root or root in destination.parents:
        raise PipelineError(
            "output_inside_input", "The output folder must be outside the input bid folder"
        )
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise PipelineError(
            "output_exists", "The output folder already has content; choose a new folder"
        )
    try:
        discovery = discover(root, settings, label)
    except DiscoveryError as exc:
        raise PipelineError(str(exc), "The input folder failed validation") from exc
    if discovery.bid_id != bid_uuid:
        raise PipelineError("bid_identity_mismatch", "Discovery derived a different bid UUID")
    if not discovery.files:
        raise PipelineError(
            "no_supported_documents", "The input folder has no PDF or HTML documents"
        )
    return root, destination, label, bid_uuid, safe_url, discovery


def _stale_points(indexer: Any, bid_uuid: uuid.UUID, document_ids: set[str]) -> int:
    """Active points for this bid from documents that are not in this folder."""
    manager = indexer.collection()
    profile = indexer.runtime.profile().fingerprint
    conditions: list[models.Condition] = [
        models.FieldCondition(key="bid_id", match=models.MatchValue(value=str(bid_uuid))),
        models.FieldCondition(key="index_active", match=models.MatchValue(value=True)),
        models.FieldCondition(key="profile_fingerprint", match=models.MatchValue(value=profile)),
    ]
    must_not: list[models.Condition] = [
        models.FieldCondition(key="document_id", match=models.MatchAny(any=sorted(document_ids)))
    ]
    return int(
        indexer.client.count(
            manager.name,
            count_filter=models.Filter(must=conditions, must_not=must_not),
            exact=True,
        ).count
    )


def process_bid(
    input_dir: Path,
    output: Path,
    *,
    settings: Settings,
    services: Services,
    bid: str | None = None,
    llm_mode: str = "live",
    fixture: Path | None = None,
    model: LanguageModel | None = None,
    config: ExtractionConfig | None = None,
    env_file: Path = Path(".env"),
) -> tuple[int, dict[str, Any]]:
    """Run every stage; return the exit code and the run summary written to the output folder.

    Raises ``PipelineError`` (nothing written) for an invalid request. Stage failures are
    recorded in the summary, later stages are skipped, and the exit code is non-zero.
    """
    from rfp.chunking.local import chunk_folder
    from rfp.cli import parse_folder
    from rfp.search.indexing import load_local_artifacts

    started_at = datetime.now(UTC)
    started = time.monotonic()
    root, destination, label, bid_uuid, safe_url, discovery = _prepare(
        input_dir, output, bid, settings
    )
    destination.mkdir(parents=True, exist_ok=True)
    parsed_dir, chunks_dir, traces_dir = (
        destination / "parsed",
        destination / "chunks",
        destination / "traces",
    )
    bid_path = destination / f"{label}.json"
    trace_path = traces_dir / f"extraction_{label}.json"
    stages = {
        name: Stage(name) for name in ("validate", "parse", "chunk", "index", "extract", "verify")
    }
    shared: dict[str, Any] = {}

    def validate(stage: Stage) -> None:
        rejected = [item for item in discovery.ignored if item["reason"] in REJECTED_INPUTS]
        stage.detail = {
            "documents": [source.relative_path for source in discovery.files],
            "ignored": discovery.ignored,
            "warnings": discovery.warnings,
        }
        stage.status = "partial" if rejected else "succeeded"

    def parse(stage: Stage) -> None:
        summary, _ = parse_folder(root, parsed_dir, settings, label)
        if summary["bid_id"] != str(bid_uuid):
            raise StageFailed("bid_isolation_violation")
        documents: list[dict[str, Any]] = []
        for path in sorted(parsed_dir.glob("*.json")):
            if path.name == "summary.json":
                continue
            document = ParsedDocument.model_validate_json(path.read_bytes())
            if document.bid_id != bid_uuid:
                raise StageFailed("bid_isolation_violation")
            page_warnings = Counter(
                warning for page in document.parsed_pages for warning in page.warnings
            )
            documents.append(
                {
                    "file": document.relative_path,
                    "document_id": str(document.document_id),
                    "doc_type": document.doc_type.value,
                    "addendum_number": document.addendum_number,
                    "pages": document.page_count,
                    "status": document.processing_status.value,
                    "warnings": document.warnings[:10],
                    "page_warnings": dict(sorted(page_warnings.items())),
                }
            )
        usable = [item for item in documents if item["status"] != ParseStatus.failed.value]
        shared["documents"] = sorted(Path(item["file"]).name for item in usable)
        stage.detail = {
            "parsed": summary["parsed"],
            "partially_parsed": summary["partially_parsed"],
            "failed": summary["failed"],
            "documents": documents,
        }
        if not usable:
            raise StageFailed("no_parsable_documents", "Every document failed to parse")
        clean = len(usable) == len(documents) and not summary["partially_parsed"]
        stage.status = "succeeded" if clean else "partial"

    def chunk(stage: Stage) -> None:
        summary, _ = chunk_folder(parsed_dir, chunks_dir, settings)
        stage.detail = {
            key: summary[key]
            for key in ("source_documents", "chunks", "failures", "partial_documents")
        }
        if not summary["chunks"]:
            raise StageFailed("no_chunks", "No document produced searchable chunks")
        clean = not summary["failures"] and not summary["partial_documents"]
        stage.status = "succeeded" if clean else "partial"

    def index(stage: Stage) -> None:
        generations, failures = load_local_artifacts(chunks_dir, settings)
        if any(generation.bid_id != bid_uuid for generation in generations):
            raise StageFailed("bid_isolation_violation")
        if not generations:
            raise StageFailed("no_indexable_chunks")
        indexer, search = services(settings)
        shared["search"] = search
        report = indexer.index(generations)
        report.input_failures = failures
        summary = report.as_dict()
        (destination / "index.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        stage.detail = {
            key: summary[key]
            for key in (
                "collection",
                "generations",
                "completed",
                "unchanged",
                "failed",
                "pending",
                "points",
                "input_failures",
            )
        }
        if not summary["completed"] + summary["unchanged"]:
            raise StageFailed("indexing_failed", "No chunk generation was indexed")
        stale = _stale_points(indexer, bid_uuid, {str(g.document_id) for g in generations})
        stage.detail["other_active_points_for_bid"] = stale
        if stale:
            raise StageFailed(
                "bid_has_other_documents",
                "The collection already holds active evidence for this bid id from documents "
                "not in this folder; use a new --bid-id or --collection",
            )
        clean = not summary["failed"] and not summary["pending"] and not failures
        stage.status = "succeeded" if clean else "partial"

    def extract(stage: Stage) -> None:
        code, summary = execute(
            bid=label,
            output=destination,
            trace_dir=traces_dir,
            qdrant_url=safe_url,
            llm_mode=llm_mode,
            fixture=fixture,
            search=shared["search"],
            config=config,
            model=model,
            documents=shared["documents"],
        )
        trace = ExtractionTrace.model_validate_json(trace_path.read_bytes())
        validation = summary["validation"]
        stage.detail = {
            "outcome": summary["outcome"],
            "validation": validation,
            "model": summary["model"],
            "model_errors": sum(1 for event in trace.events if event.error),
            "token_usage": trace.token_usage.model_dump(),
        }
        if code:
            raise StageFailed("extraction_failed", "The extraction workflow did not complete")
        if not validation["passed"] and validation["unresolved"]:
            raise StageFailed(
                "no_values_extracted", "No field passed validation; see the trace for model errors"
            )
        stage.status = "partial" if validation["unresolved"] else "succeeded"

    def verify(stage: Stage) -> None:
        max_retries = (config or ExtractionConfig()).max_retries
        report = check_bid(bid_path, trace_path, pages_from_parsed(parsed_dir, label), max_retries)
        files = {name for _, name in pages_from_parsed(parsed_dir, label)}
        problems, notes = [], []
        if not report["field_names_ok"]:
            problems.append("The output does not hold the 20 required fields in order")
        for row in report["fields"]:
            if row["status"] != "accepted":
                continue
            if row["foreign_files"]:
                problems.append(f"{row['field']}: cites a file that is not in this bid")
            if row["token_share"] == 0:
                problems.append(f"{row['field']}: value is not on its cited pages")
            elif row["support_on_cited_pages"] == "partial":
                notes.append(f"{row['field']}: restated (token share {row['token_share']})")
            if row["sources_without_value_tokens"]:
                notes.append(f"{row['field']}: some cited sources lack the value's tokens")
        for change in report["addendum_changes"]:
            if {source["file"] for source in change["sources"]} - files:
                problems.append(f"Addendum change for {change['field']} cites another bid")
        trace = report.get("trace")
        if trace is None:
            problems.append("The extraction trace is missing")
        elif not trace["retries_bounded"]:
            problems.append("Retries exceeded the configured bound")
        written = [bid_path, trace_path, destination / "index.json"]
        leaked = leaks([path for path in written if path.is_file()], secret_values(env_file))
        if leaked:
            problems.append("A configured secret or key-shaped string appears in the outputs")
        report["secret_leaks"] = [Path(path).name for path in leaked]
        report["problems"], report["notes"] = problems, notes
        (destination / "verification.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        accepted = [row for row in report["fields"] if row["status"] == "accepted"]
        stage.detail = {
            "schema_valid": report["schema_valid"],
            "accepted": len(accepted),
            "verbatim": sum(r["support_on_cited_pages"] == "verbatim" for r in accepted),
            "addendum_changes": len(report["addendum_changes"]),
            "problems": problems,
            "notes": notes,
        }
        if problems:
            raise StageFailed("verification_failed", "; ".join(problems))
        stage.status = "succeeded"

    actions = {
        "validate": validate,
        "parse": parse,
        "chunk": chunk,
        "index": index,
        "extract": extract,
        "verify": verify,
    }
    for name, action in actions.items():
        if not _run(stages[name], action):
            break

    statuses = [stage.status for stage in stages.values()]
    if "failed" in statuses:
        status, exit_code = "failed", EXIT_FAILED
    elif "partial" in statuses:
        status, exit_code = "partial", EXIT_PARTIAL
    else:
        status, exit_code = "succeeded", EXIT_OK
    outputs = {
        "extraction": bid_path,
        "trace": trace_path,
        "verification": destination / "verification.json",
        "index_report": destination / "index.json",
        "parsed": parsed_dir,
        "chunks": chunks_dir,
    }
    summary: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "bid_id": label,
        "bid_uuid": str(bid_uuid),
        "input_folder": root.name,
        "status": status,
        "exit_code": exit_code,
        "started_at": started_at.isoformat(),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "qdrant": {"url": safe_url, "collection": settings.qdrant_collection},
        "llm_mode": llm_mode,
        "stages": [stage.as_dict() for stage in stages.values()],
        "outputs": {
            key: path.relative_to(destination).as_posix()
            for key, path in outputs.items()
            if path.exists()
        },
    }
    (destination / "run_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return exit_code, summary
