"""Memory system public API.

Usage:
    from app.core.memory import init_memory, save_message, remember, recall, ...

This module re-exports the key functions from sub-modules so the rest
of the codebase only needs to import from `app.core.memory`.
"""

from app.core.memory.compactor import compact_session  # noqa: F401
from app.core.memory.long_term import (  # noqa: F401
    append_to_daily,
    extract_memories,
    get_memory_context,
    read_memory_file,
    recall,
    remember,
)
from app.core.memory.models import MemoryEntry, Message, Session  # noqa: F401
from app.core.memory.store import (  # noqa: F401
    clear_history,
    delete_memory,
    get_history,
    get_memory_stats,
    get_session,
    init_db,
    list_all_memories,
    save_memory,
    save_message,
)


def init_memory() -> None:
    """Initialise the memory system: create DB tables + ensure directories."""
    from app.core.memory.long_term import ensure_dirs

    init_db()
    ensure_dirs()
