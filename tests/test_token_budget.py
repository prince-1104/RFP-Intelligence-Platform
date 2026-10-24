"""Exact embedding-model token budgets for chunks and cross-encoder inputs."""

import json
from pathlib import Path

import pytest
from search_fakes import (
    BID_A,
    FakeReranker,
    generation,
    sample_bid,
    services,
)
from test_chunk_engine import document, page
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from rfp.chunking import tokens
from rfp.chunking.engine import chunk_document
from rfp.chunking.tokens import ModelTokenCounter, TokenizerUnavailable, counter_for
from rfp.core.config import Settings
from rfp.schemas.chunks import ChunkConfig
from rfp.schemas.parsed import ParsedTable, SourceLocator
from rfp.schemas.search import SearchConfig, SearchRequest
from rfp.search.models import RerankScores

REAL_CACHE = ".cache/fastembed"


def write_tokenizer(directory: Path, max_length: int = 4) -> Path:
    words = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", *"abcdefgh"]
    tokenizer = Tokenizer(models.WordLevel({w: i for i, w in enumerate(words)}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
    )
    directory.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(directory / "tokenizer.json"))
    (directory / "tokenizer_config.json").write_text(
        json.dumps({"model_max_length": max_length, "pad_token": "[PAD]"})
    )
    (directory / "special_tokens_map.json").write_text(json.dumps({"pad_token": "[PAD]"}))
    return directory


def test_model_counter_counts_full_input_without_truncation(tmp_path):
    directory = write_tokenizer(tmp_path / "model")
    counter = ModelTokenCounter("test/model", directory)
    # Inference truncates at 4; the budget counter must see the true length incl. specials.
    assert counter.count("a b c d e f") == 8
    assert counter.counts(["a", "a b"]) == [3, 4]
    assert counter.pair_count("a b", "c d e f g") == 10
    first = counter.identifier
    assert first.startswith("hf:test/model@") and len(first.rsplit("@", 1)[1]) == 16
    write_tokenizer(directory, max_length=8)
    assert ModelTokenCounter("test/model", directory).identifier != first


def test_counter_for_rejects_a_different_tokenizer(tmp_path, monkeypatch):
    directory = write_tokenizer(tmp_path / "model")
    monkeypatch.setattr(tokens, "model_dir", lambda model, cache_dir, kind="dense": directory)
    expected = ModelTokenCounter("test/model", directory).identifier
    assert counter_for("model_tokenizer_v1", expected, str(tmp_path)).identifier == expected
    with pytest.raises(TokenizerUnavailable, match="tokenizer_identifier_mismatch"):
        counter_for("model_tokenizer_v1", "hf:test/model@0000000000000000", str(tmp_path / "x"))


def test_model_config_never_falls_back_to_the_estimate():
    with pytest.raises(ValueError, match="token_counter_required"):
        chunk_document(document([page(["text"])]), ChunkConfig())
    with pytest.raises(ValueError):
        ChunkConfig(token_counter="utf8_bytes_v1")
    with pytest.raises(ValueError):
        ChunkConfig(tokenizer_identifier="hf:model-without-hash")


def test_settings_bind_chunk_budget_to_the_dense_model():
    base = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://test:test@localhost/test",
        "redis_url": "redis://localhost:6379/0",
        "qdrant_url": "http://localhost:6333",
    }
    assert Settings(**base).chunking.max_tokens <= Settings(**base).search.dense_max_tokens
    with pytest.raises(ValueError, match="DENSE_MAX_TOKENS"):
        Settings(**base, chunking={"target_tokens": 500, "max_tokens": 600})
    with pytest.raises(ValueError, match="DENSE_MODEL"):
        Settings(**base, search={"dense_model": "BAAI/bge-base-en-v1.5", "dense_dim": 768})


def test_rerank_scores_each_distinct_text_once_and_reports_windowing(settings):
    class Recording(FakeReranker):
        def __init__(self):
            super().__init__()
            self.batches: list[list[str]] = []

        def rerank(self, query, documents):
            self.batches.append(list(documents))
            scores = super().rerank(query, documents).scores
            return RerankScores(scores, windowed=1, max_pair_tokens=600)

    reranker = Recording()
    indexer, search, _ = services(settings, reranker=reranker)
    assert indexer.index([generation(r) for r in sample_bid(BID_A)]).succeeded
    response = search.search(SearchRequest(query="proposals valid ninety days", top_k=5))
    sent = reranker.batches[-1]
    assert len(sent) == len(set(sent)) == response.candidate_counts["rerank_inputs"]
    duplicated = [r for r in response.results if "ninety days" in r.text]
    assert duplicated and duplicated[0].additional_citations  # both sources still cited
    assert "rerank_input_windowed" in response.warnings
    assert response.candidate_counts["rerank_windowed"] == 1


def real_counter() -> ModelTokenCounter:
    try:
        counter = counter_for(
            "model_tokenizer_v1", ChunkConfig().tokenizer_identifier, REAL_CACHE
        )
    except TokenizerUnavailable:
        pytest.skip("BGE tokenizer is not cached locally (run init-models)")
    assert isinstance(counter, ModelTokenCounter)
    return counter


def test_real_bge_chunks_fit_the_budget_and_are_never_truncated():
    counter = real_counter()
    from fastembed.common.preprocessor_utils import load_tokenizer

    inference, _ = load_tokenizer(tokens.model_dir("BAAI/bge-small-en-v1.5", REAL_CACHE))
    prose = " ".join(
        f"Clause {i}.{i % 7} requires PX-{i:04d} at $1,{i:03d}.50 within {i} days." for i in range(300)
    )
    cell = "\n".join(f"• Requirement {i}: Intel i5, 16GB DDR5, SKU 210-B{i:03d}" for i in range(200))
    table = ParsedTable(
        table_id="t",
        table_index=1,
        rows=[["Scope", cell], ["Qty", "20"]],
        source_locator=SourceLocator(relative_path="specs.pdf", page_number=1),
        extraction_method="pdfplumber",
    )
    config = ChunkConfig()
    result = chunk_document(document([page([prose], tables=[table])]), config, counter=counter)
    texts = [content.text for content in result.contents]
    exact = counter.counts(texts)
    assert max(exact) <= config.max_tokens and len(texts) > 10
    assert [sum(e.attention_mask) for e in inference.encode_batch(texts)] == exact
    joined = " ".join(texts)
    assert all(f"PX-{i:04d}" in joined and f"210-B{i:03d}" in joined for i in range(200))
    assert all(o.tokenizer_identifier == config.tokenizer_identifier for o in result.occurrences)


def test_real_reranker_windows_overlong_pairs_instead_of_truncating():
    real_counter()
    from rfp.search.errors import ModelUnavailable
    from rfp.search.models import FastEmbedReranker

    try:
        reranker = FastEmbedReranker(SearchConfig(model_cache_dir=REAL_CACHE))
    except ModelUnavailable:
        pytest.skip("Reranker model is not cached locally")
    filler = " ".join(f"Section {i} covers delivery logistics and invoicing." for i in range(120))
    evidence = "The manufacturer warranty period for every laptop is five years."
    query = "What is the warranty period for laptops?"
    output = reranker.rerank(query, [filler + " " + evidence, filler, evidence])
    assert output.windowed == 2 and output.truncated == 0 and output.max_pair_tokens > 512
    assert output.scores[0] > output.scores[1]
    assert abs(output.scores[2] - reranker.rerank(query, [evidence]).scores[0]) < 1e-4
