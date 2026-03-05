"""Skill auto-discovery and registry.

Scans `app/skills/*.py` for BaseSkill subclasses, imports them dynamically,
and provides `get_skill(name)` / `list_all_skills()`.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

from app.skills.base import BaseSkill, SkillManifest
from app.utils.logger import logger

# Global registry: skill_name -> skill_instance
_registry: dict[str, BaseSkill] = {}


def discover_and_register_skills() -> int:
    """Scan the `app.skills` package, find BaseSkill subclasses, and register them.

    Returns the number of skills registered.
    """
    import app.skills as skills_pkg

    count = 0
    for importer, module_name, is_pkg in pkgutil.iter_modules(skills_pkg.__path__):
        if module_name.startswith("_") or module_name == "base":
            continue

        full_name = f"app.skills.{module_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as e:
            logger.error("Failed to import skill module %s: %s", full_name, e)
            continue

        # Find all BaseSkill subclasses in the module
        for attr_name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, BaseSkill) and obj is not BaseSkill:
                try:
                    instance = obj()
                    name = instance.manifest.name
                    _registry[name] = instance
                    logger.info(
                        "Skill registered: %s (%s) v%s",
                        name,
                        instance.manifest.description,
                        instance.manifest.version,
                    )
                    count += 1
                except Exception as e:
                    logger.error("Failed to instantiate skill %s.%s: %s", full_name, attr_name, e)

    return count


def get_skill(name: str) -> BaseSkill | None:
    """Look up a registered skill by name."""
    return _registry.get(name)


def list_all_skills() -> list[SkillManifest]:
    """Return manifests of all registered skills."""
    return [s.manifest for s in _registry.values()]


def get_skills_description_for_llm() -> str:
    """Build a formatted skill list string for injection into the LLM system prompt."""
    if not _registry:
        return "当前没有可用的技能。"

    lines = []
    for skill in _registry.values():
        m = skill.manifest
        examples = "、".join(f"「{e}」" for e in m.usage_examples[:3])
        lines.append(f"- **{m.name}**: {m.description}")
        if examples:
            lines.append(f"  示例: {examples}")

    return "\n".join(lines)
