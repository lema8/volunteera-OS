"""Model provider registry.

New backends register themselves here (or at runtime via
:func:`register_provider`) and become selectable from configuration without
any change to the agent.
"""

from __future__ import annotations

from typing import Any, Callable

from lema.providers.anthropic import AnthropicProvider
from lema.providers.base import (
    ChatResponse,
    ContextOverflowError,
    Message,
    ModelProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderHealth,
    ProviderRateLimitError,
    Role,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolSpec,
    Usage,
)
from lema.providers.ollama import OllamaProvider
from lema.providers.openai_compat import OpenAICompatibleProvider

ProviderFactory = Callable[[Any], ModelProvider]

_REGISTRY: dict[str, ProviderFactory] = {
    "openai": OpenAICompatibleProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "openrouter": OpenAICompatibleProvider,
    "groq": OpenAICompatibleProvider,
    "together": OpenAICompatibleProvider,
    "llamacpp": OpenAICompatibleProvider,
    "vllm": OpenAICompatibleProvider,
    "lmstudio": OpenAICompatibleProvider,
    "local": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}


def register_provider(kind: str, factory: ProviderFactory) -> None:
    """Register a custom provider adapter at runtime."""
    _REGISTRY[kind.strip().lower()] = factory


def available_providers() -> list[str]:
    return sorted(set(_REGISTRY))


def create_provider(config: Any) -> ModelProvider:
    """Instantiate the adapter named by ``config.kind``."""
    kind = str(getattr(config, "kind", "") or "").strip().lower()
    factory = _REGISTRY.get(kind)
    if factory is None:
        known = ", ".join(available_providers())
        raise ProviderError(f"unknown provider kind {kind!r}. Known kinds: {known}")
    return factory(config)


__all__ = [
    "AnthropicProvider",
    "ChatResponse",
    "ContextOverflowError",
    "Message",
    "ModelProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderAuthError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderHealth",
    "ProviderRateLimitError",
    "Role",
    "StreamEvent",
    "StreamEventType",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "available_providers",
    "create_provider",
    "register_provider",
]
