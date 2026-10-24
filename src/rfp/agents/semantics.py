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
    omits: str = ""
    """Detail the cited text states; the value must then carry `omits_cue`."""
    omits_cue: str = ""
    omits_problem: str = ""
    omits_scope: str = "window"
    """Where `omits` is looked for: "window" around the value, or the whole cited "text"."""


NOT_IDENTIFIER = rf"^\s*(?:{DATE_RE.pattern}|{PHONE_RE.pattern}|{EMAIL_RE.pattern})\s*$"
DOCUMENT_HEADING = (
    r"^\s*(?:purchase order\s+)?(?:request for (?:proposals?|quotes?|quotations?|qualifications|"
    r"information|bids?)|invitation (?:for|to) bids?|bid (?:form|document))"
    r"(?:\s*\([a-z]+\))?(?:\s+(?:hardware|software|master|contract|form|document|section|general|"
    r"information|solicitation|services?|goods|no\.?|number|\d+|[-:#]))*\s*$"
)
DELIVERY_CUE = r"deliver(?!able)|ship|lead ?time|arriv|fulfil|receipt of (?:the )?(?:order|po)"
RULES: dict[str, FieldRule] = {
    "Bid Number": FieldRule(
        needs=r"\d",
        needs_problem="An identifier must contain a digit",
        reject=NOT_IDENTIFIER,
        reject_problem="A date, phone number or email address is not an identifier",
    ),
    "Title": FieldRule(
        needs=r"[a-z]{3}",
        needs_problem="A title must contain words",
        reject=DOCUMENT_HEADING,
        reject_problem=(
            "A document-type heading is not the solicitation title; use the passage that "
            "names what is being bought, such as a listing or cover title"
        ),
    ),
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
        omits=r"renew|extension|extend",
        omits_cue=r"renew|extension|extend|option|successive|additional",
        omits_problem="Term of Bid omits the renewal or extension options stated next to it",
    ),
    "Pre Bid Meeting": FieldRule(
        cue=r"pre-?bid|pre-?proposal|pre-?submittal|conference|meeting|walk-?through|site visit",
    ),
    "Installation": FieldRule(cue=r"install|deploy|set-?up|imag|white glove|configur"),
    "Bid Bond Requirement": FieldRule(cue=r"bond|security|guarant|surety"),
    "Delivery Date": FieldRule(cue=DELIVERY_CUE, conflict=r"due date|closing|deadline|submi"),
    "Payment Terms": FieldRule(
        cue=r"pay|invoice|net ?\d|remit|billing",
        reject=r"\b(?:prime\s+)?(?:vendors?|contractors?|suppliers?)\b[^.]{0,60}\bpay\b[^.]{0,120}"
        r"\bsub-?contractors?\b",
        reject_problem=(
            "Value is the vendor's duty to pay its subcontractors, not how the buyer pays "
            "the vendor"
        ),
    ),
    "Any Additional Documentation Required": FieldRule(
        cue=r"affidavit|form|certific|w-9|attach|exhibit|document|questionnaire|declaration|"
        r"statement|disclosure|insurance|license|reference|signature|acknowledg",
        value_only=True,
        reject=CHECKBOX_RE.pattern,
        reject_problem="Value is a checkbox instruction on a form, not a required document",
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
    "Product": FieldRule(
        needs=r"[a-z]{3}",
        needs_problem="A product must be named in words",
        omits=r"\bqty\b|quantit",
        omits_cue=r"(?<![\w-])\d[\d,]*(?![\w-])|\b(?:one|two|three|four|five|six|seven|eight|"
        r"nine|ten|dozen)\b",
        omits_problem=(
            "Product omits the quantity stated in the cited passage; name the items with "
            "their quantities"
        ),
        omits_scope="text",
    ),
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


SPLIT_EMAIL_RE = re.compile(
    r"([\w.+-]+@[\w-]+(?:\.[\w-]+)*)"
    r"(?:\s+\.([a-z]{2,6})|\.\s+([a-z]{2,6}(?:\.[a-z]{2,6})+))(?![\w@])"
)


def join_split_emails(text: str) -> str:
    """Rejoin an email address that PDF extraction split at a dot ("md .us", "state. md.us").

    Only a break before a dot, or after a dot when the rest is itself a dotted lowercase
    domain, is joined, so an address that ends a sentence is never merged with the next word.
    """
    return SPLIT_EMAIL_RE.sub(
        lambda match: f"{match.group(1)}.{match.group(2) or match.group(3)}", text
    )


ID_PATTERN = r"(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{4,}"
IDENTIFIER_RE = re.compile(
    r"\b(?P<label>solicitation|bid|rfp|rfq|ifb|itb|porfp|project|sourcing|reference)"
    r"\s*(?:number|no\.?|#)\s*:?\s*(?:(?:[A-Za-z]+\s+){1,3}#|#)?\s*"
    rf"(?P<id>{ID_PATTERN})",
    re.I,
)
SOLICITATION_LABELS = frozenset({"solicitation", "bid", "rfp", "rfq", "ifb", "itb"})
LISTING_LABELS = frozenset({"reference"})
TABLE_CELLS_RE = re.compile(r'\\n|"\s*,\s*"|[\[\]"]|\bnull\b')


def labelled_identifiers(text: str) -> list[tuple[str, str, str]]:
    """(label, identifier, matched text) for each identifier printed after a number label.

    Table chunks are stored as rows of quoted cells, so a label and its value can sit in
    neighbouring cells; the cell punctuation is removed before matching.
    """
    flat = " ".join(TABLE_CELLS_RE.sub(" ", text).split())
    return [
        (match.group("label").casefold(), match.group("id"), match.group(0))
        for match in IDENTIFIER_RE.finditer(flat)
    ]


def _same_identifier(identifier: str, value: str) -> bool:
    pattern = rf"(?<![\w-]){re.escape(identifier.casefold())}(?![\w-])"
    return re.search(pattern, value.casefold()) is not None


def _identifier_tokens(text: str) -> set[str]:
    tokens = set(re.findall(rf"(?<![\w-]){ID_PATTERN}(?![\w-])", text))
    return {
        token
        for token in tokens
        if not PHONE_RE.fullmatch(token) and not (token.isdigit() and len(token) < 6)
    }


def bid_number_problems(
    value: str, evidence_texts: list[str], listing_texts: list[str] | None = None
) -> list[str]:
    """Bid Number is the number the solicitation is listed and filed under.

    1. A listing site's reference number is never the solicitation number.
    2. When the evidence labels a solicitation (bid, RFP) number, the value must be one.
    3. Otherwise, when a listing passage and a solicitation document share identifiers,
       the value must be one of them; a number printed only on a form or only on the
       listing is not. Other document identifiers are kept as cited aliases in the notes.
    """
    found = [item for text in evidence_texts for item in labelled_identifiers(text)]
    labels = {label for label, identifier, _ in found if _same_identifier(identifier, value)}
    if labels and labels <= LISTING_LABELS:
        return ["Bid Number is a listing reference number, not the solicitation number"]
    solicitation = sorted(
        {identifier for label, identifier, _ in found if label in SOLICITATION_LABELS}
    )
    if solicitation:
        if any(_same_identifier(item, value) for item in solicitation):
            return []
        return ["The evidence labels " + " or ".join(solicitation) + " as the solicitation number"]
    listing_texts = listing_texts or []
    listing = {token for text in listing_texts for token in _identifier_tokens(text)}
    documents = "\n".join(text for text in evidence_texts if text not in listing_texts)
    shared = sorted(token for token in listing if _same_identifier(token, documents))
    if shared and not any(_same_identifier(token, value) for token in shared):
        return [
            "The listing and the solicitation documents share "
            + " or ".join(shared)
            + "; use the number the solicitation is listed under"
        ]
    return []


def identifier_aliases(value: str, texts: list[str]) -> list[tuple[int, str]]:
    """(text index, labelled text) for other identifiers of the solicitation.

    Listing reference numbers are excluded.
    """
    aliases: list[tuple[int, str]] = []
    seen = {value.casefold()}
    for index, text in enumerate(texts):
        for label, identifier, matched in labelled_identifiers(text):
            if label in LISTING_LABELS or _same_identifier(identifier, value):
                continue
            if identifier.casefold() in seen:
                continue
            seen.add(identifier.casefold())
            aliases.append((index, matched))
    return aliases


def only_incomplete(reasons: list[str]) -> bool:
    """The value was rejected only because it leaves out a detail stated next to it."""
    omissions = {rule.omits_problem for rule in RULES.values() if rule.omits_problem}
    return bool(reasons) and all(reason in omissions for reason in reasons)


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
        haystack = normalize(join_split_emails(text))
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
    if rule.omits and not re.search(rule.omits_cue, lowered, re.I):
        scope = [normalize(text) for text in texts] if rule.omits_scope == "text" else None
        if any(re.search(rule.omits, part, re.I) for part in scope or windows(value, texts)):
            problems.append(rule.omits_problem)
    return problems
