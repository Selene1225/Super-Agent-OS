"""Central Agent — logic dispatcher between LLM and skills.

Phase 1: Pure chat mode — no skill dispatch / code generation.
"""

from collections import defaultdict

from app.core.factory import ModelFactory
from app.utils.logger import logger

# Maximum conversation turns to keep per chat_id
_MAX_HISTORY = 20

_SYSTEM_PROMPT = (
    "你是 Super-Agent-OS (SAO)，一个为用户量身打造的个人 AI 助理。\n"
    "你运行在用户的本地 Docker 容器中，可以通过飞书与用户沟通。\n"
    "请用简洁、专业的中文回答用户的问题。\n"
    "如果用户使用英文提问，请用英文回答。"
)


class Agent:
    """Conversational agent backed by a ModelFactory.

    Maintains per-chat conversation history in memory and delegates
    LLM calls to the ModelFactory.
    """

    def __init__(self, factory: ModelFactory) -> None:
        self._factory = factory
        # chat_id -> list of {"role": ..., "content": ...}
        self._histories: dict[str, list[dict[str, str]]] = defaultdict(list)

    async def process(self, user_message: str, chat_id: str) -> str:
        """Process an incoming user message and return the assistant's reply.

        Args:
            user_message: The raw text from the user.
            chat_id: Unique conversation identifier (Feishu open_id or chat_id).

        Returns:
            The assistant's reply text.
        """
        logger.info("Agent.process — chat_id=%s, msg_len=%d", chat_id, len(user_message))

        history = self._histories[chat_id]

        # Append user message
        history.append({"role": "user", "content": user_message})

        # Trim history to the most recent N turns (user+assistant pairs)
        if len(history) > _MAX_HISTORY * 2:
            history[:] = history[-_MAX_HISTORY * 2 :]

        # Build full message list with system prompt
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + history

        # Call LLM
        try:
            reply = await self._factory.get_response(messages)
        except Exception as e:
            logger.error("Agent.process — LLM call failed: %s", e)
            reply = f"抱歉，AI 模型调用出现问题：{e}"

        # Append assistant reply to history
        history.append({"role": "assistant", "content": reply})

        return reply
