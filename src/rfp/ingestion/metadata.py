import re
import unicodedata
from datetime import date

from rfp.schemas.parsed import DocumentType


def normalize_text(value: str) -> str:
    return "\n".join(
        " ".join(line.split()) for line in unicodedata.normalize("NFC", value).splitlines()
    ).strip()


def classify(
    filename: str, text: str, content_type: str, type_patterns: dict[str, str] | None = None
) -> dict:
    """Conservative, replaceable rules. Only explicitly labeled dates are publication dates."""
    source = f"{filename}\n{text[:100000]}"
    numbers = set(re.findall(r"\baddendum\s*(?:no\.?\s*|#\s*)?(\d+)\b", source, re.I))
    kind = DocumentType.other
    if re.search(r"\baddend(?:um|a)\b", source, re.I):
        kind = DocumentType.addendum
    elif re.search(r"\baffidavit\b", source, re.I):
        kind = DocumentType.affidavit
    elif re.search(r"\b(?:specifications?|specs)\b", filename, re.I):
        kind = DocumentType.specs
    elif re.search(r"\b(?:rfp|request for proposals?)\b", source, re.I):
        kind = DocumentType.rfp
    elif content_type == "text/html" and re.search(
        r"\b(?:bid|solicitation|procurement)\b", source, re.I
    ):
        kind = DocumentType.bid_page
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
