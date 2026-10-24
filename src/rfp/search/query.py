"""Deterministic query normalization and controlled synonym expansion (no LLM rewriting)."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from rfp.schemas.search import QueryExpansion, SearchConfig
from rfp.search.errors import InvalidSearchRequest
from rfp.search.identifiers import DASHES, extract_identifiers, identifier_terms

QUOTES = dict.fromkeys(map(ord, "\u2018\u2019\u201a\u201b"), "'") | dict.fromkeys(
    map(ord, "\u201c\u201d\u201e\u201f"), '"'
)


@dataclass(frozen=True)
class QueryPlan:
    original: str
    normalized: str
    dense_query: str
    sparse_query: str
    expansions: list[QueryExpansion] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    identifier_terms: list[str] = field(default_factory=list)

    @property
    def query_hash(self) -> str:
        # Logged instead of query text, which may be sensitive.
        return hashlib.sha256(self.original.encode("utf-8")).hexdigest()[:16]


def normalize_query(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(DASHES).translate(QUOTES)
    text = "".join(c if c.isprintable() else " " for c in text)
    return " ".join(text.split())


def _contains(haystack: str, phrase: str) -> bool:
    pattern = r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])"
    return re.search(pattern, haystack, re.IGNORECASE) is not None


def expand(normalized: str, synonyms: dict[str, list[str]], limit: int) -> list[QueryExpansion]:
    expansions: list[QueryExpansion] = []
    seen = normalized.lower()
    budget = limit
    for term in sorted(synonyms, key=lambda value: (-len(value), value)):
        if budget <= 0:
            break
        if not _contains(normalized, term):
            continue
        added = []
        for candidate in synonyms[term]:
            if budget <= 0:
                break
            if not _contains(seen, candidate):
                added.append(candidate)
                seen += " " + candidate.lower()
                budget -= 1
        if added:
            expansions.append(QueryExpansion(term=term, added=added))
    return expansions


def plan_query(query: str, config: SearchConfig) -> QueryPlan:
    if len(query) > config.max_query_chars:
        raise InvalidSearchRequest("Query exceeds the configured maximum length", "query_too_long")
    normalized = normalize_query(query)
    if not any(c.isalnum() for c in normalized):
        raise InvalidSearchRequest("Query must contain letters or digits", "invalid_query")
    expansions = expand(normalized, config.synonyms, config.max_expansions)
    added = [item for expansion in expansions for item in expansion.added]
    identifiers = extract_identifiers(normalized)
    return QueryPlan(
        original=query,
        normalized=normalized,
        # The user's exact wording is preserved for dense retrieval.
        dense_query=query.strip(),
        # Expansion is additive and applied only to the lexical branch.
        sparse_query=" ".join([normalized, *added]),
        expansions=expansions,
        identifiers=identifiers,
        identifier_terms=identifier_terms(identifiers),
    )
