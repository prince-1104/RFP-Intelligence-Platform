"""Extraction may search only a loopback Qdrant. Cloud targets fail closed."""

from urllib.parse import urlsplit

from rfp.agents.errors import ExtractionError

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def assert_local_qdrant(url: str) -> str:
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ExtractionError(
            "unsafe_search_target",
            "Extraction searches only loopback Qdrant and refuses credentials or remote hosts.",
        )
    return url.rstrip("/")
