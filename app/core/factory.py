"""Model factory — instantiates and routes requests to LLM providers.

Phase 2: Full version with multi-model support and automatic fallback.
On 429 / Timeout / APIError the factory transparently tries the next
available provider (up to 2 fallback attempts).
"""

from __future__ import annotations

from openai import APIError, APITimeoutError, RateLimitError

from app.core.provider.base import BaseLLMProvider
from app.core.provider.deepseek import DeepSeekProvider
from app.core.provider.doubao import DoubaoProvider
from app.core.provider.qwen import QwenProvider
from app.utils.config import Settings
from app.utils.logger import logger

# Maximum number of fallback retries (excluding the primary attempt)
_MAX_FALLBACK_RETRIES = 2

# Exception types that trigger automatic fallback
_RETRIABLE_ERRORS = (RateLimitError, APITimeoutError, APIError)


class ModelFactory:
    """Create and manage LLM provider instances with automatic fallback."""

    def __init__(self, settings: Settings) -> None:
        self._providers: list[BaseLLMProvider] = []
        self._primary: BaseLLMProvider | None = None
        self._init_providers(settings)

    # ------------------------------------------------------------------ #
    # Initialisation
    # ------------------------------------------------------------------ #

    def _init_providers(self, settings: Settings) -> None:
        """Instantiate all available providers based on configuration."""
        provider_map: dict[str, BaseLLMProvider | None] = {}

        # Qwen
        if settings.qwen_api_key:
            provider_map["qwen"] = QwenProvider(
                api_key=settings.qwen_api_key,
                base_url=settings.qwen_base_url,
                model=settings.qwen_model,
            )

        # DeepSeek
        if settings.deepseek_api_key:
            provider_map["deepseek"] = DeepSeekProvider(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
            )

        # Doubao
        if settings.doubao_api_key:
            provider_map["doubao"] = DoubaoProvider(
                api_key=settings.doubao_api_key,
                base_url=settings.doubao_base_url,
                model=settings.doubao_model,
            )

        # Set primary provider from config; fall back to first available
        primary_key = settings.primary_model.lower()
        if primary_key in provider_map:
            self._primary = provider_map[primary_key]
        elif provider_map:
            first_key = next(iter(provider_map))
            self._primary = provider_map[first_key]
            logger.warning(
                "PRIMARY_MODEL='%s' not available, falling back to '%s'",
                settings.primary_model,
                first_key,
            )

        # Build ordered provider list: primary first, then the rest
        if self._primary is not None:
            self._providers.append(self._primary)
            for key, prov in provider_map.items():
                if prov is not self._primary:
                    self._providers.append(prov)

        if self._primary:
            logger.info("ModelFactory — primary provider: %s", self._primary.model_name)
        else:
            logger.warning("No LLM provider configured. Set at least one API key in .env")

        logger.info("Available models: %s", self.available_models())

    # ------------------------------------------------------------------ #
    # Chat completion with automatic fallback
    # ------------------------------------------------------------------ #

    async def get_response(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool | None = None,
    ) -> str:
        """Get a chat completion, with automatic fallback on failure.

        Tries the primary provider first. If it fails with a retriable
        error (429, timeout, or general API error), the factory moves on
        to the next available provider, up to _MAX_FALLBACK_RETRIES times.
        """
        if not self._providers:
            raise RuntimeError("No LLM provider configured. Set API keys in .env")

        last_error: Exception | None = None
        attempts = 0

        for provider in self._providers:
            if attempts > _MAX_FALLBACK_RETRIES:
                break
            try:
                reply = await provider.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                )
                if attempts > 0:
                    logger.info(
                        "Fallback succeeded — answered by %s after %d attempt(s)",
                        provider.model_name,
                        attempts,
                    )
                return reply
            except _RETRIABLE_ERRORS as exc:
                attempts += 1
                last_error = exc
                logger.warning(
                    "Provider %s failed (%s: %s) — trying next provider (%d/%d)",
                    provider.model_name,
                    type(exc).__name__,
                    exc,
                    attempts,
                    _MAX_FALLBACK_RETRIES,
                )
            except Exception as exc:
                # Non-retriable errors bubble up immediately
                logger.error("Provider %s non-retriable error: %s", provider.model_name, exc)
                raise

        # All providers failed
        raise RuntimeError(
            f"All LLM providers failed after {attempts} attempt(s). Last error: {last_error}"
        )

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @property
    def primary_model_name(self) -> str | None:
        """Return the name of the current primary model."""
        return self._primary.model_name if self._primary else None

    def available_models(self) -> list[str]:
        """Return names of all configured providers."""
        return [p.model_name for p in self._providers]

    def get_provider(self, model_name: str) -> BaseLLMProvider | None:
        """Look up a provider by model name (case-insensitive partial match)."""
        target = model_name.lower()
        for p in self._providers:
            if target in p.model_name.lower():
                return p
        return None

    def switch_primary(self, model_name: str) -> bool:
        """Switch the primary provider at runtime. Returns True on success."""
        provider = self.get_provider(model_name)
        if provider is None:
            return False
        self._primary = provider
        # Reorder: primary first, then the rest
        self._providers = [provider] + [p for p in self._providers if p is not provider]
        logger.info("Switched primary model to: %s", provider.model_name)
        return True
