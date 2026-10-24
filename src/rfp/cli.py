"""Offline parsing and operator-only production ingestion (no HTTP filesystem access)."""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from rfp.core.config import Settings, get_settings
from rfp.ingestion.discovery import DiscoveryError, discover, read_source
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import artifact_bytes, parse_isolated
from rfp.schemas.parsed import ParseStatus


def local_settings() -> Settings:
    # Deliberately independent of .env and external-service credentials.
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": SecretStr("postgresql+psycopg://local@localhost/local"),
        "redis_url": SecretStr("redis://localhost:6379/0"),
        "qdrant_url": "http://localhost:6333",
        "qdrant_api_key": SecretStr(""),
    }
    return Settings(**values)


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
    args = parser.parse_args(argv)
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
            else:
                job_id = args.job_id
                from sqlalchemy.orm import Session

                from rfp.db.models import ProcessingJob

                with Session(engine) as session:
                    job = session.get(ProcessingJob, job_id)
                    if job is None:
                        raise ValueError("job_not_found")
                    task_name = "rfp.chunk" if job.kind == "chunking" else "rfp.ingest"
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
