"""Data models for the memory system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Message:
    """A single conversation message stored in SQLite."""

    id: str  # UUID
    session_id: str  # chat_id / open_id
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """Conversation session metadata."""

    id: str  # chat_id / open_id
    created_at: datetime
    last_active: datetime
    message_count: int = 0
    compact_summary: str | None = None
    model: str = ""


@dataclass
class MemoryEntry:
    """A single long-term memory entry."""

    id: str  # UUID
    content: str  # The memory text
    category: str  # "preference" | "fact" | "decision" | "context"
    source: str  # "user_explicit" | "agent_inferred" | "compact"
    created_at: datetime
    expires_at: datetime | None = None


# Valid categories for memory entries
MEMORY_CATEGORIES = ("preference", "fact", "decision", "context")

# Valid sources
MEMORY_SOURCES = ("user_explicit", "agent_inferred", "compact")
