"""Context compaction — summarise conversation history via LLM.

Inspired by OpenClaw's /compact command:
1. Before compacting, flush important memories to MEMORY.md
2. Summarise the conversation into a compact paragraph
3. Replace full history with the summary
4. Store summary in session metadata
"""

from __future__ import annotations

from app.core.memory.models import Message
from app.utils.logger import logger

COMPACT_PROMPT = """\
请将以下对话历史压缩为一段简洁的摘要（不超过 300 字）。
保留：关键话题、用户的请求和偏好、重要结论、待办事项。
丢弃：寒暄、重复内容、已完成的事项细节。

对话历史：
{history_text}

请直接输出摘要文本，不要加标题或格式。
"""

MEMORY_FLUSH_PROMPT = """\
在压缩对话之前，请检查以下对话是否包含**值得长期记住**的信息。
仅提取以下类型：
- 用户的偏好/习惯（如"我喜欢xx"）
- 重要事实（如"我在xx公司工作"）
- 关键决定或承诺

用 JSON 数组返回（无其他文字）：
[{{"content": "记忆内容", "category": "preference|fact|decision"}}]
如果没有，返回：[]

对话历史：
{history_text}
"""


async def compact_session(
    session_id: str,
    factory,
) -> str:
    """Compact a session's history into a summary.

    Steps:
    1. Read full history from SQLite
    2. Flush any important memories to long_term before discarding
    3. LLM-summarise the history
    4. Clear old messages, save summary
    5. Return the summary text

    Returns the compact summary.
    """
    import json

    from app.core.memory.long_term import remember
    from app.core.memory.store import clear_history, get_history, save_message, update_session_summary

    messages = get_history(session_id, limit=100)
    if len(messages) < 4:
        return "对话太短，无需压缩。"

    history_text = _format_history(messages)

    # Step 1: Flush important memories before compacting
    try:
        flush_prompt = MEMORY_FLUSH_PROMPT.format(history_text=history_text[:3000])
        raw = await factory.get_response(
            [{"role": "user", "content": flush_prompt}],
            temperature=0.1,
            max_tokens=400,
            enable_thinking=False,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        flush_items = json.loads(raw)
        if isinstance(flush_items, list):
            for item in flush_items:
                if isinstance(item, dict) and "content" in item:
                    await remember(
                        content=item["content"],
                        category=item.get("category", "fact"),
                        source="compact",
                    )
            if flush_items:
                logger.info("Compact flush: saved %d memories before compaction", len(flush_items))
    except Exception as e:
        logger.debug("Compact memory flush skipped: %s", e)

    # Step 2: LLM summarise
    compact_prompt = COMPACT_PROMPT.format(history_text=history_text[:4000])
    try:
        summary = await factory.get_response(
            [{"role": "user", "content": compact_prompt}],
            temperature=0.3,
            max_tokens=500,
            enable_thinking=False,
        )
        summary = summary.strip()
    except Exception as e:
        logger.error("Compact LLM call failed: %s", e)
        return f"压缩失败: {e}"

    # Step 3: Clear old messages, save summary as system message
    deleted = clear_history(session_id)
    update_session_summary(session_id, summary)

    # Save the summary as a system message so future conversations have context
    save_message(session_id, "system", f"[对话摘要] {summary}")

    logger.info("Compacted session %s: %d messages → summary (%d chars)", session_id, deleted, len(summary))
    return summary


def _format_history(messages: list[Message]) -> str:
    """Format messages into a readable text block for LLM."""
    lines = []
    for msg in messages:
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(msg.role, msg.role)
        lines.append(f"{role_label}: {msg.content}")
    return "\n".join(lines)
