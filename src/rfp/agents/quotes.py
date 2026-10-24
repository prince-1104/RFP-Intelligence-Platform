"""Locate a model quote as a contiguous span of one source passage.

Matching tolerates case, whitespace, line breaks, typographic quotes and dashes (tier 1), and
then punctuation between words (tier 2). Words must still appear in the same order with nothing
skipped, so a paraphrase or a quote stitched from separate parts of a passage does not match.
The returned excerpt is the source text, never the model's wording.
"""

import re

CHAR_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u00ad": "",
    }
)
ELLIPSIS = re.compile(r"\.\.\.|\u2026")
WORD = re.compile(r"[0-9a-z]+")
MAX_QUOTE_CHARS = 600
# Some indexed table cells carry a literal backslash-n where the PDF had a line break.
ESCAPED_BREAKS = frozenset("nrt")
ESCAPED_BREAK = re.compile(r"\\[nrt]")


def _canonical(text: str) -> tuple[str, list[int]]:
    """Casefolded, whitespace-collapsed text and the source index of every output character."""
    chars: list[str] = []
    index: list[int] = []
    pending_space = False
    skip = False
    for position, raw in enumerate(text):
        if skip:
            skip = False
            continue
        char = raw.translate(CHAR_MAP)
        if not char:
            continue
        if char == "\\" and text[position + 1 : position + 2] in ESCAPED_BREAKS:
            skip = True
            char = " "
        if char.isspace():
            pending_space = bool(chars)
            continue
        if pending_space:
            chars.append(" ")
            index.append(position)
            pending_space = False
        for folded in char.casefold():
            chars.append(folded)
            index.append(position)
    return "".join(chars), index


def _strip(quote: str) -> str:
    """Remove wrapping quotation marks and leading or trailing ellipses only."""
    value = quote.strip()
    while True:
        trimmed = value.strip("\"'\u201c\u201d\u2018\u2019 ")
        for mark in ("...", "\u2026"):
            trimmed = trimmed.removeprefix(mark).removesuffix(mark).strip()
        if trimmed == value:
            return value
        value = trimmed


def locate(quote: str, passage: str) -> tuple[int, int] | None:
    """Source span (start, end) of the quote inside the passage, or None."""
    needle = _strip(quote)
    if not needle or len(needle) > MAX_QUOTE_CHARS or ELLIPSIS.search(needle):
        return None
    hay, hay_index = _canonical(passage)
    canon, _ = _canonical(needle)
    if not canon:
        return None
    found = hay.find(canon)
    if found >= 0:
        return hay_index[found], hay_index[found + len(canon) - 1] + 1
    words = [m for m in WORD.finditer(canon)]
    if not words:
        return None
    source_words = list(WORD.finditer(hay))
    target = [m.group(0) for m in words]
    tokens = [m.group(0) for m in source_words]
    for start in range(len(tokens) - len(target) + 1):
        if tokens[start : start + len(target)] == target:
            first = source_words[start]
            last = source_words[start + len(target) - 1]
            return hay_index[first.start()], hay_index[last.end() - 1] + 1
    return None


def verbatim(quote: str, passage: str) -> str | None:
    """The matched source text with whitespace collapsed, or None when the quote is not found."""
    span = locate(quote, passage)
    if span is None:
        return None
    return " ".join(ESCAPED_BREAK.sub(" ", passage[span[0] : span[1]]).split())
