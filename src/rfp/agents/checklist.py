"""Candidate checklist for list questions ("which affidavits", "all forms", ...).

Candidates are named items found in retrieved passages and in the file names of retrieved
documents. Every candidate must be answered or explicitly marked unresolved. The checklist only
covers retrieved evidence; it is never proof that a list is exhaustive.
"""

import re

from rfp.agents.schemas import EvidenceHit
from rfp.agents.validator import normalize

LIST_NOUNS = {
    "affidavits": "affidavit",
    "forms": "form",
    "certifications": "certification",
    "certificates": "certificate",
    "attachments": "attachment",
    "exhibits": "exhibit",
    "documents": "document",
    "requirements": "requirement",
    "submittals": "submittal",
    "licenses": "license",
}
LIST_CUE = re.compile(r"\b(which|what|list|all|every|each)\b", re.IGNORECASE)
NAMED_ITEM_NOUNS = frozenset(
    {"affidavit", "form", "certification", "certificate", "attachment", "exhibit"}
)
LEADING_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "each",
        "any",
        "such",
        "said",
        "other",
        "certain",
        "of",
        "and",
        "or",
        "to",
        "for",
        "in",
        "on",
        "by",
        "with",
        "my",
        "your",
        "our",
        "its",
        "their",
        "following",
    }
)


def list_keyword(question: str) -> str | None:
    if not LIST_CUE.search(question):
        return None
    for word in re.findall(r"[a-z]+", question.casefold()):
        if word in LIST_NOUNS:
            return LIST_NOUNS[word]
    return None


def _clean(words: list[str], keyword: str) -> str | None:
    while words and words[0].casefold() in LEADING_STOP:
        words = words[1:]
    if not words:
        return None
    return " ".join([*(word.title() for word in words), keyword.title()])


def _from_text(text: str, keyword: str) -> list[str]:
    pattern = re.compile(rf"((?:\b[A-Z][A-Za-z&'-]*\s+){{1,2}})(?i:{re.escape(keyword)})s?\b")
    items = []
    for match in pattern.finditer(text):
        item = _clean(match.group(1).split(), keyword)
        if item:
            items.append(item)
    return items


def _from_file(name: str, keyword: str) -> str | None:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", name)
    words = re.split(r"[\s_\-]+", stem)
    folded = [word.casefold() for word in words]
    if keyword not in folded:
        return None
    position = folded.index(keyword)
    return _clean([w for w in words[:position] if w][-2:], keyword)


def candidates(question: str, hits: list[EvidenceHit]) -> dict[str, list[str]]:
    """Map each candidate item to the evidence ids that mention it."""
    keyword = list_keyword(question)
    if keyword is None or keyword not in NAMED_ITEM_NOUNS:
        return {}
    found: dict[str, list[str]] = {}
    for hit in hits:
        names = _from_text(hit.text, keyword)
        from_file = _from_file(hit.sources[0].file, keyword)
        if from_file:
            names.append(from_file)
        for name in names:
            found.setdefault(name, [])
            if hit.evidence_id not in found[name]:
                found[name].append(hit.evidence_id)
    merged: dict[str, list[str]] = {}
    for name in sorted(found, key=len):
        base = next(
            (kept for kept in merged if normalize(name).endswith(" " + normalize(kept))), None
        )
        if base is None:
            merged[name] = list(found[name])
        else:
            merged[base].extend(i for i in found[name] if i not in merged[base])
    return merged


def mentions(item: str, text: str) -> bool:
    return normalize(item) in normalize(text.replace("_", " "))
