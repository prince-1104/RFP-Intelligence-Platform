"""Injectable language-model boundary. Live calls use HTTP; tests pass a fake.

No agent framework is imported. httpx is already a project dependency, and the
orchestrator only needs one structured completion at a time.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from rfp.agents.errors import ModelError
from rfp.agents.schemas import TokenUsage

LLM_KEYS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_OUTPUT_TOKENS",
)


@dataclass(frozen=True)
class ModelRequest:
    purpose: str
    system: str
    user: str


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float


class LanguageModel(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_seconds: float = 60
    max_output_tokens: int = 2500


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in LLM_KEYS:
            found[key] = value.strip().strip('"').strip("'")
    return found


def load_llm_config(env_file: Path | None = Path(".env")) -> LLMConfig:
    """Environment variables win over the private env file. The key is never logged."""
    file_values = _dotenv_values(env_file) if env_file is not None else {}
    values = {key: os.environ.get(key, file_values.get(key, "")) for key in LLM_KEYS}
    timeout = float(values["LLM_TIMEOUT_SECONDS"] or 60)
    max_tokens = int(values["LLM_MAX_OUTPUT_TOKENS"] or 2500)
    return LLMConfig(
        provider=values["LLM_PROVIDER"].strip().lower(),
        model=values["LLM_MODEL"].strip(),
        api_key=values["LLM_API_KEY"].strip(),
        base_url=values["LLM_BASE_URL"].strip().rstrip("/"),
        timeout_seconds=timeout,
        max_output_tokens=max_tokens,
    )


def build_live_model(config: LLMConfig | None = None) -> "OpenAICompatibleModel":
    loaded = config or load_llm_config()
    if (
        loaded.provider not in {"openai", "openai_compatible"}
        or not loaded.model
        or not loaded.api_key
    ):
        raise ModelError(
            "llm_not_configured",
            "Set LLM_PROVIDER, LLM_MODEL and LLM_API_KEY. Extraction will not invent answers.",
        )
    base = loaded.base_url
    if loaded.provider == "openai" and not base:
        base = "https://api.openai.com/v1"
    if not base:
        raise ModelError(
            "llm_not_configured",
            "LLM_BASE_URL is required for an OpenAI-compatible provider.",
        )
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ModelError(
            "llm_not_configured", "LLM_BASE_URL must be an HTTPS URL without credentials."
        )
    return OpenAICompatibleModel(
        provider=loaded.provider,
        model=loaded.model,
        api_key=loaded.api_key,
        base_url=base,
        timeout_seconds=loaded.timeout_seconds,
        max_output_tokens=loaded.max_output_tokens,
    )


class OpenAICompatibleModel:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ):
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "max_tokens": self.max_output_tokens,
                    "messages": [
                        {"role": "system", "content": request.system},
                        {"role": "user", "content": request.user},
                    ],
                },
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ModelError("llm_timeout", "The language model timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelError("llm_provider_error", "The language model request failed") from exc
        latency = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            raise ModelError(
                "llm_provider_error",
                f"The language model returned HTTP {response.status_code}",
            )
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                "llm_malformed", "The language model response was not usable JSON"
            ) from exc
        if not isinstance(text, str):
            raise ModelError("llm_malformed", "The language model response did not contain text")
        usage = body.get("usage") if isinstance(body, dict) else None
        input_tokens = output_tokens = None
        if isinstance(usage, dict):
            if isinstance(usage.get("prompt_tokens"), int):
                input_tokens = usage["prompt_tokens"]
            if isinstance(usage.get("completion_tokens"), int):
                output_tokens = usage["completion_tokens"]
        return ModelResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
        )


def account(tokens: TokenUsage, response: ModelResponse) -> None:
    tokens.calls += 1
    if response.input_tokens is not None:
        tokens.input_tokens += response.input_tokens
    if response.output_tokens is not None:
        tokens.output_tokens += response.output_tokens


def parse_json_object(text: str) -> dict:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(payload, strict=False)
    except json.JSONDecodeError as exc:
        start, end = payload.find("{"), payload.rfind("}")
        if start < 0 or end <= start:
            raise ModelError("llm_malformed", "Model response was not JSON") from exc
        try:
            value = json.loads(payload[start : end + 1], strict=False)
        except json.JSONDecodeError as inner:
            raise ModelError("llm_malformed", "Model response was not JSON") from inner
    if not isinstance(value, dict):
        raise ModelError("llm_malformed", "Model response was not a JSON object")
    return value
