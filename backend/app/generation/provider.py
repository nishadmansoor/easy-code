"""LLM provider abstraction.

The default provider is a locally hosted model via Ollama; the project never
requires a paid API key. An OpenAI-compatible provider is available for users
who want it, and reads its credentials from the environment only.

When no model backend is reachable, :class:`ExtractiveProvider` produces a
useful (if plainer) answer directly from the retrieved evidence, so the system
degrades instead of failing.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from backend.app.config.settings import settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when a configured provider cannot be reached."""


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> LLMResponse: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @property
    def model_name(self) -> str:
        return "unknown"


class OllamaProvider(LLMProvider):
    """Local inference through an Ollama server."""

    name = "ollama"

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.host = (host or settings.ollama_host).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.llm_timeout_seconds

    @property
    def model_name(self) -> str:
        return self.model

    def is_available(self) -> bool:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=3.0)
            response.raise_for_status()
        except (httpx.HTTPError, OSError):
            return False

        models = {entry.get("name", "") for entry in response.json().get("models", [])}
        if not models:
            return False
        # Ollama reports "qwen2.5:7b"; accept a bare name as matching its :latest tag.
        return self.model in models or f"{self.model}:latest" in models

    def generate(self, prompt: str, system: str | None = None) -> LLMResponse:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": settings.llm_temperature,
                "num_predict": settings.llm_max_tokens,
            },
        }
        if system:
            payload["system"] = system

        try:
            response = httpx.post(
                f"{self.host}/api/generate", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama request failed: {exc}") from exc

        return LLMResponse(
            text=response.json().get("response", "").strip(),
            model=self.model,
            provider=self.name,
        )


class OpenAICompatibleProvider(LLMProvider):
    """Optional hosted provider. Credentials come from the environment only."""

    name = "openai"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.base_url = (base_url or settings.openai_base_url).rstrip("/")
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model

    @property
    def model_name(self) -> str:
        return self.model

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, system: str | None = None) -> LLMResponse:
        if not self.api_key:
            raise LLMUnavailableError("No API key configured for the OpenAI-compatible provider")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": settings.llm_temperature,
                    "max_tokens": settings.llm_max_tokens,
                },
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Provider request failed: {exc}") from exc

        text = response.json()["choices"][0]["message"]["content"].strip()
        return LLMResponse(text=text, model=self.model, provider=self.name)


class ExtractiveProvider(LLMProvider):
    """Fallback that summarises the retrieved evidence without a model.

    It never writes a claim of its own: the output is a list of the locations
    that were retrieved, which keeps the system honest when no LLM is present.
    """

    name = "extractive"

    def is_available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "none (extractive fallback)"

    def generate(self, prompt: str, system: str | None = None) -> LLMResponse:
        locations = _extract_locations(prompt)
        if not locations:
            text = (
                "I couldn't find enough evidence in the indexed repository to determine this."
            )
        else:
            listed = "\n".join(f"- {location}" for location in locations[:10])
            text = (
                "No language model is configured, so this answer lists the repository "
                "locations that matched the question instead of explaining them.\n\n"
                f"Most relevant locations:\n{listed}\n\n"
                "Start an Ollama server (see the README) for full explanations."
            )
        return LLMResponse(text=text, model=self.model_name, provider=self.name)


def _extract_locations(prompt: str) -> list[str]:
    import re

    pattern = re.compile(r"Location:\s*(\S+:\d+-\d+)")
    seen: list[str] = []
    for match in pattern.finditer(prompt):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


_provider: LLMProvider | None = None


def build_provider(name: str | None = None) -> LLMProvider:
    """Construct the configured provider, falling back when it is unreachable."""
    choice = (name or settings.llm_provider).lower()

    if choice == "extractive":
        return ExtractiveProvider()

    candidate: LLMProvider
    if choice == "openai":
        candidate = OpenAICompatibleProvider()
    else:
        candidate = OllamaProvider()

    if candidate.is_available():
        return candidate

    logger.warning(
        "LLM provider %r is not available; falling back to extractive answers", choice
    )
    return ExtractiveProvider()


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Override the process-wide provider (used by tests and benchmarks)."""
    global _provider
    _provider = provider
