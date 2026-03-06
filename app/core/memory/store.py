"""SQLite persistence for conversation history and memory entries.

Tables:
- messages:       conversation messages (role, content, timestamps)
- sessions:       session metadata (last_active, compact_summary)
- memory_entries:  long-term memory index (structured, queryable)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.memory.models import MemoryEntry, Message, Session
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")

_DB_PATH = Path("data/memory.db")
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    """Get or create the singleton SQLite connection."""
    global _conn
    if _conn is None:
        raise RuntimeError("Memory store not initialized. Call init_db() first.")
    return _conn


def init_db() -> None:
    """Create tables and initialise the database connection."""
    global _conn
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")

    _conn.executescript(
        """\
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, created_at);

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            compact_summary TEXT,
            model TEXT
        );

        CREATE TABLE IF NOT EXISTS memory_entries (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        );
        """
    )
    _conn.commit()
    logger.info("Memory store initialized: %s", _DB_PATH)


# ═══════════════════════════════════════════════════════════════════════════
# Messages CRUD
# ═══════════════════════════════════════════════════════════════════════════


def save_message(session_id: str, role: str, content: str, metadata: dict | None = None) -> Message:
    """Save a conversation message to SQLite."""
    conn = _get_conn()
    msg_id = uuid.uuid4().hex[:16]
    now = datetime.now(TZ)
    meta_json = json.dumps(metadata or {}, ensure_ascii=False)

    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, session_id, role, content, now.isoformat(), meta_json),
    )

    # Upsert session
    conn.execute(
        """\
        INSERT INTO sessions (id, created_at, last_active, message_count, model)
        VALUES (?, ?, ?, 1, '')
        ON CONFLICT(id) DO UPDATE SET
            last_active = excluded.last_active,
            message_count = message_count + 1
        """,
        (session_id, now.isoformat(), now.isoformat()),
    )
    conn.commit()

    return Message(id=msg_id, session_id=session_id, role=role, content=content, created_at=now, metadata=metadata or {})


def get_history(session_id: str, limit: int = 20) -> list[Message]:
    """Get recent conversation messages for a session."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, session_id, role, content, created_at, metadata FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()

    messages = []
    for row in reversed(rows):  # Reverse to chronological order
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        messages.append(
            Message(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=meta,
            )
        )
    return messages


def clear_history(session_id: str) -> int:
    """Delete all messages for a session. Returns count deleted."""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute(
        "UPDATE sessions SET message_count = 0, compact_summary = NULL WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    return cursor.rowcount


def get_session(session_id: str) -> Session | None:
    """Get session metadata."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return Session(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_active=datetime.fromisoformat(row["last_active"]) if row["last_active"] else datetime.fromisoformat(row["created_at"]),
        message_count=row["message_count"],
        compact_summary=row["compact_summary"],
        model=row["model"] or "",
    )


def update_session_summary(session_id: str, summary: str) -> None:
    """Store a compact summary for a session."""
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET compact_summary = ? WHERE id = ?",
        (summary, session_id),
    )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Memory entries CRUD
# ═══════════════════════════════════════════════════════════════════════════


def save_memory(content: str, category: str, source: str, expires_at: datetime | None = None) -> MemoryEntry:
    """Save a long-term memory entry."""
    conn = _get_conn()
    entry_id = uuid.uuid4().hex[:16]
    now = datetime.now(TZ)

    conn.execute(
        "INSERT INTO memory_entries (id, content, category, source, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (entry_id, content, category, source, now.isoformat(), expires_at.isoformat() if expires_at else None),
    )
    conn.commit()

    return MemoryEntry(id=entry_id, content=content, category=category, source=source, created_at=now, expires_at=expires_at)


def search_memories(query: str, limit: int = 5) -> list[MemoryEntry]:
    """Search memory entries by keyword (simple LIKE match).

    Future: replace with vector/embedding search.
    """
    conn = _get_conn()
    # Split query into words, match any
    words = query.strip().split()
    if not words:
        return list_all_memories(limit)

    conditions = " OR ".join(["content LIKE ?"] * len(words))
    params = [f"%{w}%" for w in words]

    rows = conn.execute(
        f"SELECT * FROM memory_entries WHERE ({conditions}) AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at DESC LIMIT ?",
        (*params, datetime.now(TZ).isoformat(), limit),
    ).fetchall()

    return [_row_to_memory(row) for row in rows]


def list_all_memories(limit: int = 50) -> list[MemoryEntry]:
    """List all active memory entries."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM memory_entries WHERE expires_at IS NULL OR expires_at > ? ORDER BY created_at DESC LIMIT ?",
        (datetime.now(TZ).isoformat(), limit),
    ).fetchall()
    return [_row_to_memory(row) for row in rows]


def delete_memory(entry_id: str) -> bool:
    """Delete a memory entry by ID."""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
    conn.commit()
    return cursor.rowcount > 0


def get_memory_stats() -> dict:
    """Return summary statistics about the memory store."""
    conn = _get_conn()
    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    memory_count = conn.execute(
        "SELECT COUNT(*) FROM memory_entries WHERE expires_at IS NULL OR expires_at > ?",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()[0]

    # Category breakdown
    categories = {}
    for row in conn.execute(
        "SELECT category, COUNT(*) as cnt FROM memory_entries WHERE expires_at IS NULL OR expires_at > ? GROUP BY category",
        (datetime.now(TZ).isoformat(),),
    ).fetchall():
        categories[row[0]] = row[1]

    return {
        "total_messages": msg_count,
        "total_sessions": session_count,
        "total_memories": memory_count,
        "categories": categories,
    }


def _row_to_memory(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        content=row["content"],
        category=row["category"],
        source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
    )
