import hashlib
import re
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

TOKENIZER_FILES = ("special_tokens_map.json", "tokenizer.json", "tokenizer_config.json")
ESTIMATED_IDENTIFIER = "utf8-bytes-div4-v1"


class TokenCounter(Protocol):
    identifier: str

    def count(self, text: str) -> int: ...


class EstimatedTokenCounter:
    """Deterministic UTF-8 bytes / 4 estimate, not any embedding-model tokenizer."""

    identifier = ESTIMATED_IDENTIFIER

    def count(self, text: str) -> int:
        return (len(text.encode("utf-8")) + 3) // 4


class TokenizerUnavailable(ValueError):
    pass


def largest(low: int, high: int, fits: Callable[[int], bool]) -> int:
    """Largest value in [low, high] satisfying a predicate true up to some point, else low - 1."""
    best = low - 1
    while low <= high:
        middle = (low + high) // 2
        if fits(middle):
            best, low = middle, middle + 1
        else:
            high = middle - 1
    return best


def smallest(low: int, high: int, fits: Callable[[int], bool]) -> int:
    """Smallest value in [low, high] satisfying a predicate true from some point, else high + 1."""
    best = high + 1
    while low <= high:
        middle = (low + high) // 2
        if fits(middle):
            best, high = middle, middle - 1
        else:
            low = middle + 1
    return best


def token_windows(
    text: str,
    budget: int,
    overlap: int,
    measure: Callable[[str], int],
    overlap_measure: Callable[[str], int] | None = None,
) -> tuple[list[tuple[int, int]], bool]:
    """Whitespace-bounded character spans whose measured token count fits ``budget``.

    Consecutive spans share up to ``overlap`` tokens. Words are never split unless a single
    word alone exceeds the budget (reported via the returned flag).
    """
    overlap_measure = overlap_measure or measure
    words = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
    spans: list[tuple[int, int]] = []
    split_word = False
    index = 0
    # Predicates below are consumed by largest()/smallest() within the same iteration, so
    # capturing the loop variables is safe (B023).
    while index < len(words):
        start = words[index][0]
        last = largest(
            index,
            len(words) - 1,
            lambda j: measure(text[start : words[j][1]]) <= budget,  # noqa: B023
        )
        if last < index:
            word_end = words[index][1]
            cut = max(
                largest(
                    start + 1,
                    word_end,
                    lambda end: measure(text[start:end]) <= budget,  # noqa: B023
                ),
                start + 1,
            )
            spans.append((start, cut))
            split_word = True
            if cut < word_end:
                words[index] = (cut, word_end)
            else:
                index += 1
            continue
        end = words[last][1]
        spans.append((start, end))
        first = index
        index = last + 1
        if overlap and index < len(words):
            # Restart at the earliest word whose suffix fits the overlap, strictly after this
            # window's first word so progress is guaranteed.
            index = smallest(
                first + 1,
                last + 1,
                lambda k: overlap_measure(text[words[k][0] : end]) <= overlap,  # noqa: B023
            )
    return spans, split_word


def tokenizer_identifier(model: str, model_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in TOKENIZER_FILES:
        path = model_dir / name
        if path.exists():
            digest.update(name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return f"hf:{model}@{digest.hexdigest()[:16]}"


def model_from_identifier(identifier: str) -> str:
    if not identifier.startswith("hf:") or "@" not in identifier:
        raise TokenizerUnavailable("tokenizer_identifier_invalid")
    return identifier[3:].rsplit("@", 1)[0]


class ModelTokenCounter:
    """Exact input length for a FastEmbed model: the inference tokenizer with truncation and
    padding disabled, counting the special tokens the model actually receives."""

    def __init__(self, model: str, model_dir: Path, tokenizer: Any | None = None):
        if tokenizer is None:
            from fastembed.common.preprocessor_utils import load_tokenizer

            tokenizer, _ = load_tokenizer(model_dir)
        tokenizer.no_truncation()
        tokenizer.no_padding()
        self._tokenizer = tokenizer
        self._lock = threading.Lock()
        self.model = model
        self.identifier = tokenizer_identifier(model, model_dir)

    def count(self, text: str) -> int:
        return self._count(text)

    def counts(self, texts: list[str]) -> list[int]:
        with self._lock:
            return [len(encoding.ids) for encoding in self._tokenizer.encode_batch(texts)]

    def pair_count(self, query: str, document: str) -> int:
        with self._lock:
            return len(self._tokenizer.encode(query, document).ids)

    @lru_cache(maxsize=65536)  # noqa: B019 - counters are long-lived and few per process
    def _count(self, text: str) -> int:
        with self._lock:
            return len(self._tokenizer.encode(text).ids)


def model_dir(model: str, cache_dir: str, kind: str = "dense") -> Path:
    """Resolve cached model files without loading ONNX weights or downloading anything."""
    try:
        if kind == "reranker":
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            wrapper: Any = TextCrossEncoder(
                model_name=model, cache_dir=cache_dir, local_files_only=True, lazy_load=True
            )
        else:
            from fastembed import TextEmbedding

            wrapper = TextEmbedding(
                model_name=model, cache_dir=cache_dir, local_files_only=True, lazy_load=True
            )
        return Path(wrapper.model._model_dir)
    except Exception:
        raise TokenizerUnavailable("tokenizer_unavailable") from None


_counters: dict[tuple[str, str], TokenCounter] = {}
_counters_lock = threading.Lock()


def counter_for(token_counter: str, identifier: str, cache_dir: str) -> TokenCounter:
    """The counter a chunk configuration requires; a different tokenizer is an explicit error."""
    if token_counter == "utf8_bytes_v1":
        return EstimatedTokenCounter()
    key = (identifier, str(Path(cache_dir).resolve()))
    with _counters_lock:
        if key not in _counters:
            model = model_from_identifier(identifier)
            counter = ModelTokenCounter(model, model_dir(model, cache_dir))
            if counter.identifier != identifier:
                raise TokenizerUnavailable("tokenizer_identifier_mismatch")
            _counters[key] = counter
        return _counters[key]
