"""Skill Marketplace — search, install, and manage SAO skill packages.

Public API re-exports for convenience.
"""

from app.core.marketplace.models import SkillPackageInfo
from app.core.marketplace.search import search, search_github, search_local, search_pypi, normalise_query
from app.core.marketplace.installer import (
    find_local_package,
    get_installed_packages,
    hot_reload_skill,
    install_skill,
    load_marketplace_skills,
    package_to_module,
    scan_local_packages,
    uninstall_skill,
)

__all__ = [
    "SkillPackageInfo",
    "normalise_query",
    "search",
    "search_github",
    "search_local",
    "search_pypi",
    "find_local_package",
    "scan_local_packages",
    "install_skill",
    "uninstall_skill",
    "load_marketplace_skills",
    "get_installed_packages",
    "hot_reload_skill",
    "package_to_module",
]
