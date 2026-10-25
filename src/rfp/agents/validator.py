"""Deterministic checks against retrieved evidence. Model confidence is not evidence."""

import re

from rfp.agents.schemas import DraftField, EvidenceHit, FieldValidation, SourceRef
from rfp.agents.semantics import (
    DATE_RE,
    MONTH_YEAR_RE,
    RELATIVE_RE,
    STOP,
    TABLE_CELLS_RE,
    bid_number_problems,
    join_split_emails,
    normalize,
    semantic_problems,
)
from rfp.schemas.parsed import DocumentType

DATE_FIELDS = {"Due Date", "Pre Bid Meeting", "Delivery Date"}
RELATIVE_DATE_FIELDS = {"Delivery Date"}
IDENTIFIER_FIELDS = {"Bid Number", "Model_no", "Part_no"}
SCHEDULE_RE = re.compile(
    r"\bschedul\w*\b[^.]{0,40}\bdeliver|\bdeliver\w*\b[^.]{0,40}\bschedul", re.I
)
"""A delivery schedule the vendor must follow, stated without a calendar date."""
ABSENCE = ("not found", "no evidence", "not stated", "not in the documents", "no requirement")


def contained(value: str, text: str) -> bool:
    needle = normalize(value)
    haystack = normalize(text)
    if not needle:
        return False
    if len(needle) <= 6:
        return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None
    return needle in haystack


def whole_identifier(value: str, text: str) -> bool:
    """The value occurs with no letter, digit or hyphen joined to either end, so a
    truncated code such as 210-BLY does not match 210-BLYZ."""
    needle = normalize(value)
    haystack = normalize(TABLE_CELLS_RE.sub(" ", text))
    return bool(needle) and (
        re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", haystack) is not None
    )


def date_phrases(text: str) -> list[str]:
    return [match.group(0).strip() for match in DATE_RE.finditer(text)]


def value_tokens(value: str) -> list[str]:
    """Distinctive tokens of a value that its cited text must contain."""
    return [
        token.rstrip(".,;:")
        for token in re.findall(r"[a-z0-9][a-z0-9./+-]{2,}", normalize(value))
        if token.rstrip(".,;:") not in STOP
    ]


def _support(value: str, texts: list[str]) -> bool:
    blob = join_split_emails("\n".join(texts))
    if contained(value, blob):
        return True
    tokens = value_tokens(value)
    if not tokens:
        return False
    haystack = normalize(blob)
    return all(token in haystack for token in tokens)


PARAPHRASE_COVERAGE = 0.8
PARAPHRASE_FIELDS = frozenset(
    {
        "Term of Bid",
        "Bid Submission Type",
        "Installation",
        "Bid Bond Requirement",
        "Payment Terms",
        "Any Additional Documentation Required",
        "MFG for Registration",
        "Contract or Cooperative to use",
        "Product",
        "Bid Summary",
    }
)
NUMBER_RE = re.compile(r"\d+(?:[.,/:-]\d+)*")
PARAPHRASE_GLUE = frozenset(
    {"each", "total", "plus", "also", "including", "option", "options", "optional", "then"}
)
"""Connective words a restatement adds; they carry no fact that the source must state."""


def _paraphrase_support(
    value: str, texts: list[str], coverage: float = PARAPHRASE_COVERAGE
) -> bool:
    """A faithful restatement: every number is in the cited text, and most content words
    (compared by their first six letters, so "renewals" matches "renewal") are too.

    Used only for descriptive fields. Identifiers, dates, names, contacts, titles and
    specifications must still be copied.
    """
    haystack = normalize("\n".join(texts))
    for number in NUMBER_RE.findall(value):
        if re.search(rf"(?<![\d.]){re.escape(number)}(?!\d)", haystack) is None:
            return False
    tokens = [
        token.strip(".,;:()")
        for token in re.findall(r"[a-z][a-z'-]{3,}", normalize(value))
        if token not in STOP and token.strip(".,;:()") not in PARAPHRASE_GLUE
    ]
    if not tokens:
        return False
    present = sum(1 for token in tokens if token[:6] in haystack)
    return present / len(tokens) >= coverage


def support_kind(name: str, value: str, texts: list[str]) -> str:
    """How the cited texts support an accepted value: verbatim, tokens, paraphrase or none."""
    if any(contained(value, join_split_emails(text)) for text in texts):
        return "verbatim"
    if texts and _support(value, texts):
        return "tokens"
    if texts and name in PARAPHRASE_FIELDS and _paraphrase_support(value, texts):
        return "paraphrase"
    return "none"


COPIED_SUMMARY_WORDS = 25
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
SEGMENT_SPLIT = re.compile(r"\n\s*\n|\n(?=\s*(?:\d{1,2}[.)]|\[)\s)|\n(?=\[)")
RELATION_CUE = re.compile(
    r"\b(?:including|includes?|such as|along with|together with|accompanied by|containing|"
    r"consisting of|comprising)\b",
    re.I,
)
"""Words that make one thing part of another; the source must state that link."""
CLAUSE_BREAK = re.compile(r";|,\s+(?:and|but|while|whereas)\s+|\.\s")
ITEM_SPLIT = re.compile(r",\s+(?:and\s+|or\s+)?|\s+(?:and|or|as well as)\s+")
OBLIGATION = re.compile(
    r"\b(?:must|shall|should|required|requires?|due|needs? to|is to|are to|to be)\b", re.I
)
"""A clause that imposes a requirement or deadline. Only there does "X including Y" assert
that Y belongs to the requirement; "proposals for devices, including laptops" describes."""
CLAIM_GLUE = frozenset(
    {
        "must",
        "shall",
        "should",
        "will",
        "also",
        "which",
        "their",
        "they",
        "have",
        "been",
        "being",
        "within",
        "into",
        "upon",
        "under",
        "after",
        "before",
        "necessary",
        "required",
        "requires",
        "require",
        "documentation",
        "documents",
        "items",
        "following",
        "other",
        "additional",
        "additionally",
        "various",
        "several",
        "multiple",
        "different",
        "submit",
        "submitted",
        "provide",
        "provided",
        "present",
        "presented",
        "deliver",
        "days",
        "weeks",
        "months",
        "years",
        "hours",
    }
)
VERB_CUT = re.compile(r"\s(?:is|are|must|shall|will|should|may|can|be|to be)\b.*$", re.I)
SENTENCE_COVERAGE = 0.5
"""A summary sentence restates; it needs its numbers and at least half its content words
in the cited passages. The whole summary is held to PARAPHRASE_COVERAGE."""
"""Words too generic to anchor what a claim is about."""


def _claim_words(text: str) -> list[str]:
    return [
        token[:6]
        for token in (t.strip(".,;:()'") for t in re.findall(r"[a-z][a-z'-]{3,}", normalize(text)))
        if token not in STOP and token not in PARAPHRASE_GLUE and token not in CLAIM_GLUE
    ]


def _segments(texts: list[str]) -> list[str]:
    """Source passages split into the units that state one requirement: paragraphs,
    numbered items with their bullets, and table rows."""
    pieces: list[str] = []
    for text in texts:
        flat = text.replace("\\n", "\n")
        pieces.extend(normalize(piece) for piece in SEGMENT_SPLIT.split(flat) if piece.strip())
    return pieces


def _relation_problems(sentence: str, segments: list[str]) -> list[str]:
    problems: list[str] = []
    for cue in RELATION_CUE.finditer(sentence):
        head = CLAUSE_BREAK.split(sentence[: cue.start()])[-1]
        if not OBLIGATION.search(head):
            continue
        words_before = _claim_words(head)
        anchors = {words_before[0], words_before[-1]} if words_before else set()
        tail = sentence[cue.end() :]
        later = RELATION_CUE.search(tail)
        if later:
            continue
        tail = VERB_CUT.sub("", CLAUSE_BREAK.split(tail)[0])
        if not anchors:
            continue
        for item in ITEM_SPLIT.split(tail):
            words = [word for word in _claim_words(item) if word not in anchors]
            if not words:
                continue
            linked = any(
                all(word in segment for word in words) and any(a in segment for a in anchors)
                for segment in segments
            )
            if not linked:
                problems.append(
                    f"Summary links '{item.strip(' .')}' to '{head.strip(' ,')}', but no cited "
                    "passage states them together"
                )
    return problems


def summary_claim_problems(value: str, texts: list[str]) -> list[str]:
    """Each sentence of a summary is a claim: it must be supported by the cited passages on
    its own, and a link it draws between two things (X including Y) must be stated within
    one passage segment. Facts that are each supported can still be joined wrongly.

    The link is checked lexically and only when the clause before the cue imposes a
    requirement or deadline: an item after the cue must share a segment with the clause's
    subject or the last noun before the cue. Links not marked by a cue word, and descriptive
    lists of what is procured, are not checked."""
    problems: list[str] = []
    segments = _segments(texts)
    for sentence in SENTENCE_SPLIT.split(value.strip()):
        if len(sentence.split()) < 3:
            continue
        if not _support(sentence, texts) and not _paraphrase_support(
            sentence, texts, SENTENCE_COVERAGE
        ):
            problems.append(
                f"Summary claim '{sentence.strip()}' is not supported by the cited evidence"
            )
            continue
        problems.extend(_relation_problems(sentence, segments))
    return problems


def locator_problem(source: SourceRef) -> str | None:
    pdf = source.relative_path.lower().endswith(".pdf")
    if source.bid_id is None:
        return "citation is missing a bid"
    if pdf and source.page is None:
        return "PDF citation is missing a one-based page"
    if not pdf and source.page is not None:
        return "HTML citation invented a page number"
    if not pdf and not source.html_element_id:
        return "HTML citation is missing a locator"
    return None


def _absence_ok(notes: str, evidence_text: str) -> bool:
    lowered = notes.casefold()
    if not any(phrase in lowered for phrase in ABSENCE):
        return False
    for phrase in date_phrases(notes):
        if not contained(phrase, evidence_text):
            return False
    return True


def validate_field(
    draft: DraftField,
    evidence: list[EvidenceHit],
    *,
    search_errors: list[str] | None = None,
) -> FieldValidation:
    by_id = {hit.evidence_id: hit for hit in evidence}
    reasons: list[str] = []
    if search_errors and draft.value is None and not draft.evidence_ids:
        return FieldValidation(
            name=draft.name,
            passed=False,
            status="rejected",
            reasons=[f"Retrieval failed: {search_errors[-1]}"],
            support="unsupported",
        )
    if draft.value is None:
        notes = draft.notes.strip() or "Not found in documents"
        blob = "\n".join(hit.text for hit in evidence)
        if _absence_ok(notes, blob):
            return FieldValidation(
                name=draft.name, passed=True, status="not_found", support="null_ok"
            )
        return FieldValidation(
            name=draft.name,
            passed=False,
            status="rejected",
            reasons=["Null value needs the reason 'Not found in documents'"],
            support="unsupported",
        )
    if not draft.evidence_ids:
        reasons.append("Non-null value has no citation")
    chosen: list[EvidenceHit] = []
    for evidence_id in draft.evidence_ids:
        hit = by_id.get(evidence_id)
        if hit is None:
            reasons.append(f"Fabricated citation {evidence_id}")
            continue
        chosen.append(hit)
        for source in hit.sources:
            problem = locator_problem(source)
            if problem:
                reasons.append(problem)
            if source.bid_id != hit.bid_id:
                reasons.append("Cross-bid citation")
    texts = [hit.text for hit in chosen]
    supported = bool(chosen) and (
        _support(draft.value, texts)
        or (draft.name in PARAPHRASE_FIELDS and _paraphrase_support(draft.value, texts))
    )
    if chosen and not supported:
        reasons.append("Cited evidence does not support the value")
    if (
        draft.name in DATE_FIELDS
        and not date_phrases(draft.value)
        and not MONTH_YEAR_RE.search(draft.value)
        and not (draft.name in RELATIVE_DATE_FIELDS and RELATIVE_RE.search(draft.value))
        and not (draft.name in RELATIVE_DATE_FIELDS and SCHEDULE_RE.search(draft.value))
        and draft.value.casefold()
        not in {
            "none",
            "not required",
        }
    ):
        reasons.append("Date field is not a date stated in the evidence")
    if supported:
        reasons.extend(semantic_problems(draft.name, draft.value, texts))
    if supported and draft.name == "Bid Number":
        listing = [
            hit.text
            for hit in evidence
            if any(source.document_type == DocumentType.bid_page for source in hit.sources)
        ]
        reasons.extend(bid_number_problems(draft.value, [hit.text for hit in evidence], listing))
    if (
        supported
        and draft.name == "Bid Summary"
        and len(draft.value.split()) > COPIED_SUMMARY_WORDS
        and any(contained(draft.value, text) for text in texts)
    ):
        reasons.append("Bid Summary copies one passage; summarize the bid in your own sentences")
    if supported and draft.name == "Bid Summary":
        reasons.extend(summary_claim_problems(draft.value, texts))
    if (
        draft.name in IDENTIFIER_FIELDS
        and chosen
        and not any(whole_identifier(draft.value, hit.text) for hit in chosen)
    ):
        items = [item.strip() for item in re.split(r"[;,]", draft.value) if item.strip()]
        if len(items) > 1 and all(
            any(whole_identifier(item, hit.text) for hit in chosen) for item in items
        ):
            reasons.append(
                f"{draft.name} lists {len(items)} codes; give the one identifier of the "
                "requested product"
            )
        else:
            reasons.append("Identifier is not copied from the cited evidence")
    if draft.name == "Bid Summary" and chosen:
        for match in re.findall(r"\d[\d,./:-]*", draft.value):
            number = match.rstrip(",./:-")
            if not any(number in hit.text for hit in chosen):
                reasons.append(f"Summary number {number} is not in the cited evidence")
                break
    if reasons:
        return FieldValidation(
            name=draft.name,
            passed=False,
            status="rejected",
            reasons=reasons,
            support="unsupported",
        )
    return FieldValidation(name=draft.name, passed=True, status="accepted", support="contained")
