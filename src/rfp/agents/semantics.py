"""Deterministic field-meaning checks.

Support checks prove the value is in the cited text. These rules check that it is
the kind of value the field asks for: a cue must appear in the value itself or in
a window around the value's occurrence in the cited text, and some value shapes
are never acceptable for a field. Problems become rejection reasons, so the
orchestrator's bounded retry decides what happens next.
"""

import re
from dataclasses import dataclass

DATE_RE = re.compile(
    r"\d{1,2}-[A-Za-z]{3}-\d{4}(?:\s+\d{2}:\d{2}:\d{2})?"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s*[AP]M(?:\s*[A-Z]{2,5})?)?"
    r"|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\s*[A-Z]{2,5})?)?"
    r"|\d{1,2}/\d{1,2}/\d{4}",
    re.IGNORECASE,
)
MONTH_YEAR_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+of)?\s+\d{4}\b",
    re.IGNORECASE,
)
STOP = frozenset({"the", "and", "for", "with", "this", "that", "from", "are", "was", "were"})


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


BEFORE = 150
AFTER = 80
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\s?\.\s?[\w-]+)+")
PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")
CHECKBOX_RE = re.compile(r"[\uf06f\uf0a8\u2610\u2611\u2612\u25a1]|check (?:the )?applicable", re.I)
RELATIVE_RE = re.compile(
    r"\b\d{1,3}\s*(?:\(\w+\)\s*)?(?:calendar\s+|business\s+|working\s+)?(?:days?|weeks?|months?)\b"
    r"[^.]{0,40}?\b(?:after|of|from|following|upon)\b",
    re.I,
)


@dataclass(frozen=True)
class FieldRule:
    cue: str = ""
    """Pattern that must occur in the value or near the value in the cited text."""
    value_only: bool = False
    """The cue must be in the value itself, not just nearby."""
    name_words: int = 0
    """With value_only, a value of at most this many words (a bare name) may rely on nearby text."""
    needs: str = ""
    """Pattern the value itself must match."""
    needs_problem: str = ""
    reject: str = ""
    """Pattern the value must not match."""
    reject_problem: str = ""
    conflict: str = ""
    """Label of a different field; reported when it is near the value and the cue is not."""


NOT_IDENTIFIER = rf"^\s*(?:{DATE_RE.pattern}|{PHONE_RE.pattern}|{EMAIL_RE.pattern})\s*$"
DELIVERY_CUE = r"deliver(?!able)|ship|lead ?time|arriv|fulfil|receipt of (?:the )?(?:order|po)"
RULES: dict[str, FieldRule] = {
    "Bid Number": FieldRule(
        needs=r"\d",
        needs_problem="An identifier must contain a digit",
        reject=NOT_IDENTIFIER,
        reject_problem="A date, phone number or email address is not an identifier",
    ),
    "Title": FieldRule(needs=r"[a-z]{3}", needs_problem="A title must contain words"),
    "Due Date": FieldRule(
        cue=r"due|deadline|closing|close|submi|receiv|no later than|opening|accepted until",
    ),
    "Bid Submission Type": FieldRule(
        cue=r"electronic|online|portal|e-?mail|upload|hard ?cop|mail|hand[- ]deliver|courier|"
        r"in person|fax|envelope|system|website|web site|ebid|e-bid|deliver",
        value_only=True,
    ),
    "Term of Bid": FieldRule(
        cue=r"term|renew|extension|period|year|month|through|until",
        needs=r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b",
        needs_problem="A term must state a duration or end date",
    ),
    "Pre Bid Meeting": FieldRule(
        cue=r"pre-?bid|pre-?proposal|pre-?submittal|conference|meeting|walk-?through|site visit",
    ),
    "Installation": FieldRule(cue=r"install|deploy|set-?up|imag|white glove|configur"),
    "Bid Bond Requirement": FieldRule(cue=r"bond|security|guarant|surety"),
    "Delivery Date": FieldRule(cue=DELIVERY_CUE, conflict=r"due date|closing|deadline|submi"),
    "Payment Terms": FieldRule(cue=r"pay|invoice|net ?\d|remit|billing"),
    "Any Additional Documentation Required": FieldRule(
        cue=r"affidavit|form|certific|w-9|attach|exhibit|document|questionnaire|declaration|"
        r"statement|disclosure|insurance|license|reference|signature|acknowledg",
        value_only=True,
    ),
    "MFG for Registration": FieldRule(
        cue=r"manufactur|authori[sz]|reseller|resale|dealer|distribut|oem|factory|partner|"
        r"certified|registered with",
        value_only=True,
        name_words=4,
        reject=CHECKBOX_RE.pattern,
        reject_problem="Value is checkbox or form-field text, not a manufacturer requirement",
    ),
    "Contract or Cooperative to use": FieldRule(
        cue=r"contract|cooperative|co-op|alliance|purchasing|consortium|agreement|piggyback|"
        r"interlocal|gsa|schedule",
    ),
    "Model_no": FieldRule(
        needs=r"\d",
        needs_problem="A model number must contain a digit",
        reject=NOT_IDENTIFIER,
        reject_problem="A date, phone number or email address is not a model number",
    ),
    "Part_no": FieldRule(
        needs=r"\d",
        needs_problem="A part number must contain a digit",
        reject=NOT_IDENTIFIER,
        reject_problem="A date, phone number or email address is not a part number",
    ),
    "Product": FieldRule(needs=r"[a-z]{3}", needs_problem="A product must be named in words"),
    "contact_info": FieldRule(
        cue=r"buyer|contact|procurement|purchasing|officer|poc|question|agent|specialist|"
        r"coordinator|director|manager|analyst|inquir",
        needs=rf"{EMAIL_RE.pattern}|{PHONE_RE.pattern}",
        needs_problem="Contact information needs an email address or phone number",
    ),
    "company_name": FieldRule(
        cue=r"district|isd|office|department|agency|state|county|city|school|university|college|"
        r"authority|board|commission|government|municipal|town|village|bureau|division|"
        r"ministry|corporation|solicit|issued by|on behalf",
        needs=r"[a-z]{3}",
        needs_problem="An organization must be named in words",
        reject=rf"{EMAIL_RE.pattern}|{PHONE_RE.pattern}",
        reject_problem="An organization name cannot be an email address or phone number",
    ),
    "Product Specification": FieldRule(
        cue=r"\d\s?(?:gb|tb|ghz|mhz|mp|wh|hz|nits|mm|cores?|threads?|inch|in\.|\")|"
        r"processor|cpu|ram|memory|storage|ssd|display|screen|battery|warranty|resolution|"
        r"windows|chrome|wi-?fi|bluetooth|usb|port|keyboard|camera|weight",
        value_only=True,
    ),
}


def _anchors(value: str) -> list[str]:
    whole = normalize(value)
    found = [normalize(match) for match in DATE_RE.findall(value)]
    found += [normalize(match.group(0)) for match in EMAIL_RE.finditer(value)]
    found += [normalize(match.group(0)) for match in PHONE_RE.finditer(value)]
    found += [
        token for token in re.findall(r"[a-z0-9][a-z0-9./@+-]{3,}", whole) if token not in STOP
    ]
    return [whole, *dict.fromkeys(found)]


def windows(value: str, texts: list[str]) -> list[str]:
    """Text around each occurrence of the value, or of its distinctive parts."""
    whole, *parts = _anchors(value)
    result: list[str] = []
    for text in texts:
        haystack = normalize(text)
        for anchor in [whole] if whole in haystack else parts:
            start = haystack.find(anchor)
            while start >= 0:
                result.append(haystack[max(0, start - BEFORE) : start + len(anchor) + AFTER])
                start = haystack.find(anchor, start + 1)
    return result


CHRONOLOGY_RE = re.compile(
    r"\b(?:original(?:ly)?|previous(?:ly)?|initial(?:ly)?|revised|extended|postponed|changed|"
    r"moved|amended|superseded)\b",
    re.I,
)
ABSENCE_NOTE_RE = re.compile(
    r"\bnot (?:explicitly )?(?:found|stated|specified|mentioned|provided)\b|\bno evidence\b", re.I
)


def grounded_notes(notes: str, texts: list[str], change_note: str | None = None) -> str:
    """Notes for an accepted value without unsupported claims.

    Sentences that say the value is absent, describe an earlier or revised value
    without an addendum change record, or carry a date missing from the cited
    text are dropped. An addendum change, when recorded, is stated from the record.
    """
    blob = normalize("\n".join(texts))
    kept: list[str] = [change_note] if change_note else []
    for sentence in re.split(r"(?<=[.!?])\s+", notes.strip()):
        if not sentence or ABSENCE_NOTE_RE.search(sentence) or CHRONOLOGY_RE.search(sentence):
            continue
        if any(normalize(date) not in blob for date in DATE_RE.findall(sentence)):
            continue
        kept.append(sentence)
    return " ".join(kept)


def semantic_problems(name: str, value: str, texts: list[str]) -> list[str]:
    rule = RULES.get(name)
    if rule is None:
        return []
    lowered = normalize(value)
    problems: list[str] = []
    if rule.needs and not re.search(rule.needs, lowered, re.I):
        problems.append(rule.needs_problem)
    if rule.reject and re.search(rule.reject, value, re.I):
        problems.append(rule.reject_problem)
    if rule.cue and not re.search(rule.cue, lowered, re.I):
        strict = rule.value_only and len(lowered.split()) > rule.name_words
        nearby = [] if strict else windows(value, texts)
        if not any(re.search(rule.cue, window, re.I) for window in nearby):
            labelled = rule.conflict and any(
                re.search(rule.conflict, window, re.I) for window in nearby
            )
            where = "the value" if strict else "the value or the text around it"
            problem = f"{name} cue is missing from {where}"
            if labelled:
                problem += "; the nearby label belongs to a different field"
            problems.append(problem)
    return problems
