import re
import unicodedata
from datetime import date

from rfp.schemas.parsed import DocumentType


def normalize_text(value: str) -> str:
    return "\n".join(
        " ".join(line.split()) for line in unicodedata.normalize("NFC", value).splitlines()
    ).strip()


TITLE_WINDOW = 500
TYPE_TERMS = (
    (DocumentType.addendum, r"\baddend(?:um|a)\b"),
    (DocumentType.affidavit, r"\baffidavit\b"),
    (DocumentType.specs, r"\b(?:specifications?|specs)\b"),
    (DocumentType.rfp, r"\b(?:rfp|request for proposals?)\b"),
)
ADDENDUM_NUMBER = r"\baddendum\s*(?:no\.?\s*|#\s*)?(\d+)\b"


def _title_type(head: str) -> DocumentType | None:
    hits = [
        (match.start(), kind)
        for kind, pattern in TYPE_TERMS
        if kind != DocumentType.specs and (match := re.search(pattern, head, re.I))
    ]
    return min(hits, key=lambda hit: hit[0])[1] if hits else None


def classify(
    filename: str, text: str, content_type: str, type_patterns: dict[str, str] | None = None
) -> dict:
    """Conservative, replaceable rules. Only explicitly labeled dates are publication dates.

    Evidence is ranked: filename, then HTML listing pages, then the title area, then the body.
    Passing body mentions of addenda or affidavits do not determine the type.
    """
    source = f"{filename}\n{text[:100000]}"
    name = re.sub(r"[_\-.]+", " ", re.split(r"[\\/]", filename)[-1].rsplit(".", 1)[0])
    head = text[:TITLE_WINDOW]
    kind = next((kind for kind, pattern in TYPE_TERMS if re.search(pattern, name, re.I)), None)
    if (
        kind is None
        and content_type == "text/html"
        and re.search(r"\b(?:bid|solicitation|procurement)\b", source, re.I)
    ):
        kind = DocumentType.bid_page
    if kind is None:
        kind = _title_type(head)
    if kind is None and re.search(TYPE_TERMS[-1][1], source, re.I):
        kind = DocumentType.rfp
    kind = kind or DocumentType.other
    numbers = (
        set(re.findall(ADDENDUM_NUMBER, f"{name}\n{head}", re.I))
        if kind == DocumentType.addendum
        else set()
    )
    overrides = {
        DocumentType(name)
        for name, pattern in (type_patterns or {}).items()
        if re.search(pattern, source, re.I)
    }
    if len(overrides) == 1:
        kind = overrides.pop()
    elif len(overrides) > 1:
        kind = DocumentType.other
    dates: set[date] = set()
    for match in re.finditer(
        r"\b(?:document date|date issued|issued on|published on|publication date)\s*[:\-]?\s*"
        r"(\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2},\s*\d{4})",
        source,
        re.I,
    ):
        value = match.group(1)
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                dates.add(date.fromisoformat(value))
            else:
                from datetime import datetime

                dates.add(datetime.strptime(value, "%B %d, %Y").date())
        except ValueError:
            continue
    warnings = []
    if len(numbers) > 1:
        warnings.append("ambiguous_addendum_number")
    if len(dates) > 1:
        warnings.append("ambiguous_document_date")
    return {
        "doc_type": kind,
        "addendum_number": int(next(iter(numbers))) if len(numbers) == 1 else None,
        "document_date": next(iter(dates)) if len(dates) == 1 else None,
        "warnings": warnings,
    }
