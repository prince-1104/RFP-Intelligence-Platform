import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from rfp.ingestion.discovery import normalize_path
from rfp.ingestion.html import parse_html
from rfp.ingestion.metadata import classify
from rfp.ingestion.options import PARSER_VERSION, ParserOptions
from rfp.ingestion.pdf import parse_pdf
from rfp.schemas.parsed import ParsedDocument, ParseStatus


def document_base(
    data: bytes, relative_path: str, bid_id: uuid.UUID, options: ParserOptions
) -> ParsedDocument:
    relative_path = normalize_path(relative_path)
    digest = hashlib.sha256(data).hexdigest()
    return ParsedDocument(
        document_id=uuid.uuid5(bid_id, f"{relative_path}\n{digest}"),
        bid_id=bid_id,
        original_file_name=relative_path.split("/")[-1],
        relative_path=relative_path,
        source_sha256=digest,
        source_content_type="application/pdf"
        if relative_path.lower().endswith(".pdf")
        else "text/html",
        parser_name="rfp-native-tesseract",
        parser_version=PARSER_VERSION,
        parser_fingerprint=options.fingerprint,
    )


def parse_document(
    data: bytes,
    relative_path: str,
    bid_id: uuid.UUID,
    options: ParserOptions,
    executable: str = "tesseract",
) -> ParsedDocument:
    result = document_base(data, relative_path, bid_id, options)
    if len(data) > options.max_file_bytes:
        result.warnings.append("file_size_limit")
        return result
    if result.source_content_type == "application/pdf":
        result.parsed_pages, result.page_count, result.warnings = parse_pdf(
            data, relative_path, options, executable
        )
    else:
        result.parsed_pages, result.title, result.warnings = parse_html(
            data, relative_path, options
        )
    metadata = classify(
        relative_path,
        "\n".join(p.text for p in result.parsed_pages),
        result.source_content_type,
        options.metadata_type_patterns,
    )
    result.doc_type = metadata["doc_type"]
    result.addendum_number = metadata["addendum_number"]
    result.document_date = metadata["document_date"]
    result.warnings.extend(metadata["warnings"])
    readable = any(p.text.strip() or any(t.rows for t in p.tables) for p in result.parsed_pages)
    incomplete = bool(result.warnings) or any(
        warning != "ocr_table_structure_not_recovered"
        for page in result.parsed_pages
        for warning in page.warnings
    )
    if readable:
        result.processing_status = (
            ParseStatus.partially_succeeded if incomplete else ParseStatus.succeeded
        )
    else:
        result.warnings.append("document_has_no_readable_content")
    return ParsedDocument.model_validate(result.model_dump())


def _child(
    source: str,
    destination: str,
    relative_path: str,
    bid_id: str,
    options_dict: dict,
    executable: str,
) -> None:
    options = ParserOptions.model_validate(options_dict)
    if sys.platform == "linux":
        import resource

        resource.setrlimit(
            resource.RLIMIT_CPU, (options.timeout_seconds + 1, options.timeout_seconds + 2)
        )
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    try:
        result = parse_document(
            Path(source).read_bytes(), relative_path, uuid.UUID(bid_id), options, executable
        )
        Path(destination).write_text(result.model_dump_json(), encoding="utf-8")
    except Exception:
        # A failed child is represented explicitly by the parent; content is never logged.
        raise SystemExit(1) from None


def parse_isolated(
    data: bytes,
    relative_path: str,
    bid_id: uuid.UUID,
    options: ParserOptions,
    executable: str = "tesseract",
) -> ParsedDocument:
    """Production/local CLI boundary: kill hung native parsers, bound memory on Linux."""
    failed = document_base(data, relative_path, bid_id, options)
    if len(data) > options.max_file_bytes:
        failed.warnings.append("file_size_limit")
        return failed
    with tempfile.TemporaryDirectory(prefix="rfp-parse-") as folder:
        source = Path(folder) / "source"
        destination = Path(folder) / "result.json"
        source.write_bytes(data)
        config = Path(folder) / "config.json"
        config.write_text(
            json.dumps(
                {
                    "relative_path": relative_path,
                    "bid_id": str(bid_id),
                    "options": options.model_dump(),
                    "executable": executable,
                }
            ),
            encoding="utf-8",
        )
        # subprocess works inside Celery's daemon prefork processes; multiprocessing does not.
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rfp.ingestion.runner",
                str(source),
                str(destination),
                str(config),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            try:
                process.wait(timeout=options.timeout_seconds)
            except subprocess.TimeoutExpired:
                failed.warnings.append("parser_timeout")
                return failed
            if process.returncode != 0 or not destination.exists():
                failed.warnings.append("parser_process_failed")
                return failed
            if destination.stat().st_size > 256 * 1024 * 1024:
                failed.warnings.append("parsed_artifact_size_limit")
                return failed
            return ParsedDocument.model_validate_json(destination.read_bytes())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def artifact_bytes(document: ParsedDocument) -> bytes:
    return json.dumps(
        document.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
