import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from rfp.chunking.furniture import detect as detect_furniture
from rfp.chunking.furniture import removable_blocks
from rfp.chunking.normalize import normalize
from rfp.chunking.tokens import EstimatedTokenCounter, TokenCounter, largest, token_windows
from rfp.core.logging import log_event
from rfp.ingestion.parser import artifact_bytes
from rfp.schemas.chunks import (
    CanonicalChunk,
    ChunkConfig,
    ChunkingResult,
    ChunkOccurrence,
    SourceSegment,
    TableRows,
    sha256,
    stable_json,
)
from rfp.schemas.parsed import ParsedDocument, ParsedPage, ParseStatus, SourceLocator

CONTENT_NAMESPACE = uuid.UUID("451246e0-aea3-4a38-b122-a37645c89fa9")
logger = logging.getLogger("rfp.chunking.engine")
Kind = Literal["prose", "heading", "list", "table_rows", "ocr"]


def generation_id(document_id: uuid.UUID, input_hash: str, config_hash: str) -> uuid.UUID:
    return uuid.uuid5(document_id, f"chunks:{input_hash}:{config_hash}")


@dataclass
class Piece:
    text: str
    segment: SourceSegment
    kind: Kind = "prose"
    section: tuple[str, ...] = ()


def _slice(piece: Piece, start: int, end: int) -> Piece:
    text = piece.text[start:end]
    left = len(text) - len(text.lstrip())
    right = len(text.rstrip())
    return Piece(
        text.strip(),
        piece.segment.model_copy(
            update={
                "normalized_start": piece.segment.normalized_start + start + left,
                "normalized_end": piece.segment.normalized_start + start + right,
            }
        ),
        piece.kind,
        piece.section,
    )


def _flat(text: str) -> str:
    return " ".join(text.split())


def _remove_once(text: str, value: str) -> str:
    words = value.split()
    if not words:
        return text
    pattern = (
        r"(?<![\w./:#$%+\-])"
        + r"\s+".join(re.escape(word) for word in words)
        + r"(?![\w./:#$%+\-])"
    )
    return re.sub(pattern, "\n\n", text, count=1)


def _table_text(headers: list[str] | None, rows: list[list[str | None]]) -> str:
    # JSON cell arrays preserve column boundaries even when a value contains a pipe or tab.
    import json

    lines = ["Headers: " + json.dumps(headers, ensure_ascii=False)] if headers else []
    return "\n".join(lines + [json.dumps(row, ensure_ascii=False) for row in rows])


def _page_pieces(
    page: ParsedPage,
    doc: ParsedDocument,
    config: ChunkConfig,
    deadline: float,
    furniture: frozenset[str] = frozenset(),
) -> tuple[list[Piece], list[str]]:
    pieces = []
    warnings = []
    table_values = [
        _flat(
            " ".join(
                normalize(cell, config) for row in table.rows for cell in row if cell is not None
            )
        )
        for table in page.tables
        if table.rows
    ]
    # Suppress a text copy only if whole adjacent blocks exactly equal an entire native table.
    # Uncertain matches are retained and flagged; repeated ordinary requirements are untouched.
    texts = [normalize(block.text, config) for block in page.blocks]
    flat_blocks = [_flat(value) for value in texts]
    excluded: set[int] = set()
    for value in table_values:
        if not value:
            continue
        matches = []
        for start in range(len(flat_blocks)):
            if time.monotonic() > deadline:
                raise ValueError("chunk_timeout")
            if start in excluded:
                continue
            combined = ""
            for end in range(start, len(flat_blocks)):
                combined = (combined + " " + flat_blocks[end]).strip()
                if not value.startswith(combined):
                    break
                if combined == value:
                    matches.append((start, end))
                    break
        if len(matches) == 1:
            start, end = matches[0]
            excluded.update(range(start, end + 1))
        elif page.blocks:
            warnings.append("table_prose_overlap_uncertain")
    for index, block in enumerate(page.blocks):
        # Furniture text is still subtracted from the page residual below, so it cannot return.
        if index in excluded or not texts[index] or block.block_id in furniture:
            continue
        segment = SourceSegment(
            source_locator=block.source_locator,
            block_id=block.block_id,
            normalized_end=len(texts[index]),
            layout=block.layout,
            ocr_used=block.block_type == "ocr_line" or page.ocr_used,
            ocr_language=page.ocr_language,
            ocr_average_confidence=page.ocr_average_confidence,
            warnings=page.warnings,
        )
        kind: Kind = "prose"
        if block.block_type == "heading":
            kind = "heading"
        elif block.block_type == "list_item":
            kind = "list"
        elif block.block_type == "ocr_line":
            kind = "ocr"
        # Link blocks may duplicate a containing paragraph. Keep them as separate evidence:
        # the original parser does not encode a reliable parent-child relation for all HTML.
        pieces.append(
            Piece(
                texts[index],
                segment,
                kind,
                (block.section_heading,) if block.section_heading else (),
            )
        )
    # Phase 2 HTML retains nav/div text in page.text even when no explicit block represents it.
    residual = normalize(page.text, config)
    for text in texts:
        if time.monotonic() > deadline:
            raise ValueError("chunk_timeout")
        residual = _remove_once(residual, text)
    for value in table_values:
        residual = _remove_once(residual, value)
    residual = normalize(residual, config)
    if residual:
        locator = SourceLocator(relative_path=doc.relative_path, page_number=page.page_number)
        pieces.append(
            Piece(
                residual,
                SourceSegment(
                    source_locator=locator,
                    normalized_end=len(residual),
                    ocr_used=page.ocr_used,
                    ocr_language=page.ocr_language,
                    ocr_average_confidence=page.ocr_average_confidence,
                    warnings=page.warnings,
                ),
                "ocr" if page.ocr_used else "prose",
            )
        )
    return pieces, warnings


def _split(piece: Piece, config: ChunkConfig, counter: TokenCounter) -> list[Piece]:
    if counter.count(piece.text) <= config.max_tokens:
        return [piece]
    # Prefer sentences, then whole words. Overlap between the resulting chunks is added by the
    # caller, so windows inside one long sentence do not overlap here.
    result = []
    for sentence in re.finditer(r".*?(?:[.!?](?=\s+[A-Z0-9(])|$)", piece.text, re.S):
        if not sentence.group().strip():
            continue
        if counter.count(sentence.group()) <= config.target_tokens:
            result.append(_slice(piece, sentence.start(), sentence.end()))
            continue
        spans, _ = token_windows(sentence.group(), config.target_tokens, 0, counter.count)
        result.extend(
            _slice(piece, sentence.start() + start, sentence.start() + end) for start, end in spans
        )
    return result


@dataclass
class RowPart:
    text: str
    column_start: int
    column_end: int
    cell_part: int | None = None
    cell_parts: int | None = None
    warnings: tuple[str, ...] = ()


def _row_parts(
    headers: list[str] | None, row: list[str | None], config: ChunkConfig, counter: TokenCounter
) -> list[RowPart]:
    """Split one over-budget row at cell boundaries; split a single oversized cell at word
    boundaries with overlap. Header/value alignment is kept by slicing both identically."""

    def render(start: int, end: int, cells: list[str | None] | None = None) -> str:
        return _table_text(
            headers[start:end] if headers else None,
            [cells if cells is not None else row[start:end]],
        )

    parts: list[RowPart] = []
    column = 0
    # The predicates are consumed within the same iteration, so capturing ``column`` is safe.
    while column < len(row):
        last = largest(
            column + 1,
            len(row),
            lambda end: counter.count(render(column, end)) <= config.max_tokens,  # noqa: B023
        )
        if last > column:
            parts.append(RowPart(render(column, last), column + 1, last))
            column = last
            continue
        cell = row[column] or ""
        spans, split_word = token_windows(
            cell,
            config.max_tokens,
            config.overlap_tokens,
            lambda value: counter.count(render(column, column + 1, [value])),  # noqa: B023
            counter.count,
        )
        warnings = ("token_budget_word_split",) if split_word else ()
        for number, (start, end) in enumerate(spans, 1):
            parts.append(
                RowPart(
                    render(column, column + 1, [cell[start:end]]),
                    column + 1,
                    column + 1,
                    number,
                    len(spans),
                    warnings,
                )
            )
        column += 1
    return parts


def _tail(pieces: list[Piece], budget: int, counter: TokenCounter) -> list[Piece]:
    if budget <= 0:
        return []
    result: list[Piece] = []
    for piece in reversed(pieces):
        candidate = [piece, *result]
        if counter.count("\n\n".join(part.text for part in candidate)) <= budget:
            result = candidate
            continue
        words = list(re.finditer(r"\S+", piece.text))
        for match in words:
            part = _slice(piece, match.start(), len(piece.text))
            if counter.count("\n\n".join(p.text for p in [part, *result])) <= budget:
                return [part, *result]
        break
    return result


def chunk_document(
    document: ParsedDocument,
    config: ChunkConfig,
    parsed_sha256: str | None = None,
    counter: TokenCounter | None = None,
) -> ChunkingResult:
    document = ParsedDocument.model_validate(document.model_dump())
    if counter is None:
        if config.token_counter != "utf8_bytes_v1":
            raise ValueError("token_counter_required")
        counter = EstimatedTokenCounter()
    if counter.identifier != config.tokenizer_identifier:
        raise ValueError("Token counter identifier does not match effective configuration")
    input_hash = parsed_sha256 or sha256(artifact_bytes(document))
    gid = generation_id(document.document_id, input_hash, config.fingerprint)
    result = ChunkingResult(
        generation_id=gid,
        bid_id=document.bid_id,
        document_id=document.document_id,
        relative_path=document.relative_path,
        source_sha256=document.source_sha256,
        parsed_artifact_sha256=input_hash,
        normalized_content_hash=sha256(b""),
        configuration=config,
        configuration_hash=config.fingerprint,
        processing_status=ParseStatus.failed,
        warnings=list(document.warnings),
    )
    if document.processing_status == ParseStatus.failed:
        result.warnings.append("source_parse_failed")
        return result
    size = sum(
        len(page.text)
        + sum(len(block.text) for block in page.blocks)
        + sum(len(header) for table in page.tables for header in (table.headers or []))
        + sum(len(cell or "") for table in page.tables for row in table.rows for cell in row)
        for page in document.parsed_pages
    )
    if size > config.max_input_chars:
        result.warnings.append("chunk_input_character_limit")
        return result
    deadline = time.monotonic() + config.timeout_seconds
    furniture: frozenset[str] = frozenset()
    if config.page_furniture == "repeated_v1":
        candidates = detect_furniture(document)
        furniture = frozenset(removable_blocks(candidates))
        if furniture:
            result.warnings.append("repeated_page_furniture_removed")
            log_event(
                logger,
                "page_furniture_removed",
                document_id=str(document.document_id),
                blocks=len(furniture),
                candidates=[
                    {
                        "zone": c.zone,
                        "pages": len(c.pages),
                        "removed_pages": c.removed_pages,
                    }
                    for c in candidates
                    if c.removed
                ],
            )
    canonical: dict[str, CanonicalChunk] = {}
    normalized_evidence: list[dict] = []
    output_chars = 0

    def guard() -> None:
        if time.monotonic() > deadline:
            raise ValueError("chunk_timeout")

    def emit(
        pieces: list[Piece], table: TableRows | None = None, extra: tuple[str, ...] = ()
    ) -> None:
        nonlocal output_chars
        guard()
        if len(result.occurrences) >= config.max_chunks:
            raise ValueError("chunk_count_limit")
        value = "\n\n".join(piece.text for piece in pieces).strip()
        if len(value) < config.min_content_chars:
            result.warnings.append("below_minimum_content_threshold")
            return
        output_chars += len(value)
        if output_chars > config.max_output_chars:
            raise ValueError("chunk_output_character_limit")
        digest = sha256(value.encode("utf-8"))
        content = canonical.setdefault(
            digest,
            CanonicalChunk(
                content_id=uuid.uuid5(CONTENT_NAMESPACE, digest), content_hash=digest, text=value
            ),
        )
        segments = [piece.segment for piece in pieces]
        pages = [
            segment.source_locator.page_number
            for segment in segments
            if segment.source_locator.page_number is not None
        ]
        count = counter.count(value)
        warnings = sorted(
            set(
                [
                    *document.warnings,
                    *(w for segment in segments for w in segment.warnings),
                    *(table.warnings if table else []),
                    *extra,
                ]
            )
        )
        if count > config.max_tokens:
            warnings.append("indivisible_row_or_identifier_exceeds_token_budget")
            result.warnings.append("chunk_exceeds_token_budget")
        sequence = len(result.occurrences) + 1
        section = list(pieces[0].section)
        result.occurrences.append(
            ChunkOccurrence(
                occurrence_id=uuid.uuid5(gid, str(sequence)),
                generation_id=gid,
                content_id=content.content_id,
                content_hash=digest,
                bid_id=document.bid_id,
                document_id=document.document_id,
                original_file_name=document.original_file_name,
                relative_path=document.relative_path,
                source_sha256=document.source_sha256,
                source_parser_fingerprint=document.parser_fingerprint,
                source_parsed_sha256=input_hash,
                document_type=document.doc_type,
                addendum_number=document.addendum_number,
                chunk_type=pieces[0].kind,
                normalized_text=value,
                section_heading=section[-1] if section else None,
                section_path=section,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                source_segments=segments,
                table=table,
                sequence=sequence,
                normalization_version=config.normalization_version,
                chunking_version=config.chunking_version,
                configuration_hash=config.fingerprint,
                estimated_token_count=count,
                tokenizer_identifier=counter.identifier,
                warnings=warnings,
            )
        )

    pending: list[Piece] = []
    heading_path: list[str] = []
    current_section: tuple[str, ...] = ()

    def flush() -> None:
        nonlocal pending
        if pending:
            emit(pending)
            pending = []

    try:
        for page in document.parsed_pages:
            guard()
            pieces, warnings = _page_pieces(page, document, config, deadline, furniture)
            result.warnings.extend(warnings)
            for piece in pieces:
                guard()
                if piece.segment.block_id is None and page.blocks:
                    # Residual HTML nav/div content has no reliable section association.
                    # Preserve it independently instead of attaching it to the last heading.
                    flush()
                    piece.section = ()
                    for part in _split(piece, config, counter):
                        emit([part])
                    continue
                if piece.kind == "heading":
                    flush()
                    numbered = re.match(r"^(\d+(?:\.\d+)*)[.)]?\s+", piece.text)
                    depth = len(numbered.group(1).split(".")) if numbered else 1
                    ancestors = heading_path[: depth - 1]
                    if numbered and depth > 1:
                        numbers = numbered.group(1).split(".")
                        parent_numbers = [
                            re.match(r"^(\d+(?:\.\d+)*)[.)]?\s+", title) for title in ancestors
                        ]
                        if len(ancestors) != depth - 1 or any(
                            match is None or match.group(1) != ".".join(numbers[: index + 1])
                            for index, match in enumerate(parent_numbers)
                        ):
                            ancestors = []
                            result.warnings.append("numbered_heading_parent_unavailable")
                    heading_path = ancestors + [piece.text]
                    piece.section = tuple(heading_path)
                    for part in _split(piece, config, counter):
                        emit([part])
                    current_section = piece.section
                    continue
                if piece.section:
                    if not heading_path or heading_path[-1] != piece.section[-1]:
                        heading_path = list(piece.section)
                piece.section = tuple(heading_path)
                if pending and (piece.section != current_section or piece.kind != pending[0].kind):
                    flush()
                current_section = piece.section
                for part in _split(piece, config, counter):
                    if pending and (
                        counter.count("\n\n".join(p.text for p in [*pending, part]))
                        > config.max_tokens
                        or counter.count("\n\n".join(p.text for p in pending))
                        >= config.target_tokens
                    ):
                        old = pending
                        flush()
                        pending = _tail(
                            old,
                            min(
                                config.overlap_tokens,
                                config.max_tokens - counter.count(part.text) - 1,
                            ),
                            counter,
                        )
                    pending.append(part)
            # Table chunks are independent and never share prose overlap.
            if page.tables:
                flush()
            for table in page.tables:
                guard()
                headers = (
                    [normalize(cell, config) for cell in table.headers] if table.headers else None
                )
                rows = [
                    [normalize(cell, config) if cell is not None else None for cell in row]
                    for row in table.rows
                ]
                normalized_evidence.append(
                    {"table_id": table.table_id, "headers": headers, "rows": rows}
                )
                section = (
                    (table.source_locator.heading,)
                    if table.source_locator.heading
                    else tuple(heading_path)
                )

                def emit_rows(
                    text_value: str,
                    first: int,
                    last: int,
                    part: RowPart | None = None,
                    table=table,
                    headers=headers,
                    rows=rows,
                    section=section,
                    page=page,
                ) -> None:
                    emit(
                        [
                            Piece(
                                text_value,
                                SourceSegment(
                                    source_locator=table.source_locator,
                                    normalized_end=len(text_value),
                                    warnings=[*page.warnings, *table.warnings],
                                    ocr_used=page.ocr_used,
                                    ocr_language=page.ocr_language,
                                    ocr_average_confidence=page.ocr_average_confidence,
                                ),
                                "table_rows",
                                section,
                            )
                        ],
                        TableRows(
                            table_id=table.table_id,
                            table_index=table.table_index,
                            row_start=first + 1,
                            row_end=last,
                            headers=headers,
                            rows=rows[first:last],
                            extraction_method=table.extraction_method,
                            warnings=table.warnings,
                            column_start=part.column_start if part else None,
                            column_end=part.column_end if part else None,
                            cell_part=part.cell_part if part else None,
                            cell_parts=part.cell_parts if part else None,
                        ),
                        part.warnings if part else (),
                    )

                start = 0
                while start < len(rows):
                    guard()
                    end = start + 1
                    if counter.count(_table_text(headers, rows[start:end])) > config.max_tokens:
                        result.warnings.append("table_row_split_for_token_budget")
                        for row_part in _row_parts(headers, rows[start], config, counter):
                            emit_rows(row_part.text, start, end, row_part)
                        start = end
                        continue
                    while (
                        end < len(rows)
                        and end - start < config.table_row_limit
                        and counter.count(_table_text(headers, rows[start : end + 1]))
                        <= config.max_tokens
                    ):
                        end += 1
                    emit_rows(_table_text(headers, rows[start:end]), start, end)
                    start = end
            normalized_evidence.extend(
                {"text": piece.text, "section": piece.section, "kind": piece.kind}
                for piece in pieces
            )
        flush()
    except ValueError as exc:
        if str(exc) not in {"chunk_timeout", "chunk_count_limit", "chunk_output_character_limit"}:
            raise
        result.warnings.append(str(exc))
        # Truncated generations must not replace a previously complete generation.
        result.occurrences = []
        canonical = {}
        return result
    result.contents = sorted(canonical.values(), key=lambda content: str(content.content_id))
    result.normalized_content_hash = sha256(stable_json({"evidence": normalized_evidence}))
    if result.occurrences:
        result.processing_status = (
            ParseStatus.partially_succeeded
            if document.processing_status == ParseStatus.partially_succeeded
            or "below_minimum_content_threshold" in result.warnings
            else ParseStatus.succeeded
        )
    elif config.empty_behavior == "skipped":
        result.skipped = True
    else:
        result.warnings.append("no_chunkable_content")
    result.warnings = sorted(set(result.warnings))
    return ChunkingResult.model_validate(result.model_dump())


def chunk_bytes(result: ChunkingResult) -> bytes:
    return stable_json(result.model_dump(mode="json"))
