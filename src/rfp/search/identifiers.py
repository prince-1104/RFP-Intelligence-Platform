"""Deterministic identifier detection (bid IDs, model numbers, SKUs, part numbers).

BM25 tokenization strips punctuation (``PX-420`` becomes ``px`` + ``420``), so identifiers are
also stored as exact keyword payload values and matched by a dedicated retrieval branch.
"""

import re
import unicodedata

DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), "-")
SEPARATORS = "-/._#"
VERSION = "identifiers_v1"

_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|[-/._#](?=[A-Za-z0-9]))*")
_UNIT_OR_ORDINAL = re.compile(
    r"^\d+(?:\.\d+)?(?:st|nd|rd|th|am|pm|mm|cm|m|km|in|ft|yd|kg|g|lb|lbs|oz|gb|mb|tb|"
    r"hz|khz|mhz|ghz|v|kv|w|kw|a|ma|s|ms|h|hr|hrs|pcs|ea)$",
    re.IGNORECASE,
)
MAX_IDENTIFIERS = 200


def clean(text: str) -> str:
    return unicodedata.normalize("NFC", text).translate(DASHES)


def is_identifier(token: str) -> bool:
    if not 3 <= len(token) <= 64 or not any(c.isdigit() for c in token):
        return False
    if _UNIT_OR_ORDINAL.match(token):
        return False
    has_letter = any(c.isalpha() for c in token)
    has_structural_separator = any(c in "-/#" for c in token)
    return has_letter or has_structural_separator


def normalize_identifier(token: str) -> str:
    return clean(token).upper()


def compact(identifier: str) -> str:
    return "".join(c for c in identifier if c not in SEPARATORS)


def extract_identifiers(text: str) -> list[str]:
    """Ordered unique normalized identifiers, punctuation preserved."""
    found: dict[str, None] = {}
    for match in _TOKEN.finditer(clean(text)):
        token = match.group(0)
        if is_identifier(token):
            found.setdefault(normalize_identifier(token), None)
            if len(found) >= MAX_IDENTIFIERS:
                break
    return list(found)


def identifier_terms(identifiers: list[str]) -> list[str]:
    """Normalized plus compact forms, so ``PX-420`` and ``PX420`` both match exactly."""
    terms: dict[str, None] = {}
    for identifier in identifiers:
        terms.setdefault(identifier, None)
        terms.setdefault(compact(identifier), None)
    return [term for term in terms if term]


def matched_identifiers(query_identifiers: list[str], payload_terms: list[str]) -> list[str]:
    available = set(payload_terms)
    return [
        identifier
        for identifier in query_identifiers
        if identifier in available or compact(identifier) in available
    ]
