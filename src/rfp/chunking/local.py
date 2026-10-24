import os
from pathlib import Path

from rfp.chunking.engine import chunk_bytes, chunk_document
from rfp.core.config import Settings
from rfp.schemas.chunks import sha256, stable_json
from rfp.schemas.parsed import ParsedDocument, ParseStatus


def _write(path: Path, payload: bytes) -> bool:
    if path.is_symlink():
        raise ValueError("output_symlink_rejected")
    if path.exists() and path.read_bytes() == payload:
        return False
    temporary = path.with_suffix(".tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(payload)
        temporary.replace(path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return True


def chunk_folder(path: Path, output: Path, settings: Settings) -> tuple[dict, int]:
    root, destination = path.expanduser().resolve(), output.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("input_directory_not_found")
    if destination == root or root in destination.parents:
        raise ValueError("output_must_be_outside_source_folder")
    destination.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "source_documents": 0,
        "chunks": 0,
        "canonical_contents": 0,
        "reused_content_records": 0,
        "failures": [],
        "skipped_inputs": 0,
        "partial_documents": 0,
        "unchanged_outputs": 0,
        "source_duplicates": {},
    }
    hashes: dict[str, list[str]] = {}
    contents: set[str] = set()
    generations: set[str] = set()
    visited = 0
    total_bytes = 0
    for base, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".")
            and not (Path(base) / name).is_symlink()
            and not (Path(base) / name).is_junction()
        )
        visited += len(directories)
        for name in sorted(names):
            visited += 1
            if visited > settings.ingestion_max_files * 20:
                raise ValueError("directory_entry_limit")
            source = Path(base) / name
            if (
                source.is_symlink()
                or not source.is_file()
                or name.startswith(".")
                or source.suffix.lower() != ".json"
                or name == "summary.json"
            ):
                summary["skipped_inputs"] += 1
                continue
            relative = source.relative_to(root).as_posix()
            summary["source_documents"] += 1
            if summary["source_documents"] > settings.ingestion_max_files:
                raise ValueError("input_file_count_limit")
            try:
                if source.stat().st_size > settings.chunking.max_artifact_bytes:
                    raise ValueError("parsed_artifact_size_limit")
                with source.open("rb") as stream:
                    payload = stream.read(settings.chunking.max_artifact_bytes + 1)
                total_bytes += len(payload)
                if (
                    len(payload) > settings.chunking.max_artifact_bytes
                    or total_bytes > settings.ingestion_max_total_bytes
                ):
                    raise ValueError("input_size_limit")
                parsed = ParsedDocument.model_validate_json(payload)
                result = chunk_document(parsed, settings.chunking, sha256(payload))
                if str(result.generation_id) in generations:
                    summary["skipped_inputs"] += 1
                    continue
                generations.add(str(result.generation_id))
                hashes.setdefault(parsed.source_sha256, []).append(str(parsed.document_id))
                if not _write(destination / f"{result.generation_id}.json", chunk_bytes(result)):
                    summary["unchanged_outputs"] += 1
                summary["chunks"] += len(result.occurrences)
                contents.update(content.content_hash for content in result.contents)
                if result.skipped:
                    summary["skipped_inputs"] += 1
                elif result.processing_status == ParseStatus.failed:
                    summary["failures"].append(
                        {"path": relative, "code": "chunk_generation_failed"}
                    )
                elif result.processing_status == ParseStatus.partially_succeeded:
                    summary["partial_documents"] += 1
            except Exception:
                # Per-input boundary: malformed artifacts do not discard a valid remainder.
                # Exception messages may contain source text, so only safe failure codes escape.
                summary["failures"].append(
                    {"path": relative, "code": "invalid_or_unprocessable_artifact"}
                )
            if total_bytes > settings.ingestion_max_total_bytes:
                break
    summary["canonical_contents"] = len(contents)
    summary["reused_content_records"] = summary["chunks"] - len(contents)
    summary["source_duplicates"] = {
        digest: sorted(set(ids)) for digest, ids in sorted(hashes.items()) if len(set(ids)) > 1
    }
    _write(destination / "summary.json", stable_json(summary))
    code = int(bool(summary["failures"] or summary["partial_documents"] or not generations))
    return summary, code
