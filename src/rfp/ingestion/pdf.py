import io
import time

import pdfplumber
import pymupdf

from rfp.ingestion.metadata import normalize_text
from rfp.ingestion.ocr import ocr_page
from rfp.ingestion.options import ParserOptions
from rfp.schemas.parsed import Layout, ParsedBlock, ParsedPage, ParsedTable, SourceLocator


def native_page(page, locator: SourceLocator) -> tuple[str, list[ParsedBlock]]:
    blocks = []
    for index, block in enumerate(page.get_text("blocks", sort=True), 1):
        if block[6] != 0:
            continue
        value = normalize_text(block[4])
        if value:
            blocks.append(
                ParsedBlock(
                    block_id=f"pdf-{locator.page_number}-block-{index}",
                    block_type="text",
                    text=value,
                    source_locator=locator,
                    layout=Layout(bbox=tuple(block[:4])),
                )
            )
    return "\n".join(block.text for block in blocks), blocks


def native_tables(page, locator: SourceLocator) -> list[ParsedTable]:
    tables = []
    for index, found in enumerate(page.find_tables(), 1):
        left, top, right, bottom = (float(value) for value in found.bbox)
        rows = [
            [normalize_text(value) if value is not None else None for value in row]
            for row in found.extract()
        ]
        cells = [
            [_box(cell) if cell is not None else None for cell in row.cells] for row in found.rows
        ]
        source = locator.model_copy(update={"table_index": index})
        tables.append(
            ParsedTable(
                table_id=f"pdf-{locator.page_number}-table-{index}",
                table_index=index,
                headers=None,
                rows=rows,
                source_locator=source,
                extraction_method="pdfplumber",
                warnings=["ambiguous_table_headers_first_row_preserved"],
                layout=Layout(bbox=(left, top, right, bottom)),
                cell_boxes=cells,
            )
        )
    return tables


def _box(cell) -> tuple[float, float, float, float]:
    left, top, right, bottom = (float(value) for value in cell)
    return left, top, right, bottom


def parse_pdf(
    data: bytes, relative_path: str, options: ParserOptions, executable: str
) -> tuple[list[ParsedPage], int, list[str]]:
    pages: list[ParsedPage] = []
    warnings: list[str] = []
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError):
        return [], 0, ["invalid_pdf"]
    with document:
        if document.needs_pass:
            return [], document.page_count, ["password_protected_pdf"]
        count = document.page_count
        if count == 0:
            return [], 0, ["empty_pdf"]
        table_document = None
        try:
            try:
                table_document = pdfplumber.open(io.BytesIO(data))
            except Exception:
                warnings.append("pdf_table_reader_failed")
            deadline = time.monotonic() + options.timeout_seconds
            for index in range(min(count, options.max_pages)):
                if time.monotonic() > deadline:
                    warnings.append("parser_timeout")
                    break
                result = ParsedPage(page_number=index + 1)
                pages.append(result)
                locator = SourceLocator(relative_path=relative_path, page_number=index + 1)
                try:
                    page = document.load_page(index)
                except Exception:
                    result.warnings.append("pdf_page_unreadable")
                    continue
                try:
                    result.text, result.blocks = native_page(page, locator)
                except Exception:
                    result.warnings.append("native_text_extraction_failed")
                if table_document is not None:
                    try:
                        result.tables = native_tables(table_document.pages[index], locator)
                    except Exception:
                        result.warnings.append("native_table_extraction_failed")
                insufficient = (
                    sum(c.isalnum() for c in result.text) < options.ocr_min_text_chars
                    or len(result.text.split()) < options.ocr_min_words
                )
                if options.ocr_force or insufficient:
                    result.ocr_used = True
                    result.ocr_language = options.ocr_language
                    try:
                        text, blocks, confidence = ocr_page(
                            page, options, executable, locator, f"pdf-{index + 1}"
                        )
                        result.ocr_average_confidence = confidence
                        result.blocks.extend(blocks)
                        if text:
                            # Preserve native evidence when OCR is additional/forced.
                            result.text = f"{result.text}\n{text}".strip()
                        else:
                            result.warnings.append("ocr_empty_result")
                        result.warnings.append("ocr_table_structure_not_recovered")
                    except Exception:
                        # Isolated parser boundary: native libraries expose many error types.
                        # Keep other pages and native evidence, never provider exception text.
                        result.warnings.append("ocr_failed_check_executable_language_or_timeout")
                if not result.text and not result.tables:
                    result.warnings.append("page_has_no_readable_content")
            if count > options.max_pages:
                warnings.append("pdf_page_limit")
        finally:
            if table_document is not None:
                table_document.close()
    return pages, count, warnings
