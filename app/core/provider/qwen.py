"""Qwen (通义千问) provider via DashScope OpenAI-compatible endpoint."""

from openai import AsyncOpenAI

from app.core.provider.base import BaseLLMProvider
from app.utils.logger import logger


class QwenProvider(BaseLLMProvider):
    """Qwen LLM provider using the OpenAI-compatible API."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        # qwen3 / qwen3.5 models support thinking mode control
        self._supports_thinking = "qwen3" in model.lower()
        logger.info(
            "QwenProvider initialized — model=%s, base_url=%s, thinking_capable=%s",
            model, base_url, self._supports_thinking,
        )

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
        logger.debug("QwenProvider.chat — %d messages, model=%s", len(messages), self._model)

        # Build extra_body for qwen3 thinking mode control
        extra_body: dict | None = None
        if self._supports_thinking and enable_thinking is not None:
            extra_body = {"enable_thinking": enable_thinking}
            if not enable_thinking:
                logger.debug("QwenProvider: thinking mode disabled for this call")

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **({"extra_body": extra_body} if extra_body else {}),
        )
        content = response.choices[0].message.content or ""
        logger.debug("QwenProvider.chat — reply length=%d", len(content))
        return content
