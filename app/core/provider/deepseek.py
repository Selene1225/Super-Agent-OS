"""DeepSeek provider via OpenAI-compatible endpoint."""

from openai import AsyncOpenAI

from app.core.provider.base import BaseLLMProvider
from app.utils.logger import logger


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek LLM provider using the OpenAI-compatible API."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        logger.info("DeepSeekProvider initialized — model=%s, base_url=%s", model, base_url)

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool | None = None,
    ) -> str:
        logger.debug("DeepSeekProvider.chat — %d messages, model=%s", len(messages), self._model)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        logger.debug("DeepSeekProvider.chat — reply length=%d", len(content))
        return content
