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
    r"\s+\d{1,2},\s+\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s*[AP]M(?:\s*(?-i:[A-Z]{2,5})\b)?)?"
    r"|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\s*(?-i:[A-Z]{2,5})\b)?)?"
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
# A run of two or more digits, or one token that mixes letters and digits.
CODE_TOKEN = r"\d{2,}|\b(?=[a-z-]*\d)(?=[\d-]*[a-z])[a-z\d][a-z\d-]*\b"
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
        omits=r"\b(?:via|through|on|using)\s+(?:microsoft\s+)?(?:teams|zoom|webex|google meet)\b|"
        r"\bvideo ?conferenc|\bvirtual(?:ly)?\b|\bin[- ]person\b|\bconference call\b",
        omits_cue=r"teams|zoom|webex|meet\b|video|virtual|in[- ]person|call|online|room|"
        r"building|street|avenue|address|location|held at",
        omits_problem=(
            "Pre Bid Meeting omits how or where the meeting is held, which the cited passage states"
        ),
        omits_scope="text",
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
        needs=CODE_TOKEN,
        needs_problem=(
            "A model number needs a code such as 5550 or UM-40; a numbered tier or line item "
            "is not a model"
        ),
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


DUE_TIME_PROBLEM = "Due Date omits the time stated with the date in the cited passage"
DUE_ZONE_PROBLEM = "Due Date omits the time zone stated with the date and time in the cited passage"
WARRANTY_PROBLEM = (
    "Product Specification gives one warranty term while the cited passages state different "
    "warranty terms; keep each device's warranty with its device"
)
OMISSIONS = frozenset({DUE_TIME_PROBLEM, DUE_ZONE_PROBLEM, WARRANTY_PROBLEM})


def only_incomplete(reasons: list[str]) -> bool:
    """The value was rejected only because it leaves out a detail stated next to it."""
    omissions = {rule.omits_problem for rule in RULES.values() if rule.omits_problem} | OMISSIONS
    return bool(reasons) and all(reason in omissions for reason in reasons)


ZONE = (
    r"\b(?-i:[ECMP][SD]?T|AK[SD]T|H[SD]T|UTC|GMT)\b|\b(?:eastern|central|mountain|pacific|local)"
    r"(?:\s+(?:standard|daylight|prevailing))?\s+time\b"
)
TIME_AFTER_DATE = re.compile(
    r"^[\s,]{0,3}(?:(?:at|by|@|before|no later than)\s+)?"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?|\d{1,2}\s*[ap]\.?m\.?)"
    rf"(?:\s*\(?\s*(?P<zone>{ZONE}))?",
    re.I,
)
TIME_RE = re.compile(r"\d{1,2}:\d{2}|\b\d{1,2}\s*[ap]\.?m\b", re.I)
ZONE_RE = re.compile(ZONE, re.I)
ZONE_AFTER = re.compile(rf"^\s*\(?\s*({ZONE})", re.I)


CLOCK_START = re.compile(r",?\s+(?:at\s+)?(?=\d{1,2}:\d{2})", re.I)


def _date_key(phrase: str) -> str:
    """The calendar date of a date phrase, without any time or zone."""
    return normalize(CLOCK_START.split(phrase)[0]).strip(", ")


def _stated_times(date: str, texts: list[str]) -> list[tuple[str, str | None]]:
    """(time, zone) printed with this calendar date in the texts."""
    stated: list[tuple[str, str | None]] = []
    for text in texts:
        flat = " ".join(TABLE_CELLS_RE.sub(" ", text).split())
        for match in DATE_RE.finditer(flat):
            phrase = match.group(0)
            if _date_key(phrase) != date:
                continue
            clock = CLOCK_START.search(phrase)
            inline = TIME_AFTER_DATE.match(phrase[clock.start() :]) if clock else None
            after = TIME_AFTER_DATE.match(flat[match.end() :])
            if inline:
                trailing = ZONE_AFTER.match(flat[match.end() :])
                zone = inline.group("zone") or (trailing.group(1) if trailing else None)
                stated.append((inline.group("time"), zone))
            if after:
                stated.append((after.group("time"), after.group("zone")))
    return stated


LABEL_CHARS = 60
QUESTION_LABEL = re.compile(
    r"\b(?:questions?|inquir\w*|clarifications?)\b"
    r"(?![^\d]*\b(?:proposals?|bids?|responses?|offers?|solicitation|submissions?)\b)[^\d]*$",
    re.I,
)


def _labels_before(date: str, texts: list[str]) -> list[str]:
    """The label just before each occurrence of the calendar date, after any sentence break."""
    labels: list[str] = []
    for text in texts:
        flat = " ".join(TABLE_CELLS_RE.sub(" ", text).split())
        for match in DATE_RE.finditer(flat):
            if _date_key(match.group(0)) == date:
                window = flat[max(0, match.start() - LABEL_CHARS) : match.start()]
                labels.append(re.split(r"[.;!?]\s", window)[-1])
    return labels


def due_date_problems(value: str, texts: list[str]) -> list[str]:
    """A deadline keeps the time and zone printed with its date, and adds neither.

    Only a time or zone written immediately after the same calendar date counts; a zone
    is never taken from the locale or from another passage.
    """
    dates = [_date_key(match.group(0)) for match in DATE_RE.finditer(value)]
    if not dates:
        return []
    stated = [item for date in dates for item in _stated_times(date, texts)]
    problems: list[str] = []
    labels = [label for date in dates for label in _labels_before(date, texts)]
    if labels and all(QUESTION_LABEL.search(label) for label in labels):
        problems.append("Due Date is the deadline for vendor questions, not for responses")
    has_time = TIME_RE.search(value) is not None
    zone = ZONE_RE.search(value)
    if stated and not has_time:
        problems.append(DUE_TIME_PROBLEM)
    elif not zone and any(found_zone for _, found_zone in stated):
        problems.append(DUE_ZONE_PROBLEM)
    if zone and not any(
        found_zone and normalize(found_zone) == normalize(zone.group(0)) for _, found_zone in stated
    ):
        problems.append("Due Date states a time zone that is not printed with that date")
    return problems


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n\s*\n")


def _segments(text: str) -> list[str]:
    """Sentences of prose, and whole rows of a table chunk (cells joined by spaces)."""
    segments: list[str] = []
    for block in text.split("\n"):
        stripped = block.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            segments.append(" ".join(TABLE_CELLS_RE.sub(" ", stripped).split()))
    prose = "\n".join(block for block in text.split("\n") if not block.strip().startswith("["))
    for part in SENTENCE_SPLIT.split(prose):
        flat = " ".join(part.split())
        if flat:
            segments.append(flat)
    return segments


def _mentions(item: str, segment: str) -> bool:
    needle = normalize(item)
    haystack = normalize(segment)
    if len(needle) <= 6:
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
    return needle in haystack


MONTH_OF_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+of\s+(\d{4})\b",
    re.I,
)
ORDERING_CUE = r"\bquot(?:e|es|ing|ation)|\border(?:s|ing)?\b|\baward|\bpurchas|\bfund"
MONTH_YEAR = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+\d{4}\b",
    re.I,
)


def delivery_problems(value: str, texts: list[str]) -> list[str]:
    """A delivery date must come from a sentence about delivery, not about quotes or award.
    The sentence is found by the whole value or, for a paraphrased value, by its dates."""
    value = MONTH_OF_YEAR.sub(r"\1 \2", value)
    needles = [value, *MONTH_YEAR.findall(value), *DATE_RE.findall(value)]
    sentences = [
        segment
        for text in texts
        for segment in _segments(MONTH_OF_YEAR.sub(r"\1 \2", join_split_emails(text)))
        if any(_mentions(needle, segment) for needle in needles)
    ]
    if not sentences or any(re.search(DELIVERY_CUE, item, re.I) for item in sentences):
        return []
    if any(re.search(ORDERING_CUE, item, re.I) for item in sentences):
        return [
            "Delivery Date is when quotes, orders or the award are expected, not when goods "
            "must be delivered"
        ]
    return []


REQUIRED_CUE = re.compile(
    r"\b(?:must|shall|required|requires?|mandatory|needs? to|will need to)\b|"
    r"\bshould be (?:submitted|included|provided|attached|completed)\b|"
    r"\bto be (?:submitted|presented|provided|included|attached|completed|returned|signed)\b|"
    r"\battached to (?:the|your|each) (?:submission|response|proposal|bid|offer)\b|"
    r"\bsubmit(?:ted)? (?:it )?with\b|"
    r"\bcompleted,? (?:and )?(?:signed|attached|returned|submitted)",
    re.I,
)
EXAMPLE_CUE = re.compile(r"\b(?:e\.g\.|for example|for instance|such as)\b", re.I)
CONDITION_CUE = re.compile(
    r"^\W*(?:\d+\.\s*)?(?:if|when|where|in the event|should the)\b|\bif (?:applicable|"
    r"required|requested|awarded)\b|\bunless\b|\bonly if\b|\bwhere applicable\b",
    re.I,
)
VALUE_CONDITION = re.compile(r"\b(?:if|when|where|applicable|only|upon request|unless)\b", re.I)
DOCUMENT_NOUN = re.compile(
    r"affidavit|form\b|forms\b|certific|w-?9|attachment|exhibit|document|questionnaire|"
    r"declaration|statement|disclosure|insurance|licen[cs]e|reference|signature|acknowledg|"
    r"letter|plan\b|proof|copy|copies|schedule|worksheet|bond|resume|report|list\b|sheet|"
    r"agreement|addend",
    re.I,
)
"""A listed item must name something submitted, not a product or service requirement."""
ITEM_SPLIT = re.compile(r"\s*;\s*|\s*,\s*(?=(?:and\s+)?[A-Z0-9])|\n+")


def document_problems(value: str, texts: list[str]) -> list[str]:
    """Each listed document must be required by the cited text.

    A document named only in a vendor question, an example, or a form's own wording is
    not a requirement, and a conditional requirement must keep its condition. Items the
    text does not name verbatim are left to the support check.
    """
    segments = [segment for text in texts for segment in _segments(join_split_emails(text))]
    problems: list[str] = []
    for raw in ITEM_SPLIT.split(value):
        item = re.sub(r"^(?:and|or)\s+", "", raw.strip().strip(".:"), flags=re.I)
        if len(re.findall(r"[a-z]", item, re.I)) < 3:
            continue
        if not DOCUMENT_NOUN.search(item):
            problems.append(f"'{item}' is not a document, form or certificate")
            continue
        found = [segment for segment in segments if _mentions(item, segment)]
        if not found:
            continue
        stated = [
            segment
            for segment in found
            if not segment.rstrip().endswith("?") and not EXAMPLE_CUE.search(segment)
        ]
        required = [segment for segment in stated if REQUIRED_CUE.search(segment)]
        unconditional = [segment for segment in required if not CONDITION_CUE.search(segment)]
        if unconditional or (required and VALUE_CONDITION.search(item)):
            continue
        if required:
            problems.append(f"'{item}' is required only under a condition the value omits")
        elif not stated:
            problems.append(f"'{item}' appears only in a question or example, not a requirement")
        else:
            problems.append(f"'{item}' is not stated as a required submission in the cited text")
    return problems


NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
YEARS = r"(\d+|one|two|three|four|five)[\s-]*(?:\(\d+\)[\s-]*)?(?:years?|yrs?)\b"
WARRANTY_TERM = re.compile(
    rf"{YEARS}[^.\n]{{0,40}}?\bwarrant|\bwarrant\w*\b[^.\n]{{0,60}}?\b{YEARS}", re.I
)


def _warranty_terms(text: str) -> set[str]:
    flat = " ".join(TABLE_CELLS_RE.sub(" ", text).split())
    terms = set()
    for match in WARRANTY_TERM.finditer(flat):
        word = (match.group(1) or match.group(2)).casefold()
        terms.add(NUMBER_WORDS.get(word, word))
    return terms


def warranty_problems(value: str, texts: list[str]) -> list[str]:
    stated = set().union(*(_warranty_terms(text) for text in texts)) if texts else set()
    given = _warranty_terms(value)
    if len(stated) >= 2 and len(given) == 1:
        return [WARRANTY_PROBLEM]
    return []


HEADING_NOUN = re.compile(
    r"\b(?:qualifications?|requirements?|registration|certifications?|terms|conditions|"
    r"specifications?|instructions)\s*:?\s*$",
    re.I,
)
HEADING_FIELDS = frozenset(
    {"MFG for Registration", "Installation", "Bid Bond Requirement", "Payment Terms"}
)


def heading_problems(name: str, value: str) -> list[str]:
    """A bare section heading names a topic; the field needs the requirement under it."""
    words = value.split()
    if name not in HEADING_FIELDS or not 2 <= len(words) <= 10 or REQUIRED_CUE.search(value):
        return []
    content = [word for word in words if len(word) > 3 and word[0].isalpha()]
    if HEADING_NOUN.search(value) and all(word[0].isupper() for word in content):
        return [f"{name} value is a section heading; state the requirement written under it"]
    return []


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
    problems.extend(heading_problems(name, value))
    if name == "Due Date":
        problems.extend(due_date_problems(value, texts))
    elif name == "Delivery Date":
        problems.extend(delivery_problems(value, texts))
    elif name == "Any Additional Documentation Required":
        problems.extend(document_problems(value, texts))
    elif name == "Product Specification":
        problems.extend(warranty_problems(value, texts))
    return problems
