"""Repeated PDF page furniture (running headers, footers, page numbers) found before chunking.

Detection reads native PyMuPDF text blocks of one parsed PDF and never changes the parsed
artifact: the chunker skips the selected blocks, so chunk text stays a verbatim subset of the
original page text and citations keep their page numbers.

A block is furniture only when all of these hold:

- it lies in the top or bottom band of the page (``EDGE_FRACTION`` of the document's text extent,
  measured from native block coordinates);
- the same normalized text (case, punctuation and whitespace ignored; digit values too, but only
  in text with a page counter) sits in the same band on at least ``MIN_PAGES`` pages, covering at
  least ``MIN_DENSITY`` of the pages between its first and last occurrence;
- it is short (``MAX_CHARS``) and carries no requirement wording (``SUBSTANTIVE``).

The first occurrence of a repeated text is kept so it stays searchable once; only pure page-number
lines are removed everywhere. Anything uncertain is retained. Removal is idempotent: once repeats
are gone, the kept occurrence no longer meets ``MIN_PAGES``.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from rfp.schemas.parsed import ParsedBlock, ParsedDocument, ParsedPage

EDGE_FRACTION = 0.12
MIN_PAGES = 3
MIN_DENSITY = 0.5
MAX_CHARS = 160
REPORT_CHARS = 120

Zone = Literal["top", "bottom"]
Decision = Literal["removed", "retained"]

SUBSTANTIVE = re.compile(
    r"\b(?:shall|must|required?|requires|requirements?|mandatory|deadline|due|closing"
    r"|warrant(?:y|ies|ed)|submit\w*|submission|deliver\w*|price[sd]?|pricing|costs?|fees?"
    r"|quantit(?:y|ies)|qty|penalt\w*|agree\w*|eligib\w*|no later than|not be accepted)\b"
    r"|\$\s?\d|\b\d{1,2}:\d{2}\s*(?:[ap]\.?m\b)",
    re.IGNORECASE,
)
PAGE_NUMBER = re.compile(r"(?:page )?#(?: (?:of )?#)?")
PAGE_COUNTER = re.compile(r"(?:^|\s)(?:page #|# of #)(?:\s|$)|^#$")


def furniture_key(text: str) -> str:
    """Comparison key: case, punctuation and whitespace are ignored. Digit values are ignored only
    when the text carries a page counter ("Page 3 of 9", "Page 2 | 5", a bare number), so body
    lines that differ only in their numbers never collapse into one key."""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = " ".join(re.sub(r"[^\w]+", " ", value).replace("_", " ").split())
    masked = re.sub(r"\d+", "#", value)
    return masked if PAGE_COUNTER.search(masked) else value


@dataclass
class Occurrence:
    page: int
    block_id: str
    text: str


@dataclass
class Candidate:
    """One repeated text in one band, with every occurrence and the decision for each."""

    key: str
    zone: Zone
    occurrences: list[Occurrence]
    decision: Decision = "retained"
    reason: str = ""
    removed: list[Occurrence] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return sorted({o.page for o in self.occurrences})

    @property
    def removed_pages(self) -> list[int]:
        return sorted({o.page for o in self.removed})

    def as_dict(self) -> dict:
        text = " ".join(self.occurrences[0].text.split())
        return {
            "zone": self.zone,
            "key": self.key[:REPORT_CHARS],
            "text": text if len(text) <= REPORT_CHARS else text[: REPORT_CHARS - 1] + "…",
            "page_count": len(self.pages),
            "pages": self.pages,
            "decision": self.decision,
            "removed_pages": self.removed_pages,
            "reason": self.reason,
        }


def _native(block: ParsedBlock) -> bool:
    return (
        block.block_type == "text"
        and block.layout is not None
        and block.layout.coordinate_system == "pdf_points"
        and bool(block.text.strip())
    )


def _zones(pages: list[ParsedPage]) -> dict[str, Zone]:
    boxes = [b.layout.bbox for page in pages for b in page.blocks if _native(b) and b.layout]
    if not boxes:
        return {}
    top = min(box[1] for box in boxes)
    bottom = max(box[3] for box in boxes)
    band = (bottom - top) * EDGE_FRACTION
    if band <= 0:
        return {}
    zones: dict[str, Zone] = {}
    for page in pages:
        for block in page.blocks:
            if not _native(block) or block.layout is None:
                continue
            _, y0, _, y1 = block.layout.bbox
            if y1 <= top + band:
                zones[block.block_id] = "top"
            elif y0 >= bottom - band:
                zones[block.block_id] = "bottom"
    return zones


def _decide(candidate: Candidate) -> None:
    pages = candidate.pages
    text = " ".join(candidate.occurrences[0].text.split())
    span = pages[-1] - pages[0] + 1
    if len(pages) < MIN_PAGES:
        candidate.reason = f"appears on fewer than {MIN_PAGES} pages"
    elif len(pages) / span < MIN_DENSITY:
        candidate.reason = (
            f"scattered: {len(pages)} of {span} pages between first and last occurrence"
        )
    elif len(text) > MAX_CHARS:
        candidate.reason = f"longer than {MAX_CHARS} characters"
    elif any(SUBSTANTIVE.search(o.text) for o in candidate.occurrences):
        candidate.reason = "contains requirement wording"
    elif PAGE_NUMBER.fullmatch(candidate.key):
        candidate.decision = "removed"
        candidate.removed = list(candidate.occurrences)
        candidate.reason = "page number repeated in the same band"
    else:
        candidate.decision = "removed"
        first = pages[0]
        candidate.removed = [o for o in candidate.occurrences if o.page != first]
        candidate.reason = (
            f"repeated in the {candidate.zone} band on {len(pages)} pages; "
            f"first occurrence (page {first}) kept"
        )


def detect(document: ParsedDocument) -> list[Candidate]:
    """Every text repeated in a page band of a PDF, with its decision. HTML yields nothing."""
    pages = [page for page in document.parsed_pages if page.page_number is not None]
    if len(pages) < MIN_PAGES:
        return []
    zones = _zones(pages)
    groups: dict[tuple[Zone, str], list[Occurrence]] = defaultdict(list)
    for page in pages:
        number = page.page_number or 0
        for block in page.blocks:
            zone = zones.get(block.block_id)
            key = furniture_key(block.text) if zone else ""
            if zone and key:
                groups[(zone, key)].append(Occurrence(number, block.block_id, block.text))
    candidates = []
    for (zone, key), occurrences in sorted(groups.items()):
        if len({o.page for o in occurrences}) < 2:
            continue
        candidate = Candidate(key=key, zone=zone, occurrences=occurrences)
        _decide(candidate)
        candidates.append(candidate)
    return candidates


def removable_blocks(candidates: list[Candidate]) -> set[str]:
    return {o.block_id for c in candidates for o in c.removed}


def strip(document: ParsedDocument, block_ids: set[str]) -> ParsedDocument:
    """A copy without the given blocks; page text is rebuilt from the remaining blocks."""
    pages = []
    for page in document.parsed_pages:
        blocks = [b for b in page.blocks if b.block_id not in block_ids]
        if len(blocks) == len(page.blocks):
            pages.append(page)
            continue
        text = page.text
        for block in page.blocks:
            if block.block_id in block_ids:
                text = text.replace(block.text, "", 1)
        text = re.sub(r"\n{2,}", "\n", text).strip()
        pages.append(page.model_copy(update={"blocks": blocks, "text": text}))
    return document.model_copy(update={"parsed_pages": pages})
