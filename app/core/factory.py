"""Model factory — instantiates and routes requests to LLM providers.

Phase 1: Minimal version — only Qwen, no fallback logic.
"""

from app.core.provider.base import BaseLLMProvider
from app.core.provider.qwen import QwenProvider
from app.utils.config import Settings
from app.utils.logger import logger


class ModelFactory:
    """Create and manage LLM provider instances."""

    def __init__(self, settings: Settings) -> None:
        self._providers: list[BaseLLMProvider] = []
        self._primary: BaseLLMProvider | None = None
        self._init_providers(settings)

    def _init_providers(self, settings: Settings) -> None:
        """Instantiate available providers based on configuration."""
        if settings.qwen_api_key:
            provider = QwenProvider(
                api_key=settings.qwen_api_key,
                base_url=settings.qwen_base_url,
                model=settings.qwen_model,
            )
            self._providers.append(provider)
            self._primary = provider
            logger.info("ModelFactory — primary provider: %s", provider.model_name)

        if not self._primary:
            logger.warning("No LLM provider configured. Set at least QWEN_API_KEY in .env")

    async def get_response(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Get a chat completion from the primary model.

        Phase 1: No fallback — raises on failure.
        Phase 2 will add automatic degradation to backup models.
        """
        if self._primary is None:
            raise RuntimeError("No LLM provider configured. Set API keys in .env")
        return await self._primary.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def available_models(self) -> list[str]:
        """Return names of all configured providers."""
        return [p.model_name for p in self._providers]
