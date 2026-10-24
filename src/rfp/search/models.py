"""Local FastEmbed inference behind small protocols so tests can inject offline fakes."""

import hashlib
import importlib.metadata
import logging
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

import numpy as np

from rfp.chunking.tokens import ModelTokenCounter, token_windows
from rfp.core.logging import log_event
from rfp.schemas.chunks import sha256, stable_json
from rfp.schemas.search import SearchConfig
from rfp.search.errors import InferenceFailed, InvalidVector, ModelUnavailable, SearchServiceError

logger = logging.getLogger("rfp.search.models")

# Embeddings are computed from exact canonical chunk text only; section context stays on the
# occurrence so identical canonical text can safely share vectors across occurrences.
INPUT_REPRESENTATION = "canonical_text_v1"
MAX_SPARSE_INDEX = 2**32 - 1

Identity = dict[str, str | int | float | bool | None]
T = TypeVar("T")


@dataclass(frozen=True)
class SparseVector:
    indices: tuple[int, ...]
    values: tuple[float, ...]


@dataclass(frozen=True)
class EmbeddingProfile:
    fingerprint: str
    dense_model: str
    sparse_model: str
    dense_dim: int
    fastembed_version: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def short(self) -> str:
        return self.fingerprint[:12]


class DenseEmbedder(Protocol):
    model_id: str
    dim: int
    max_tokens: int

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]: ...
    def embed_query(self, text: str) -> np.ndarray: ...
    def token_counts(self, texts: Sequence[str]) -> list[int]: ...
    def identity(self) -> Identity: ...


class SparseEmbedder(Protocol):
    model_id: str

    def embed_documents(self, texts: Sequence[str]) -> list[SparseVector]: ...
    def embed_query(self, text: str) -> SparseVector: ...
    def identity(self) -> Identity: ...


@dataclass(frozen=True)
class RerankScores:
    scores: list[float]
    # Documents whose query+document input exceeded the cross-encoder limit and were scored
    # as the maximum over overlapping windows instead of being truncated.
    windowed: int = 0
    # Over-limit pairs left to model truncation because the query alone is too long to window.
    truncated: int = 0
    max_pair_tokens: int = 0


class Reranker(Protocol):
    model_id: str

    def rerank(self, query: str, documents: Sequence[str]) -> RerankScores: ...
    def identity(self) -> Identity: ...


def validate_dense(vector: Any, dim: int) -> list[float]:
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or array.shape[0] != dim or not np.all(np.isfinite(array)):
        raise InvalidVector(code="dense_dimension_mismatch" if array.ndim == 1 else None)
    if float(np.linalg.norm(array)) == 0.0:
        raise InvalidVector()
    return [float(value) for value in array]


def validate_sparse(indices: Any, values: Any) -> SparseVector:
    index_list = [int(value) for value in np.asarray(indices).tolist()]
    value_list = [float(value) for value in np.asarray(values, dtype=np.float64).tolist()]
    if len(index_list) != len(value_list) or len(set(index_list)) != len(index_list):
        raise InvalidVector(code="invalid_sparse_vector")
    if any(i < 0 or i > MAX_SPARSE_INDEX for i in index_list) or any(
        not math.isfinite(v) for v in value_list
    ):
        raise InvalidVector(code="invalid_sparse_vector")
    pairs = sorted(zip(index_list, value_list, strict=True))
    return SparseVector(tuple(i for i, _ in pairs), tuple(v for _, v in pairs))


def compute_profile(dense: Identity, sparse: Identity) -> EmbeddingProfile:
    details = {"dense": dense, "sparse": sparse, "input_representation": INPUT_REPRESENTATION}
    return EmbeddingProfile(
        fingerprint=sha256(stable_json(details)),
        dense_model=str(dense["model"]),
        sparse_model=str(sparse["model"]),
        dense_dim=int(dense["dim"] or 0),
        fastembed_version=str(dense.get("fastembed_version") or ""),
        details=details,
    )


def collection_name(base: str, profile: EmbeddingProfile) -> str:
    return f"{base}__{profile.short}"


def fastembed_version() -> str:
    try:
        return importlib.metadata.version("fastembed")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _model_dir(wrapper: Any) -> Path:
    inner = getattr(wrapper, "model", None)
    directory = getattr(inner, "_model_dir", None)
    if directory is None:
        raise ModelUnavailable("Model files could not be located for version verification")
    return Path(directory)


def _description(registry: list[dict[str, Any]], model: str) -> dict[str, Any]:
    for description in registry:
        if description["model"] == model:
            return description
    raise ModelUnavailable(
        f"Model '{model}' is not supported by the pinned FastEmbed version",
        code="unsupported_model",
    )


def _cache_kwargs(config: SearchConfig, allow_download: bool) -> dict[str, Any]:
    Path(config.model_cache_dir).mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": config.model_cache_dir,
        "local_files_only": not (allow_download or config.allow_model_download),
    }


class FastEmbedDense:
    def __init__(self, config: SearchConfig, allow_download: bool = False):
        from fastembed import TextEmbedding

        self.description = _description(TextEmbedding.list_supported_models(), config.dense_model)
        if int(self.description["dim"]) != config.dense_dim:
            raise ModelUnavailable(
                "Configured SEARCH__DENSE_DIM does not match the dense model dimension",
                code="dense_dimension_mismatch",
            )
        self.model_id = config.dense_model
        self.dim = config.dense_dim
        self.max_tokens = config.dense_max_tokens
        self.instruction = config.query_instruction
        self.batch_size = config.embed_batch_size
        try:
            self._model = TextEmbedding(
                model_name=config.dense_model,
                threads=config.onnx_threads,
                **_cache_kwargs(config, allow_download),
            )
        except SearchServiceError:
            raise
        except Exception:
            raise ModelUnavailable() from None
        self._file_hash = _file_sha256(_model_dir(self._model) / self.description["model_file"])
        self._counter = ModelTokenCounter(self.model_id, _model_dir(self._model))

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        return list(self._model.passage_embed(list(texts), batch_size=self.batch_size))

    def embed_query(self, text: str) -> np.ndarray:
        return next(iter(self._model.query_embed(self.instruction + text)))

    def token_counts(self, texts: Sequence[str]) -> list[int]:
        # FastEmbed's own count is taken after truncation and can never exceed the limit.
        return self._counter.counts(list(texts))

    def identity(self) -> Identity:
        return {
            "model": self.model_id,
            "source": str(self.description["sources"].get("hf")),
            "model_file": str(self.description["model_file"]),
            "model_file_sha256": self._file_hash,
            "dim": self.dim,
            "max_tokens": self.max_tokens,
            "normalization": "l2",
            "license": str(self.description.get("license")),
            "fastembed_version": fastembed_version(),
        }


class FastEmbedSparse:
    def __init__(self, config: SearchConfig, allow_download: bool = False):
        from fastembed import SparseTextEmbedding

        self.description = _description(
            SparseTextEmbedding.list_supported_models(), config.sparse_model
        )
        self.model_id = config.sparse_model
        self.params: dict[str, Any] = {
            "k": config.bm25_k,
            "b": config.bm25_b,
            "avg_len": config.bm25_avg_len,
            "language": config.bm25_language,
        }
        self.batch_size = config.embed_batch_size
        try:
            self._model = SparseTextEmbedding(
                model_name=config.sparse_model,
                **_cache_kwargs(config, allow_download),
                **self.params,
            )
        except SearchServiceError:
            raise
        except Exception:
            raise ModelUnavailable() from None
        stopwords = _model_dir(self._model) / f"{config.bm25_language}.txt"
        self._stopwords_hash = _file_sha256(stopwords) if stopwords.exists() else "none"

    def embed_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [
            validate_sparse(item.indices, item.values)
            for item in self._model.embed(list(texts), batch_size=self.batch_size)
        ]

    def embed_query(self, text: str) -> SparseVector:
        item = next(iter(self._model.query_embed(text)))
        return validate_sparse(item.indices, item.values)

    def identity(self) -> Identity:
        return {
            "model": self.model_id,
            "source": str(self.description["sources"].get("hf")),
            "stopwords_sha256": self._stopwords_hash,
            "license": str(self.description.get("license")),
            "fastembed_version": fastembed_version(),
            "qdrant_modifier": "idf",
            **self.params,
        }


class FastEmbedReranker:
    def __init__(self, config: SearchConfig, allow_download: bool = False):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.description = _description(
            TextCrossEncoder.list_supported_models(), config.reranker_model
        )
        self.model_id = config.reranker_model
        self.batch_size = config.rerank_batch_size
        try:
            self._model = TextCrossEncoder(
                model_name=config.reranker_model,
                threads=config.onnx_threads,
                **_cache_kwargs(config, allow_download),
            )
        except SearchServiceError:
            raise
        except Exception:
            raise ModelUnavailable() from None
        self._file_hash = _file_sha256(_model_dir(self._model) / self.description["model_file"])
        directory = _model_dir(self._model)
        self._counter = ModelTokenCounter(self.model_id, directory)
        from fastembed.common.preprocessor_utils import load_tokenizer

        truncation = load_tokenizer(directory)[0].truncation or {}
        self.max_tokens = int(truncation.get("max_length") or 512)
        self.overlap_tokens = config.rerank_window_overlap_tokens

    def rerank(self, query: str, documents: Sequence[str]) -> RerankScores:
        inputs: list[str] = []
        owners: list[int] = []
        windowed = truncated = 0
        longest = 0
        query_tokens = self._counter.count(query)
        for index, document in enumerate(documents):
            length = self._counter.pair_count(query, document)
            longest = max(longest, length)
            # A query that leaves no room for document text cannot be windowed meaningfully.
            if length <= self.max_tokens or query_tokens > self.max_tokens // 2:
                truncated += int(length > self.max_tokens)
                inputs.append(document)
                owners.append(index)
                continue
            windowed += 1
            spans, _ = token_windows(
                document,
                self.max_tokens,
                self.overlap_tokens,
                lambda value: self._counter.pair_count(query, value),
                self._counter.count,
            )
            for start, end in spans:
                inputs.append(document[start:end])
                owners.append(index)
        # Shorter inputs first so each batch pads to similar lengths; order is restored below.
        order = sorted(range(len(inputs)), key=lambda i: len(inputs[i]))
        raw = list(
            self._model.rerank(query, [inputs[i] for i in order], batch_size=self.batch_size)
        )
        scores = [-math.inf] * len(documents)
        for position, score in zip(order, raw, strict=True):
            owner = owners[position]
            scores[owner] = max(scores[owner], float(score))
        return RerankScores(scores, windowed, truncated, longest)

    def identity(self) -> Identity:
        return {
            "model": self.model_id,
            "source": str(self.description["sources"].get("hf")),
            "model_file_sha256": self._file_hash,
            "license": str(self.description.get("license")),
            "fastembed_version": fastembed_version(),
        }


class ModelRuntime:
    """Process-wide lazy models; a bounded semaphore caps concurrent CPU/memory-heavy inference."""

    def __init__(
        self,
        config: SearchConfig,
        dense_factory: Callable[[], DenseEmbedder] | None = None,
        sparse_factory: Callable[[], SparseEmbedder] | None = None,
        reranker_factory: Callable[[], Reranker] | None = None,
    ):
        self.config = config
        self._factories: dict[str, Callable[[], Any]] = {
            "dense": dense_factory or (lambda: FastEmbedDense(config)),
            "sparse": sparse_factory or (lambda: FastEmbedSparse(config)),
            "reranker": reranker_factory or (lambda: FastEmbedReranker(config)),
        }
        self._instances: dict[str, Any] = {}
        self._load_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(config.max_concurrent_inference)
        self._profile: EmbeddingProfile | None = None

    def _get(self, name: str) -> Any:
        with self._load_lock:
            if name not in self._instances:
                try:
                    self._instances[name] = self._factories[name]()
                except SearchServiceError as exc:
                    log_event(logger, "model_load_failed", logging.ERROR, model=name, code=exc.code)
                    raise
                except Exception:
                    log_event(logger, "model_load_failed", logging.ERROR, model=name)
                    raise ModelUnavailable() from None
                log_event(logger, "model_loaded", model=name)
            return self._instances[name]

    @property
    def dense(self) -> DenseEmbedder:
        return self._get("dense")

    @property
    def sparse(self) -> SparseEmbedder:
        return self._get("sparse")

    @property
    def reranker(self) -> Reranker:
        return self._get("reranker")

    def loaded(self) -> list[str]:
        return sorted(self._instances)

    def profile(self) -> EmbeddingProfile:
        if self._profile is None:
            self._profile = compute_profile(self.dense.identity(), self.sparse.identity())
        return self._profile

    def infer(self, operation: Callable[[], T], code: str = "inference_failed") -> T:
        with self._slots:
            try:
                return operation()
            except SearchServiceError:
                raise
            except Exception:
                raise InferenceFailed(code=code) from None


_shared: dict[str, ModelRuntime] = {}
_shared_lock = threading.Lock()


def shared_runtime(config: SearchConfig) -> ModelRuntime:
    """One runtime per process and configuration, so workers/API load models once."""
    key = sha256(stable_json(config.model_dump(mode="json")))
    with _shared_lock:
        if key not in _shared:
            _shared[key] = ModelRuntime(config)
        return _shared[key]


def configured_profile_key(config: SearchConfig, collection_base: str) -> str:
    """Settings-level identity usable without loading models (job registration)."""
    fields = (
        "dense_model",
        "dense_dim",
        "dense_max_tokens",
        "sparse_model",
        "bm25_k",
        "bm25_b",
        "bm25_avg_len",
        "bm25_language",
    )
    data = {name: getattr(config, name) for name in fields}
    data["collection_base"] = collection_base
    data["input_representation"] = INPUT_REPRESENTATION
    return sha256(stable_json(data))


def init_models(config: SearchConfig, include_reranker: bool = True) -> dict[str, Any]:
    """Explicit warm-up: the only path that downloads models unless configuration allows it."""
    dense = FastEmbedDense(config, allow_download=True)
    sparse = FastEmbedSparse(config, allow_download=True)
    validate_dense(dense.embed_query("warm-up"), dense.dim)
    sparse.embed_query("warm-up")
    result: dict[str, Any] = {
        "dense": dense.identity(),
        "sparse": sparse.identity(),
        "embedding_profile": compute_profile(dense.identity(), sparse.identity()).fingerprint,
        "model_cache_dir": str(Path(config.model_cache_dir).resolve()),
    }
    if include_reranker:
        reranker = FastEmbedReranker(config, allow_download=True)
        reranker.rerank("warm-up", ["warm-up"]).scores
        result["reranker"] = reranker.identity()
    return result
