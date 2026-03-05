"""Abstract base class for all SAO skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillManifest:
    """Metadata describing a skill — used for discovery and LLM routing."""

    name: str  # unique identifier, e.g. "reminder"
    description: str  # one-line Chinese description shown to LLM
    usage_examples: list[str] = field(default_factory=list)  # example user messages
    version: str = "0.1.0"
    # Optional cron schedule (APScheduler format) for periodic skills
    schedule: str | None = None


class BaseSkill(ABC):
    """Every skill must subclass this and implement manifest + run."""

    @property
    @abstractmethod
    def manifest(self) -> SkillManifest:
        """Return the skill's metadata manifest."""
        ...

    @abstractmethod
    async def run(self, params: dict[str, Any], context: "SkillContext") -> str:
        """Execute the skill and return a human-readable result.

        Args:
            params: Arbitrary parameters extracted from the user message.
                    Each skill defines its own expected params schema.
            context: Execution context providing access to factory, user info, etc.

        Returns:
            A string to be sent back to the user via Feishu.
        """
        ...


@dataclass
class SkillContext:
    """Runtime context passed to every skill execution."""

    user_message: str  # the original user message
    chat_id: str  # Feishu open_id or chat_id
    factory: Any  # ModelFactory instance for LLM calls
