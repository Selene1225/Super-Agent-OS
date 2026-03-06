"""MarketplaceSkill — natural-language interface to the SAO skill marketplace.

Supports actions:
- search: Search for skill packages on GitHub / PyPI
- install: Install a skill package and hot-reload it
- remove: Uninstall a skill package
- list: Show installed marketplace skills
"""

from __future__ import annotations

from typing import Any

from app.core.marketplace import (
    SkillPackageInfo,
    get_installed_packages,
    install_skill,
    normalise_query,
    search,
    uninstall_skill,
)
from app.core.marketplace.installer import find_local_package
from app.skills.base import BaseSkill, SkillContext, SkillManifest
from app.utils.logger import logger


class MarketplaceSkill(BaseSkill):
    """Skill that manages the SAO skill marketplace."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="marketplace",
            description="搜索、安装、卸载技能包（技能市场）",
            usage_examples=[
                "帮我搜索天气技能",
                "有什么可用的技能包",
                "安装 sao-skill-weather",
                "卸载天气技能",
                "查看已安装的市场技能",
            ],
            version="0.1.0",
        )

    async def run(self, params: dict[str, Any], context: SkillContext) -> str:
        action = params.get("action", "search")
        if action == "search":
            return await self._search(params, context)
        elif action == "install":
            return await self._install(params, context)
        elif action == "remove":
            return await self._remove(params, context)
        elif action == "list":
            return self._list_installed()
        else:
            # Default to search
            return await self._search(params, context)

    # ─── Search ───────────────────────────────────────────

    async def _search(self, params: dict[str, Any], context: SkillContext) -> str:
        query = params.get("query", "").strip()
        if not query:
            # Try to extract query from user message
            query = self._extract_query(context.user_message)
        if not query:
            return (
                "🔍 请告诉我你想搜索什么类型的技能。\n\n"
                "示例：\n"
                "• 帮我搜索天气技能\n"
                "• /market search weather\n"
                "• 有没有翻译技能"
            )

        results = await search(query)
        return self._format_search_results(query, results)

    # ─── Install ──────────────────────────────────────────

    async def _install(self, params: dict[str, Any], context: SkillContext) -> str:
        name = params.get("name", "").strip()
        if not name:
            name = self._extract_package_name(context.user_message)
        if not name:
            return (
                "📦 请指定要安装的技能包名称。\n\n"
                "用法: /market install <包名>\n"
                "示例: /market install sao-skill-weather\n\n"
                "💡 先用「搜索」找到技能包名称。"
            )

        # ─── Translate Chinese name to English ───
        # e.g. "天气" → "weather", "翻译" → "translate"
        original_name = name
        if not name.isascii():
            candidates = normalise_query(name)
            # Pick the first English candidate (skip the original Chinese)
            for c in candidates:
                if c.isascii() and c != name:
                    logger.info("Translated skill name: '%s' → '%s'", name, c)
                    name = c
                    break

        # Normalise to sao-skill-* format
        if not name.startswith("sao-skill-"):
            name = f"sao-skill-{name}"

        # Determine install target and source
        install_target = name
        source = "pypi"

        # If it looks like a git URL, install from GitHub
        if name.startswith("git+") or name.startswith("http"):
            install_target = name
            source = "github"
            pkg = name.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            name = pkg
        else:
            # ─── 1. Check local packages/ directory first ───
            local_path = find_local_package(name)
            if local_path:
                install_target = local_path
                source = "local"
                logger.info("Found local package: %s → %s", name, local_path)
            else:
                # ─── 2. Auto-search GitHub + PyPI ───
                # The package isn't local; search online before blind pip install.
                logger.info("Package '%s' not found locally, searching online...", name)
                search_kw = original_name if not original_name.isascii() else name.removeprefix("sao-skill-")
                results = await search(search_kw)
                if results:
                    best = results[0]
                    install_target = best.install_url or best.name
                    source = best.source
                    name = best.name
                    logger.info("Auto-resolved: %s → %s (source=%s)", search_kw, install_target, source)

        ok, msg, skill = await install_skill(install_target, name, source)

        # Hot-register the skill into the running Agent
        if ok and skill and context.agent:
            context.agent.register_new_skill(skill)

        return msg

    # ─── Remove ───────────────────────────────────────────

    async def _remove(self, params: dict[str, Any], context: SkillContext) -> str:
        name = params.get("name", "").strip()
        if not name:
            name = self._extract_package_name(context.user_message)
        if not name:
            return "请指定要卸载的技能包名称。\n用法: /market remove <包名>"

        if not name.startswith("sao-skill-"):
            name = f"sao-skill-{name}"

        ok, msg, skill_name = await uninstall_skill(name)

        # Unregister from the running Agent
        if ok and skill_name and context.agent:
            context.agent.unregister_skill(skill_name)

        return msg

    # ─── List installed ───────────────────────────────────

    def _list_installed(self) -> str:
        installed = get_installed_packages()
        if not installed:
            return (
                "📦 当前没有通过技能市场安装的技能。\n\n"
                "💡 试试搜索可用技能: /market search <关键词>"
            )

        lines = ["📦 **已安装的市场技能**", ""]
        for pkg_name, info in installed.items():
            skill_name = info.get("skill_name", "?")
            version = info.get("version", "?")
            source = info.get("source", "?")
            installed_at = info.get("installed_at", "?")[:10]
            lines.append(f"  • **{skill_name}** ({pkg_name}) v{version}")
            lines.append(f"    来源: {source} | 安装日期: {installed_at}")

        lines.append("")
        lines.append(f"共 {len(installed)} 个市场技能")
        return "\n".join(lines)

    # ─── Helpers ──────────────────────────────────────────

    @staticmethod
    def _format_search_results(query: str, results: list[SkillPackageInfo]) -> str:
        if not results:
            return (
                f"🔍 搜索「{query}」 的技能包...\n\n"
                f"暂未在 PyPI 和 GitHub 上找到匹配的 sao-skill-* 技能包。\n\n"
                f"💡 提示：\n"
                f"• SAO 技能市场还在成长中\n"
                f"• 你可以自己开发技能：创建名为 sao-skill-{{name}} 的 Python 包\n"
                f"• 技能包需要包含 BaseSkill 子类\n"
                f"• 发布到 PyPI 或 GitHub 后，其他用户也能搜索到"
            )

        lines = [f"🔍 搜索「{query}」找到 {len(results)} 个技能包：", ""]
        for i, pkg in enumerate(results, 1):
            source_tag = f"[{pkg.source.upper()}]"
            stars = f" ⭐{pkg.stars}" if pkg.stars else ""
            lines.append(f"  {i}. **{pkg.name}** v{pkg.version} {source_tag}{stars}")
            lines.append(f"     {pkg.description}")
            if pkg.author:
                lines.append(f"     作者: {pkg.author}")

        lines.append("")
        lines.append("安装命令: /market install <包名>")
        lines.append("例如: /market install " + results[0].name)
        return "\n".join(lines)

    @staticmethod
    def _extract_query(user_message: str) -> str:
        """Try to extract search query from natural language."""
        import re

        # "搜索XX技能", "找XX技能", "有没有XX技能包"
        for pat in [
            r"搜索(.+?)技能",
            r"找(.+?)技能",
            r"有.*?(.+?)技能",
            r"查(.+?)技能",
            r"搜(.+?)的?技能",
        ]:
            m = re.search(pat, user_message)
            if m:
                q = m.group(1).strip()
                # Remove common filler words
                q = re.sub(r"^(一[下个]?|什么|哪些?|啥)", "", q).strip()
                if q:
                    return q

        # Fallback: any keyword after "搜索" or "search"
        m = re.search(r"(?:搜索|search)\s+(\S+)", user_message, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        return ""

    @staticmethod
    def _extract_package_name(user_message: str) -> str:
        """Try to extract package name from natural language."""
        import re

        # Explicit package name: "sao-skill-xxx" or "sao_skill_xxx"
        m = re.search(r"sao[-_]skill[-_][\w-]+", user_message)
        if m:
            return m.group(0).replace("_", "-")

        # "安装XX", "装XX"
        for pat in [
            r"(?:安装|装|install)\s*(\S+)",
            r"卸载\s*(\S+)",
            r"(?:remove|uninstall)\s+(\S+)",
        ]:
            m = re.search(pat, user_message, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                if name and not name.startswith("/"):
                    return name

        return ""
