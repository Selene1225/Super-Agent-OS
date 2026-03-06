"""Long-term memory management — MEMORY.md + daily notes.

Inspired by OpenClaw's memory system:
- MEMORY.md: persistent facts, preferences, decisions (human-readable)
- daily/YYYY-MM-DD.md: daily interaction summaries
- SQLite memory_entries: structured index for search

The LLM decides what to remember via a "memory extraction" prompt after
each conversation turn. Explicit "记住xxx" requests are always honored.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.memory.models import MemoryEntry
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")

_MEMORY_DIR = Path("data/memory")
_MEMORY_FILE = _MEMORY_DIR / "MEMORY.md"
_DAILY_DIR = _MEMORY_DIR / "daily"

# ─── LLM prompt for memory extraction ────────────────────────────────────

MEMORY_EXTRACT_PROMPT = """\
你是记忆管理器。分析下面的对话，判断是否有值得长期记住的信息。

规则：
1. 用户明确说"记住"、"记下"、"别忘了"等时，**必须**提取为记忆
2. 用户透露的偏好、习惯、重要事实也应提取（如"我喜欢xx"、"我在xx公司工作"）
3. 关键决定或承诺也值得记录
4. 如果没有值得记住的内容，返回空数组

请用 JSON 数组格式返回（不要包含其他文字）：
[
  {"content": "记忆内容", "category": "preference|fact|decision|context"}
]

如果没有需要记住的内容，返回：[]

对话内容：
用户: {user_message}
助手: {assistant_reply}
"""


def ensure_dirs() -> None:
    """Create memory directories if they don't exist."""
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)


def read_memory_file() -> str:
    """Read the full content of MEMORY.md."""
    if not _MEMORY_FILE.exists():
        return ""
    return _MEMORY_FILE.read_text(encoding="utf-8")


def append_to_memory_file(content: str, category: str) -> None:
    """Append a memory entry to MEMORY.md in a structured format."""
    ensure_dirs()
    now = datetime.now(TZ)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    # Category emoji mapping
    emoji = {"preference": "💡", "fact": "📌", "decision": "🎯", "context": "📝"}.get(category, "📝")

    # Create file with header if it doesn't exist
    if not _MEMORY_FILE.exists():
        _MEMORY_FILE.write_text(
            "# SAO 长期记忆\n\n> 此文件由 Agent 自动维护，也可手动编辑。\n\n",
            encoding="utf-8",
        )

    with open(_MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {emoji} [{date_str}] {content}\n")

    logger.info("Appended to MEMORY.md: [%s] %s", category, content[:60])


def append_to_daily(content: str) -> None:
    """Append a note to today's daily file."""
    ensure_dirs()
    now = datetime.now(TZ)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    daily_file = _DAILY_DIR / f"{date_str}.md"

    if not daily_file.exists():
        daily_file.write_text(f"# {date_str} 日记\n\n", encoding="utf-8")

    with open(daily_file, "a", encoding="utf-8") as f:
        f.write(f"- [{time_str}] {content}\n")


async def extract_memories(
    user_message: str,
    assistant_reply: str,
    factory,
) -> list[dict]:
    """Use LLM to extract memorable information from a conversation turn.

    Returns list of {"content": str, "category": str} dicts.
    """
    prompt = MEMORY_EXTRACT_PROMPT.format(
        user_message=user_message,
        assistant_reply=assistant_reply[:500],  # Truncate long replies
    )

    try:
        raw = await factory.get_response(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
            enable_thinking=False,
        )
        raw = raw.strip()
        # Strip markdown code fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        memories = json.loads(raw)
        if not isinstance(memories, list):
            return []
        return [m for m in memories if isinstance(m, dict) and "content" in m]
    except Exception as e:
        logger.debug("Memory extraction returned no results: %s", e)
        return []


async def remember(
    content: str,
    category: str = "fact",
    source: str = "user_explicit",
) -> MemoryEntry:
    """Save a memory to both SQLite and MEMORY.md.

    This is the main entry point for persisting a memory.
    """
    from app.core.memory.store import save_memory

    # Write to SQLite
    entry = save_memory(content, category, source)

    # Write to MEMORY.md
    append_to_memory_file(content, category)

    logger.info("Remembered [%s/%s]: %s", category, source, content[:80])
    return entry


async def recall(query: str, limit: int = 5) -> list[MemoryEntry]:
    """Search memories by keyword. Future: vector search."""
    from app.core.memory.store import search_memories
    return search_memories(query, limit)


def get_memory_context() -> str:
    """Build a memory context string to inject into system prompts.

    Reads from MEMORY.md for a human-readable summary.
    """
    content = read_memory_file()
    if not content or content.strip() == "# SAO 长期记忆":
        return ""

    # Truncate if too long (keep last N lines)
    lines = content.strip().split("\n")
    if len(lines) > 50:
        lines = lines[:3] + ["...(更早的记忆已省略)..."] + lines[-40:]

    return "以下是你对用户的长期记忆（务必参考）：\n\n" + "\n".join(lines)
