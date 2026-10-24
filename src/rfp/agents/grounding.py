"""Lexical claim grounding against the verified quotes linked to each claim.

This is a deterministic check, not entailment. It requires that numbers, dates, times, time zones,
meeting platforms, named terms, negations, requirement words and scope words in a claim also
appear in that claim's own quotes (or, for names, in the question or cited file names). Leading
zeros are the only formatting difference tolerated (03:00 vs 3:00). It cannot tell whether a
sentence built from supported words means what the quote means.
"""

import re

from rfp.agents.validator import contained, date_phrases, normalize

TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:[-/.:,][A-Za-z0-9]+)*\b")
TIME_ZONES = frozenset(
    {"cst", "cdt", "ct", "est", "edt", "et", "pst", "pdt", "pt", "mst", "mdt", "mt", "utc", "gmt"}
)
PLATFORMS = ("teams", "zoom", "webex", "google meet", "in person", "in-person", "virtual")
SCOPE = (
    "all other",
    "unchanged",
    "remain the same",
    "no other",
    "only",
    "every",
    "exclusively",
    "mandatory",
    "optional",
)
NEGATION = re.compile(r"\b(not|no|none|never|without|nor)\b|n't\b", re.IGNORECASE)
MODAL = re.compile(r"\b(must|shall|required|requires|require|mandatory)\b", re.IGNORECASE)
LEADING_ZERO = re.compile(r"(?<!\d)0+(?=\d)")
NAMED = re.compile(r"\b[A-Za-z][A-Za-z&']*(?:-[A-Za-z]+)*\b")
COMMON = frozenset(
    {
        "a",
        "an",
        "the",
        "yes",
        "no",
        "it",
        "this",
        "these",
        "those",
        "there",
        "they",
        "vendors",
        "vendor",
        "bidders",
        "proposers",
        "each",
        "i",
    }
)


def facts(text: str) -> list[str]:
    found = list(date_phrases(text))
    for match in TOKEN.finditer(text):
        token = match.group(0).strip(".,:")
        if len(token) >= 2 and any(char.isdigit() for char in token):
            found.append(token)
    return found


def _sentence_starts(text: str) -> set[int]:
    starts = {0}
    for match in re.finditer(r"[.!?:;]\s+|\(\s*", text):
        starts.add(match.end())
    return starts


def named_terms(text: str) -> list[str]:
    """Capitalized or upper-case words that do not start a sentence."""
    starts = _sentence_starts(text)
    terms = []
    for match in NAMED.finditer(text):
        word = match.group(0)
        if match.start() in starts or not word[0].isupper():
            continue
        if word.casefold() in COMMON:
            continue
        terms.append(word)
    return terms


def _word_in(word: str, text: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(word.casefold())}(?![\w])", text) is not None


def claim_problems(
    claim: str,
    quotes: list[str],
    question: str = "",
    file_names: list[str] | None = None,
    strict_requirements: bool = False,
    required_items: list[str] | None = None,
) -> list[str]:
    """Problems with a claim. Requirement words echoed from the question are tolerated unless
    strict_requirements is set. A claim that names one of required_items with requirement wording
    needs a single quote containing both the item and a requirement word."""
    if not quotes:
        return ["claim has no verified quote"]
    support = normalize("\n".join(quotes))
    names = normalize(" ".join(name.replace("_", " ") for name in file_names or []))
    asked = normalize(question)
    problems = []
    unpadded = LEADING_ZERO.sub("", support)
    for fact in facts(claim):
        if not (contained(fact, support) or contained(LEADING_ZERO.sub("", fact), unpadded)):
            problems.append(f"'{fact}' is not in the linked quote")
    lowered = normalize(claim)
    for word in re.findall(r"[a-z]+", lowered):
        if word in TIME_ZONES and not _word_in(word, support):
            problems.append(f"time zone '{word.upper()}' is not in the linked quote")
    for phrase in PLATFORMS + SCOPE:
        if _word_in(phrase, lowered) and not _word_in(phrase, support):
            problems.append(f"'{phrase}' is not in the linked quote")
    if NEGATION.search(claim) and not NEGATION.search(support):
        problems.append("the claim negates something the linked quote does not")
    echoed = MODAL.search(question) is not None and not strict_requirements
    if MODAL.search(claim) and not MODAL.search(support) and not echoed:
        problems.append("requirement wording (must/shall/required) is not in the linked quote")
    if MODAL.search(claim):
        for item in required_items or []:
            if _word_in(normalize(item), lowered) and not any(
                _word_in(normalize(item), normalize(q)) and MODAL.search(q) for q in quotes
            ):
                problems.append(f"no linked quote states a requirement for '{item}'")
    for term in named_terms(claim):
        folded = term.casefold()
        if folded in TIME_ZONES or folded in PLATFORMS:
            continue
        if not (_word_in(folded, support) or _word_in(folded, asked) or _word_in(folded, names)):
            problems.append(f"'{term}' is not in the linked quote")
    return list(dict.fromkeys(problems))
