"""Data models for the skill marketplace."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SkillPackageInfo:
    """Information about a skill package available for installation."""

    name: str  # Package name, e.g. "sao-skill-weather"
    skill_name: str  # Skill identifier, e.g. "weather"
    description: str  # One-line description
    version: str  # Latest version
    source: str  # "pypi" | "github" | "catalog"
    install_url: str  # pip install target (package name or git+URL)
    homepage: str = ""  # Project homepage URL
    author: str = ""  # Author name
    stars: int = 0  # GitHub stars (0 for non-GitHub sources)
