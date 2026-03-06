"""Abstract base class for all LLM providers."""

from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """Unified interface that every model provider must implement."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the human-readable model identifier, e.g. 'qwen-plus'."""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool | None = None,
    ) -> str:
        """Send a chat-completion request and return the assistant's reply text.

        Args:
            messages: OpenAI-style message list [{"role": "...", "content": "..."}].
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.
            enable_thinking: For models that support thinking mode (e.g. qwen3).
                None = use model default; False = disable thinking for faster response.

        Returns:
            The assistant's reply as a plain string.
        """
        ...
