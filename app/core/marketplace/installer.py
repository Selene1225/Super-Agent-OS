"""Skill package installer — pip install + hot-reload.

Handles:
- pip install / uninstall (async subprocess)
- Hot-reload: import newly installed package, find BaseSkill subclass
- Track installed marketplace skills in data/marketplace/installed.json
- Load previously installed skills at startup
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.skills.base import BaseSkill
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")

_INSTALLED_FILE = Path("data/marketplace/installed.json")
_PACKAGES_DIR = Path("packages")  # local skill packages directory


def _ensure_dir() -> None:
    _INSTALLED_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_installed() -> dict:
    """Load the installed marketplace skills registry."""
    _ensure_dir()
    if _INSTALLED_FILE.exists():
        try:
            return json.loads(_INSTALLED_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read installed.json: %s", e)
    return {}


def _save_installed(data: dict) -> None:
    """Persist the installed marketplace skills registry."""
    _ensure_dir()
    _INSTALLED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def package_to_module(package_name: str) -> str:
    """Convert pip package name to Python module name.

    e.g. 'sao-skill-weather' → 'sao_skill_weather'
    """
    return package_name.replace("-", "_")


# ─── Local package scanning ───────────────────────────────────────────────


def scan_local_packages() -> list[dict]:
    """Scan packages/ directory for sao-skill-* packages.

    Each subdirectory that contains a pyproject.toml with name starting
    with 'sao-skill-' is considered a local skill package.

    Returns list of {name, path, description, version}.
    """
    if not _PACKAGES_DIR.is_dir():
        return []

    results = []
    for child in _PACKAGES_DIR.iterdir():
        if not child.is_dir() or not child.name.startswith("sao-skill-"):
            continue
        pyproject = child / "pyproject.toml"
        if not pyproject.exists():
            continue
        try:
            import tomllib
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            project = data.get("project", {})
            name = project.get("name", child.name)
            results.append({
                "name": name,
                "path": str(child.resolve()),
                "description": project.get("description", "(本地技能包)"),
                "version": project.get("version", "0.0.0"),
            })
        except Exception as e:
            logger.debug("Failed to parse %s: %s", pyproject, e)

    return results


def find_local_package(package_name: str) -> str | None:
    """Find a local package path by name.

    Returns the absolute path to the package directory, or None if not found.
    """
    for pkg in scan_local_packages():
        if pkg["name"] == package_name:
            return pkg["path"]
    return None


# ─── pip operations (async subprocess) ────────────────────────────────────


async def pip_install(target: str) -> tuple[bool, str]:
    """Run ``pip install <target>`` asynchronously.

    Returns (success, output_text).
    """
    logger.info("Running: pip install %s", target)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", target, "--quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = (stdout.decode() + stderr.decode()).strip()
    success = proc.returncode == 0
    if not success:
        logger.error("pip install failed (rc=%d): %s", proc.returncode, output)
    return success, output


async def pip_uninstall(package_name: str) -> tuple[bool, str]:
    """Run ``pip uninstall <package> -y`` asynchronously."""
    logger.info("Running: pip uninstall %s -y", package_name)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "uninstall", package_name, "-y",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = (stdout.decode() + stderr.decode()).strip()
    success = proc.returncode == 0
    if not success:
        logger.error("pip uninstall failed (rc=%d): %s", proc.returncode, output)
    return success, output


# ─── Hot-reload ───────────────────────────────────────────────────────────


def hot_reload_skill(module_name: str) -> BaseSkill | None:
    """Import a newly installed package and discover its BaseSkill subclass.

    Scans the module (and a ``skill`` submodule if present) for any class
    that inherits from BaseSkill.
    """
    try:
        # Evict cached modules to force fresh import
        to_remove = [k for k in sys.modules if k == module_name or k.startswith(f"{module_name}.")]
        for k in to_remove:
            del sys.modules[k]

        mod = importlib.import_module(module_name)

        # Scan top-level for BaseSkill subclass
        skill = _find_skill_class(mod, module_name)
        if skill:
            return skill

        # Try `.skill` submodule (common convention)
        try:
            sub_mod = importlib.import_module(f"{module_name}.skill")
            skill = _find_skill_class(sub_mod, module_name)
            if skill:
                return skill
        except ImportError:
            pass

        logger.warning("No BaseSkill subclass found in %s", module_name)
        return None

    except Exception as e:
        logger.error("Hot-reload failed for %s: %s", module_name, e, exc_info=True)
        return None


def _find_skill_class(mod, module_prefix: str) -> BaseSkill | None:
    """Scan a module for BaseSkill subclasses and instantiate the first one found."""
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if (
            issubclass(obj, BaseSkill)
            and obj is not BaseSkill
            and obj.__module__.startswith(module_prefix)
        ):
            try:
                return obj()
            except Exception as e:
                logger.error("Failed to instantiate %s.%s: %s", mod.__name__, name, e)
    return None


# ─── Install / Uninstall workflows ───────────────────────────────────────


async def install_skill(
    install_target: str,
    package_name: str,
    source: str = "pypi",
) -> tuple[bool, str, BaseSkill | None]:
    """Install a skill package and hot-reload it.

    Args:
        install_target: pip install target (package name or git+URL).
        package_name: canonical package name (e.g. 'sao-skill-weather').
        source: 'pypi' or 'github'.

    Returns:
        (success, user_friendly_message, skill_instance_or_None)
    """
    logger.info("Installing skill: %s (source=%s, target=%s)", package_name, source, install_target)

    # pip install
    ok, output = await pip_install(install_target)
    if not ok:
        return False, f"❌ pip install 失败:\n{output[:500]}", None

    # Hot-reload
    module_name = package_to_module(package_name)
    skill = hot_reload_skill(module_name)
    if skill is None:
        return False, f"⚠️ 包已安装到 Python 环境，但未找到 BaseSkill 子类（模块: {module_name}）。\n请确认包遵循 SAO 技能包规范。", None

    # Track in installed.json
    installed = _load_installed()
    installed[package_name] = {
        "installed_at": datetime.now(TZ).isoformat(),
        "version": skill.manifest.version,
        "module_name": module_name,
        "skill_name": skill.manifest.name,
        "source": source,
    }
    _save_installed(installed)

    msg = (
        f"✅ 技能安装成功！\n\n"
        f"📦 {package_name}\n"
        f"🔧 技能名: {skill.manifest.name} v{skill.manifest.version}\n"
        f"📝 {skill.manifest.description}\n\n"
        f"已自动加载，可以直接使用。"
    )
    logger.info("Skill installed: %s → %s v%s", package_name, skill.manifest.name, skill.manifest.version)
    return True, msg, skill


async def uninstall_skill(package_name: str) -> tuple[bool, str, str]:
    """Uninstall a skill package.

    Returns:
        (success, user_friendly_message, skill_name_to_unregister)
    """
    installed = _load_installed()
    if package_name not in installed:
        return False, f"❌ '{package_name}' 不在已安装的市场技能列表中。", ""

    skill_name = installed[package_name].get("skill_name", "")

    # pip uninstall
    ok, output = await pip_uninstall(package_name)
    if not ok:
        return False, f"❌ pip uninstall 失败:\n{output[:500]}", ""

    # Remove from tracking
    del installed[package_name]
    _save_installed(installed)

    # Purge from sys.modules
    module_name = package_to_module(package_name)
    to_remove = [k for k in sys.modules if k == module_name or k.startswith(f"{module_name}.")]
    for k in to_remove:
        del sys.modules[k]

    msg = f"✅ 已卸载 {package_name}（技能: {skill_name}）"
    logger.info("Skill uninstalled: %s (%s)", package_name, skill_name)
    return True, msg, skill_name


# ─── Startup loader ──────────────────────────────────────────────────────


def get_installed_packages() -> dict:
    """Return the installed marketplace skills metadata."""
    return _load_installed()


def load_marketplace_skills() -> dict[str, BaseSkill]:
    """Load all previously installed marketplace skills.

    Called at startup to re-register skills from a previous session.
    Returns a dict of skill_name → BaseSkill instance.
    """
    installed = _load_installed()
    skills: dict[str, BaseSkill] = {}

    for pkg_name, info in installed.items():
        module_name = info.get("module_name", package_to_module(pkg_name))
        try:
            skill = hot_reload_skill(module_name)
            if skill:
                skills[skill.manifest.name] = skill
                logger.info("Loaded marketplace skill: %s (from %s)", skill.manifest.name, pkg_name)
            else:
                logger.warning("Could not load marketplace skill: %s (module: %s)", pkg_name, module_name)
        except Exception as e:
            logger.warning("Failed to load marketplace skill %s: %s", pkg_name, e)

    return skills
