"""Parity of bounded-batch reranking with unbatched (one pair per call) scoring."""

import math
import zlib

import pytest

from rfp.search.errors import InferenceFailed
from rfp.search.models import rerank_bounded

QUERY = "warranty period laptops"


class WordCounter:
    def count(self, text: str) -> int:
        return len(text.split()) + 2

    def pair_count(self, query: str, document: str) -> int:
        return len(query.split()) + len(document.split()) + 3


class Scorer:
    """Deterministic per-pair score that records every model call."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, query: str, texts: list[str]) -> list[float]:
        self.calls.append(list(texts))
        return [self.pair(query, text) for text in texts]

    @staticmethod
    def pair(query: str, text: str) -> float:
        overlap = len(set(query.split()) & set(text.split()))
        return overlap + (zlib.crc32(text.encode()) % 1000) / 1000


DOCUMENTS = [
    "laptop warranty period is five years",
    "delivery within thirty days",
    "warranty",
    "invoices are paid net thirty",
    "the warranty period for laptops is three years on site",
    "",
    "accidental damage protection laptops",
]


def run(documents, batch_size, scorer=None, max_tokens=64, overlap=2):
    scorer = scorer or Scorer()
    result = rerank_bounded(
        QUERY, documents, scorer, WordCounter(), max_tokens, overlap, batch_size
    )
    return result, scorer


@pytest.mark.parametrize("batch_size", [1, 2, 3, 4, 7, 32])
def test_batched_scores_match_unbatched_in_input_order(batch_size):
    reference, _ = run(DOCUMENTS, 1)
    batched, scorer = run(DOCUMENTS, batch_size)
    assert batched.scores == reference.scores
    assert batched.scores == [Scorer.pair(QUERY, d) for d in DOCUMENTS]
    assert all(len(call) <= batch_size for call in scorer.calls)
    sent = [text for call in scorer.calls for text in call]
    assert sorted(sent) == sorted(DOCUMENTS)  # each pair scored exactly once
    assert len(scorer.calls) == math.ceil(len(DOCUMENTS) / batch_size)


def test_partial_final_batch_is_scored():
    result, scorer = run(DOCUMENTS, 3)
    assert [len(call) for call in scorer.calls] == [3, 3, 1]
    assert all(math.isfinite(score) for score in result.scores)


def test_empty_input_makes_no_model_call():
    result, scorer = run([], 4)
    assert result.scores == [] and scorer.calls == []


def test_batch_size_must_be_positive():
    with pytest.raises(ValueError):
        run(DOCUMENTS, 0)


@pytest.mark.parametrize(
    "bad", [lambda texts: [0.0] * (len(texts) - 1), lambda texts: [math.nan] * len(texts)]
)
def test_wrong_count_or_non_finite_scores_fail(bad):
    with pytest.raises(InferenceFailed) as error:
        rerank_bounded(QUERY, DOCUMENTS, lambda _, t: bad(t), WordCounter(), 64, 2, 4)
    assert error.value.code == "reranker_failed"


def test_model_error_propagates_without_partial_scores():
    def failing(query, texts):
        raise InferenceFailed(code="reranker_failed")

    with pytest.raises(InferenceFailed):
        rerank_bounded(QUERY, DOCUMENTS, failing, WordCounter(), 64, 2, 4)


@pytest.mark.parametrize("batch_size", [1, 2, 5, 64])
def test_windowed_documents_take_max_over_windows_for_any_batch_size(batch_size):
    filler = " ".join(f"filler{i}" for i in range(40))
    documents = [filler + " laptops warranty period", "warranty period", filler]
    reference, _ = run(documents, 1, max_tokens=20, overlap=6)
    batched, scorer = run(documents, batch_size, max_tokens=20, overlap=6)
    assert batched.scores == reference.scores
    assert batched.windowed == 2 and batched.truncated == 0
    assert all(len(call) <= batch_size for call in scorer.calls)
    windows = [t for call in scorer.calls for t in call if t not in documents]
    assert all(WordCounter().pair_count(QUERY, w) <= 20 for w in windows)
    # Some window carries all three query words; filler-only windows share none.
    assert batched.scores[0] >= 3 > 1 > batched.scores[2]


def test_real_cross_encoder_batches_match_one_pair_per_call():
    from rfp.schemas.search import SearchConfig
    from rfp.search.errors import ModelUnavailable
    from rfp.search.models import FastEmbedReranker

    try:
        reranker = FastEmbedReranker(SearchConfig(model_cache_dir=".cache/fastembed"))
    except ModelUnavailable:
        pytest.skip("Reranker model is not cached locally (run init-models)")
    query = "What is the warranty period for laptops?"
    # Varied lengths so batches pad differently, plus one pair over the 512-token limit.
    documents = [
        ("Warranty clause. " * (i * 3 + 1)) + f"Item {i} lasts {i} years." for i in range(9)
    ]
    documents.append(" ".join(f"Section {i} covers invoicing." for i in range(150)))
    scores = {}
    for size in (1, 4, 64):
        reranker.batch_size = size
        scores[size] = reranker.rerank(query, documents).scores
    for size in (4, 64):
        assert max(abs(a - b) for a, b in zip(scores[1], scores[size], strict=True)) <= 1e-4
        order = sorted(range(len(documents)), key=lambda i: -scores[size][i])
        assert order == sorted(range(len(documents)), key=lambda i: -scores[1][i])
