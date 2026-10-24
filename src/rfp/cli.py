"""Offline parsing and operator-only production ingestion (no HTTP filesystem access)."""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from rfp.chunking.tokens import TokenizerUnavailable
from rfp.core.config import Settings, get_settings
from rfp.ingestion.discovery import DiscoveryError, discover, read_source
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import artifact_bytes, parse_isolated
from rfp.schemas.parsed import ParseStatus

LOCAL_QDRANT = "http://127.0.0.1:6333"


def local_settings(qdrant_url: str = LOCAL_QDRANT) -> Settings:
    # Deliberately independent of .env and external-service credentials.
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": SecretStr("postgresql+psycopg://local@localhost/local"),
        "redis_url": SecretStr("redis://localhost:6379/0"),
        "qdrant_url": qdrant_url,
        "qdrant_api_key": SecretStr(""),
    }
    return Settings(**values)


def _location(citation: dict) -> str:
    if citation.get("page_start") is not None:
        start, end = citation["page_start"], citation["page_end"]
        location = f"page {start}" if start == end else f"pages {start}-{end}"
    else:
        parts = []
        for locator in citation.get("html_locators", [])[:2]:
            detail = [
                f"#{locator['element_id']}" if locator.get("element_id") else None,
                f"heading '{locator['heading']}'" if locator.get("heading") else None,
                f"element {locator['element_index']}" if locator.get("element_index") else None,
                f"table {locator['table_index']}" if locator.get("table_index") else None,
            ]
            parts.append(" ".join(item for item in detail if item))
        location = "HTML " + "; ".join(p for p in parts if p) if any(parts) else ""
        if not citation.get("location_known"):
            location = "location unknown"
    table = citation.get("table")
    if table:
        location += f" (table {table['table_id']} rows {table['row_start']}-{table['row_end']})"
    return location


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def format_search(response: dict) -> str:
    query = response["query"]
    lines = [
        f"Mode: {response['mode']}  top_k={response['top_k']}  "
        f"results={response['result_count']}  total={response['timings_ms'].get('total', 0)} ms",
        f"Query (normalized): {query['normalized']}",
    ]
    if query["expansions"]:
        added = ", ".join(f"{e['term']} -> {', '.join(e['added'])}" for e in query["expansions"])
        lines.append(f"Sparse expansions: {added}")
    if query["identifiers"]:
        lines.append(f"Identifiers: {', '.join(query['identifiers'])}")
    applied = {key: value for key, value in response["filters"].items() if value}
    lines.append(f"Filters: {json.dumps(applied) if applied else 'none'}")
    profile = response["profile"]
    lines.append(
        f"Profile: {profile['embedding_profile'][:12]} collection={profile['collection']} "
        f"dense={profile['dense_model']} sparse={profile['sparse_model']} "
        f"reranker={profile['reranker_model'] or '-'}"
    )
    if response["degraded"]:
        lines.append("DEGRADED RESULT")
    if response["warnings"]:
        lines.append(f"Warnings: {', '.join(response['warnings'])}")
    for result in response["results"]:
        citation = result["citation"]
        scores = result["scores"]
        lines.append("")
        lines.append(
            f"[{result['rank']}] {citation['original_file_name']} ({citation['relative_path']}) "
            f"{_location(citation)}  type={result['chunk_type']} "
            f"doc_type={citation['document_type']} addendum={citation['addendum_number']}"
        )
        if citation["section_path"]:
            lines.append(f"    section: {' > '.join(citation['section_path'])}")
        lines.append(
            f"    scores: final={_score(scores['final_score'])} "
            f"rerank={_score(scores['reranker_score'])} rrf={_score(scores['rrf_score'])} "
            f"dense={_score(scores['dense_score'])} (#{scores['dense_rank'] or '-'}) "
            f"sparse={_score(scores['sparse_score'])} (#{scores['sparse_rank'] or '-'}) "
            f"identifier=#{scores['identifier_rank'] or '-'}"
        )
        if result["identifier_match"]["matched"]:
            lines.append(
                f"    exact identifiers: {', '.join(result['identifier_match']['matched'])}"
            )
        text = " ".join(result["text"].split())
        lines.append(f"    text: {text[:500]}{'...' if len(text) > 500 else ''}")
        for extra in result["additional_citations"]:
            lines.append(
                f"    also in: {extra['original_file_name']} ({extra['relative_path']}) "
                f"{_location(extra)}"
            )
    timings = ", ".join(f"{key}={value}" for key, value in response["timings_ms"].items())
    lines.append("")
    lines.append(f"Timings (ms): {timings}")
    lines.append(f"Candidates: {json.dumps(response['candidate_counts'])}")
    return "\n".join(lines)


def _print_error(code: str, message: str) -> None:
    print(json.dumps({"error": code, "message": message}), file=sys.stderr)


def run_extract(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from rfp.agents.errors import ExtractionError, ModelError
    from rfp.agents.runner import execute
    from rfp.agents.schemas import ExtractionConfig

    updates: dict[str, Any] = {"critic_enabled": args.llm_mode == "live"}
    if args.top_k is not None:
        updates["top_k"] = args.top_k
        updates["hard_top_k"] = max(args.top_k, 20)
    if args.retries is not None:
        updates["max_retries"] = args.retries
    if args.concurrency is not None:
        updates["concurrency"] = args.concurrency
    try:
        config = ExtractionConfig.model_validate(updates)
        code, summary = execute(
            bid=args.bid_id,
            output=args.output,
            trace_dir=args.trace_dir,
            qdrant_url=args.qdrant_url,
            llm_mode=args.llm_mode,
            fixture=args.fixture,
            search=None,
            config=config,
        )
    except (ExtractionError, ModelError) as exc:
        _print_error(exc.code, exc.message)
        return 2 if exc.code in {"invalid_bid_id", "invalid_fixture", "invalid_llm_mode"} else 1
    except ValidationError:
        _print_error("invalid_extraction_request", "Invalid extraction settings")
        return 2
    print(json.dumps(summary, ensure_ascii=True))
    return code


def run_search(args: argparse.Namespace) -> int:
    from rfp.schemas.search import RetrievalMode, SearchFilters, SearchRequest
    from rfp.search.wiring import search_service

    settings = local_settings(args.qdrant_url) if args.local else get_settings()
    filters = SearchFilters(
        bid_ids=args.bid_id or [],
        document_ids=args.document_id or [],
        doc_types=args.doc_type or [],
        addendum_numbers=args.addendum_number or [],
        chunk_types=args.chunk_type or [],
    )
    request = SearchRequest(
        query=args.query, top_k=args.top_k, mode=RetrievalMode(args.mode), filters=filters
    )
    response = search_service(settings).search(request).model_dump(mode="json")
    if args.json:
        print(json.dumps(response, ensure_ascii=True))
    else:
        print(format_search(response))
    return 0


def run_index_folder(args: argparse.Namespace) -> int:
    from rfp.search.indexing import load_local_artifacts
    from rfp.search.wiring import local_indexing_service

    settings = local_settings(args.qdrant_url)
    generations, failures = load_local_artifacts(args.path, settings)
    if not generations:
        print(json.dumps({"error": "no_indexable_chunk_artifacts", "input_failures": failures}))
        return 1
    service = local_indexing_service(settings, args.cache_dir)
    report = service.index(generations)
    report.input_failures = failures
    summary = report.as_dict()
    summary["embedding_cache"] = str(args.cache_dir or Path(settings.search.local_cache_dir))
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if report.succeeded else 1


def parse_folder(
    path: Path, output: Path, settings: Settings, bid_identifier: str | None = None
) -> tuple[dict, int]:
    discovery = discover(path, settings, bid_identifier)
    destination = output.resolve()
    if destination == discovery.root or discovery.root in destination.parents:
        raise DiscoveryError("output_must_be_outside_source_folder")
    destination.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "bid_id": str(discovery.bid_id),
        "parsed": 0,
        "partially_parsed": 0,
        "failed": 0,
        "ignored": len(discovery.ignored),
        "warnings": discovery.warnings,
        "ignored_files": discovery.ignored,
    }
    options = ParserOptions.from_settings(settings)
    for source in discovery.files:
        data = read_source(source, settings.ingestion_max_file_bytes)
        result = parse_isolated(
            data, source.relative_path, discovery.bid_id, options, settings.tesseract_cmd
        )
        key = {
            ParseStatus.succeeded: "parsed",
            ParseStatus.partially_succeeded: "partially_parsed",
            ParseStatus.failed: "failed",
        }[result.processing_status]
        summary[key] += 1
        target = destination / f"{result.document_id}.json"
        if target.is_symlink():
            raise DiscoveryError("output_symlink_rejected")
        temporary = destination / f"{result.document_id}.tmp"
        if temporary.exists() or temporary.is_symlink():
            raise DiscoveryError("output_temporary_file_exists")
        try:
            with temporary.open("xb") as stream:
                stream.write(artifact_bytes(result))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    summary_target = destination / "summary.json"
    if summary_target.is_symlink():
        raise DiscoveryError("output_symlink_rejected")
    summary_target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    exit_code = (
        0 if discovery.files and not summary["failed"] and not summary["partially_parsed"] else 1
    )
    return summary, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("parse-folder", "ingest-folder"):
        command = commands.add_parser(name)
        command.add_argument("--path", type=Path, required=True)
        command.add_argument("--bid-id")
        if name == "parse-folder":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--force-ocr", action="store_true")
            command.add_argument("--tesseract-cmd")
    dispatch = commands.add_parser("dispatch-job", help="Requeue a registered nonterminal job")
    dispatch.add_argument("--job-id", type=uuid.UUID, required=True)
    chunk_local = commands.add_parser("chunk-folder", help="Offline parsed JSON to chunk JSON")
    chunk_local.add_argument("--path", type=Path, required=True)
    chunk_local.add_argument("--output", type=Path, required=True)
    chunk_bid = commands.add_parser("chunk-bid", help="Queue incremental chunk preparation from R2")
    chunk_bid.add_argument("--bid-id", type=uuid.UUID, required=True)
    init = commands.add_parser("init-models", help="Download/verify local FastEmbed models")
    init.add_argument("--skip-reranker", action="store_true")
    index_local = commands.add_parser(
        "index-folder", help="Index local Phase 3 chunk JSON into local Qdrant (no R2/DB)"
    )
    index_local.add_argument("--path", type=Path, required=True)
    index_local.add_argument("--qdrant-url", default=LOCAL_QDRANT)
    index_local.add_argument("--cache-dir", type=Path)
    index_bid = commands.add_parser(
        "index-bid", help="Index a bid's active chunk generations (PostgreSQL/R2/Celery)"
    )
    index_bid.add_argument("--bid-id", type=uuid.UUID, required=True)
    index_bid.add_argument(
        "--inline", action="store_true", help="Process in this process instead of the worker"
    )
    search = commands.add_parser("search", help="Hybrid evidence search with citations")
    search.add_argument("--query", required=True)
    search.add_argument("--bid-id", type=uuid.UUID, action="append")
    search.add_argument("--document-id", type=uuid.UUID, action="append")
    search.add_argument(
        "--doc-type",
        action="append",
        choices=["bid_page", "rfp", "addendum", "specs", "affidavit", "other"],
    )
    search.add_argument("--addendum-number", type=int, action="append")
    search.add_argument(
        "--chunk-type", action="append", choices=["prose", "heading", "list", "table_rows", "ocr"]
    )
    search.add_argument(
        "--mode", default="hybrid_rerank", choices=["dense", "sparse", "hybrid", "hybrid_rerank"]
    )
    search.add_argument("--top-k", type=int)
    search.add_argument("--json", action="store_true")
    search.add_argument(
        "--local", action="store_true", help="Ignore .env; use local Qdrant and model cache"
    )
    search.add_argument("--qdrant-url", default=LOCAL_QDRANT)
    extract = commands.add_parser(
        "extract-bid",
        help="Extract the 20 assignment fields for one indexed bid (local Qdrant only)",
    )
    extract.add_argument("--bid-id", required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--trace-dir", type=Path)
    extract.add_argument("--qdrant-url", default=LOCAL_QDRANT)
    extract.add_argument("--llm-mode", choices=["live", "test"], default="live")
    extract.add_argument(
        "--fixture", type=Path, help="JSON list of model responses for --llm-mode test"
    )
    extract.add_argument("--top-k", type=int)
    extract.add_argument("--retries", type=int)
    extract.add_argument("--concurrency", type=int)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    from pydantic import ValidationError

    from rfp.search.errors import InvalidSearchRequest, SearchServiceError

    try:
        if args.command == "init-models":
            from rfp.search.models import init_models

            settings = local_settings()
            result = init_models(settings.search, include_reranker=not args.skip_reranker)
            print(json.dumps(result, ensure_ascii=True))
            return 0
        if args.command == "index-folder":
            return run_index_folder(args)
        if args.command == "search":
            return run_search(args)
        if args.command == "extract-bid":
            return run_extract(args)
    except InvalidSearchRequest as exc:
        _print_error(exc.code, exc.message)
        return 2
    except ValidationError:
        _print_error("invalid_search_request", "Invalid query, filter or top_k")
        return 2
    except SearchServiceError as exc:
        _print_error(exc.code, exc.message)
        return 1
    except Exception:
        _print_error("operation_failed", "Check models, Qdrant, input artifacts and settings")
        return 1
    try:
        if args.command == "chunk-folder":
            from rfp.chunking.local import chunk_folder

            summary, code = chunk_folder(args.path, args.output, local_settings())
            print(json.dumps(summary, ensure_ascii=True))
            return code
        if args.command == "parse-folder":
            settings = local_settings()
            settings.ocr_force = args.force_ocr or settings.ocr_force
            if args.tesseract_cmd:
                settings.tesseract_cmd = args.tesseract_cmd
            summary, code = parse_folder(args.path, args.output, settings, args.bid_id)
            print(json.dumps(summary, ensure_ascii=False))
            return code
        from rfp.db.session import create_db_engine
        from rfp.ingestion.service import register_folder
        from rfp.storage.r2 import CloudflareR2Storage
        from rfp.workers.celery_app import create_celery

        settings = get_settings()
        if not settings.r2_configured:
            print(
                json.dumps(
                    {
                        "error": "r2_not_configured",
                        "message": "Configure R2 endpoint, bucket and S3 keys in private .env",
                    }
                ),
                file=sys.stderr,
            )
            return 1
        engine = create_db_engine(settings)
        try:
            storage = CloudflareR2Storage(settings)
            task_name = "rfp.ingest"
            if args.command == "ingest-folder":
                job_id, summary = register_folder(args.path, settings, engine, storage, args.bid_id)
                print(json.dumps(summary, ensure_ascii=False))
            elif args.command == "chunk-bid":
                from rfp.chunking.service import register_chunks

                job_id, summary = register_chunks(args.bid_id, settings, engine, storage)
                task_name = "rfp.chunk"
                print(json.dumps(summary, ensure_ascii=True))
            elif args.command == "index-bid":
                from rfp.search.jobs import process_index_job, register_index_job
                from rfp.search.wiring import production_indexing_service

                job_id, summary = register_index_job(args.bid_id, settings, engine)
                task_name = "rfp.index"
                print(json.dumps(summary, ensure_ascii=True))
                if args.inline:
                    result = process_index_job(
                        job_id,
                        settings,
                        engine,
                        lambda: production_indexing_service(settings, engine),
                    )
                    print(json.dumps(result, ensure_ascii=True))
                    return 0 if result["state"] == "succeeded" else 1
            else:
                job_id = args.job_id
                from sqlalchemy.orm import Session

                from rfp.db.models import ProcessingJob

                with Session(engine) as session:
                    job = session.get(ProcessingJob, job_id)
                    if job is None:
                        raise ValueError("job_not_found")
                    task_name = {"chunking": "rfp.chunk", "indexing": "rfp.index"}.get(
                        job.kind, "rfp.ingest"
                    )
            # Commit-before-publish: a broker failure leaves a queued job that can be requeued.
            app = create_celery(settings)
            app.tasks[task_name].apply_async(args=[str(job_id)], task_id=str(job_id), retry=False)
            print(json.dumps({"job_id": str(job_id), "dispatch": "queued"}))
            return 0
        finally:
            engine.dispose()
    except DiscoveryError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except SearchServiceError as exc:
        _print_error(exc.code, exc.message)
        return 1
    except TokenizerUnavailable as exc:
        _print_error(str(exc), "Run init-models so the embedding tokenizer is cached locally")
        return 1
    except ValueError as exc:
        if str(exc) in {"no_active_chunk_generations", "job_not_found", "index_job_not_found"}:
            _print_error(str(exc), "Requested bid/job is not available for this operation")
            return 1
        _print_error("operation_failed", "Check input, storage, database and broker availability")
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "error": "operation_failed",
                    "message": "Check input, limits, storage, database and broker availability",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
