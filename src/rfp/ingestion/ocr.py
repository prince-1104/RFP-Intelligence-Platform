import math
import threading
from collections import defaultdict
from contextlib import closing

import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output

from rfp.ingestion.metadata import normalize_text
from rfp.ingestion.options import ParserOptions
from rfp.schemas.parsed import Layout, ParsedBlock, SourceLocator

_tesseract_lock = threading.Lock()


def ocr_page(
    page, options: ParserOptions, executable: str, locator: SourceLocator, prefix: str
) -> tuple[str, list[ParsedBlock], float | None]:
    """Tesseract scores describe word recognition, not business-fact correctness."""
    import pymupdf

    width, height = page.rect.width, page.rect.height
    if width <= 0 or height <= 0:
        raise ValueError("invalid_page_dimensions")
    scale = min(options.ocr_dpi / 72, math.sqrt(options.ocr_max_image_pixels / (width * height)))
    # Rounding during rendering must not exceed the configured pixel budget.
    while math.ceil(width * scale) * math.ceil(height * scale) > options.ocr_max_image_pixels:
        scale *= 0.99
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False
    )
    with closing(Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)) as image:
        del pixmap
        with closing(ImageOps.autocontrast(image)) as prepared:
            with _tesseract_lock:
                previous = pytesseract.pytesseract.tesseract_cmd
                try:
                    pytesseract.pytesseract.tesseract_cmd = executable
                    data = pytesseract.image_to_data(
                        prepared,
                        lang=options.ocr_language,
                        output_type=Output.DICT,
                        config="--psm 3",
                        timeout=options.ocr_timeout_seconds,
                    )
                finally:
                    pytesseract.pytesseract.tesseract_cmd = previous
    lines: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    scores = []
    for index, word in enumerate(data["text"]):
        if not word.strip():
            continue
        lines[(data["block_num"][index], data["par_num"][index], data["line_num"][index])].append(
            index
        )
        confidence = float(data["conf"][index])
        if 0 <= confidence <= 100:
            scores.append(confidence)
    blocks = []
    for number, indexes in enumerate(lines.values(), 1):
        indexes.sort(key=lambda index: data["left"][index])
        text = normalize_text(" ".join(data["text"][index] for index in indexes))
        left = min(data["left"][i] for i in indexes) / scale
        top = min(data["top"][i] for i in indexes) / scale
        right = max(data["left"][i] + data["width"][i] for i in indexes) / scale
        bottom = max(data["top"][i] + data["height"][i] for i in indexes) / scale
        blocks.append(
            ParsedBlock(
                block_id=f"{prefix}-ocr-{number}",
                block_type="ocr_line",
                text=text,
                source_locator=locator,
                layout=Layout(bbox=(left, top, right, bottom)),
            )
        )
    return (
        "\n".join(block.text for block in blocks),
        blocks,
        (round(sum(scores) / len(scores), 2) if scores else None),
    )
