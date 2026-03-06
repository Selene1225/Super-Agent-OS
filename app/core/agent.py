"""Central Agent — LLM-powered intent routing to skills or chat.

Phase 3-5: The Agent asks the LLM to classify the user's intent into
either a specific skill (with action+params) or plain chat.
Skills are auto-discovered from `app/skills/`.

Phase 5.2: Memory system integration — all messages persisted to SQLite,
long-term memories extracted by LLM (OpenClaw-inspired).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from typing import Any

from app.core import commands as slash_commands
from app.core.factory import ModelFactory
from app.skills.base import BaseSkill, SkillContext
from app.utils.logger import logger

# Maximum conversation turns to keep per chat_id
_MAX_HISTORY = 20

# ─── Skill trigger keywords (fast-path: skip LLM router if none match) ──
# Map of skill_name -> list of trigger patterns (regex)
_SKILL_KEYWORDS: dict[str, list[str]] = {
    "reminder": [
        r"提醒", r"闹钟", r"日程", r"安排",
        r"remind", r"alarm", r"schedule",
        r"点.*叫我", r"后.*叫我",
        r"别忘了.{2,}",  # "别忘了" + specific content = potential reminder
    ],
    "marketplace": [
        r"搜索.*技能", r"安装.*技能", r"卸载.*技能", r"删除.*技能",
        r"装.*技能", r"找.*技能", r"有什么.*技能.*可以",
        r"技能市场", r"技能包", r"技能商店",
        r"sao-skill", r"sao_skill",
        r"skill.*(market|store|install)", r"install.*skill",
    ],
}

# ─── Intent routing prompt (injected with live skill list) ───────────────

_ROUTER_SYSTEM_PROMPT = """\
你是 Super-Agent-OS (SAO) 的意图路由器。
给定用户的消息，判断应该调用哪个技能，或者进行普通对话。

可用技能：
{skills_block}

请用严格的 JSON 格式回复（不要包含其他文字）：
- 如果需要调用技能：{{"skill": "<技能名>", "action": "<子动作>", "params": {{}}}}
- 如果是普通对话：{{"skill": "chat"}}

规则：
1. 只输出 JSON，不要输出任何其他文字
2. "action" 根据技能定义选择合适的子动作
3. 对于 reminder 技能：
   - 用户想设置新提醒 → action="set"
   - 用户想查看提醒/安排 → action="list"
   - 用户想修改已有提醒（改时间、改内容）→ action="update"
   - 用户想取消/删除已有提醒 → action="cancel"
4. 对于 marketplace 技能：
   - 搜索/查找技能 → action="search", params: {{"query": "关键词"}}
   - 安装技能包 → action="install", params: {{"name": "包名"}}
   - 卸载/删除技能 → action="remove", params: {{"name": "包名"}}
   - 查看已安装/列表 → action="list"
5. 如果用户的意图不明确或不匹配任何技能，返回 {{"skill": "chat"}}
"""

_CHAT_SYSTEM_PROMPT = (
    "你是 Super-Agent-OS (SAO)，一个为用户量身打造的个人 AI 助理。\n"
    "你运行在用户的本地 Docker 容器中，可以通过飞书与用户沟通。\n"
    "请用简洁、专业的中文回答用户的问题。\n"
    "如果用户使用英文提问，请用英文回答。"
)


class Agent:
    """LLM-powered agent with automatic skill routing and memory.

    Flow:
    1. Slash command? → dispatch immediately (no LLM)
    2. Save user message to SQLite
    3. LLM classifies intent → skill or chat
    4. Execute skill or have multi-turn chat (with memory context)
    5. Save assistant reply to SQLite
    6. Background: LLM extracts memories from the turn
    """

    def __init__(self, factory: ModelFactory) -> None:
        self._factory = factory
        self._skills: dict[str, BaseSkill] = {}
        # In-memory histories kept as fallback / for slash commands
        self._histories: dict[str, list[dict[str, str]]] = defaultdict(list)

    @property
    def factory(self) -> ModelFactory:
        return self._factory

    @property
    def skills(self) -> dict[str, BaseSkill]:
        """Read-only access to registered skills (used by slash commands)."""
        return self._skills

    @property
    def histories(self) -> dict[str, list[dict[str, str]]]:
        """Read-write access to conversation histories (used by slash commands)."""
        return self._histories

    def register_skills(self, skills: dict[str, BaseSkill]) -> None:
        """Register discovered skills. Called after skill discovery."""
        self._skills = skills
        names = list(skills.keys())
        logger.info("Agent registered %d skills: %s", len(names), names)

    def register_new_skill(self, skill: BaseSkill) -> None:
        """Hot-register a newly installed skill at runtime."""
        name = skill.manifest.name
        self._skills[name] = skill
        # Also update the global skills registry
        from app.skills import _registry
        _registry[name] = skill
        logger.info("Hot-loaded skill: %s v%s", name, skill.manifest.version)

    def unregister_skill(self, skill_name: str) -> bool:
        """Unregister a skill at runtime. Returns True if removed."""
        removed = False
        if skill_name in self._skills:
            del self._skills[skill_name]
            removed = True
        from app.skills import _registry
        if skill_name in _registry:
            del _registry[skill_name]
            removed = True
        if removed:
            logger.info("Unregistered skill: %s", skill_name)
        return removed

    # ─── Main entry point ─────────────────────────────────

    async def process(self, user_message: str, chat_id: str) -> str:
        """Process user message: route to skill or chat."""
        t0 = time.perf_counter()
        logger.info("Agent.process — chat_id=%s, msg_len=%d", chat_id, len(user_message))

        # Phase 5.1: Slash command interception (before LLM routing)
        if user_message.strip().startswith("/"):
            result = await slash_commands.dispatch(user_message, self, chat_id)
            if result is not None:
                return result

        # Phase 5.2: Persist user message to SQLite
        self._save_msg(chat_id, "user", user_message)

        # If no skills registered, go straight to chat
        if not self._skills:
            reply = await self._chat(user_message, chat_id)
        else:
            # Fast-path: keyword check before LLM routing
            # If no skill keywords match, skip the expensive LLM router call
            needs_routing = self._needs_skill_routing(user_message)

            if not needs_routing:
                logger.info("Fast-path: no skill keywords matched, skipping LLM router")
                reply = await self._chat(user_message, chat_id)
            else:
                # Keywords matched — ask LLM to classify precisely
                t1 = time.perf_counter()
                intent = await self._classify_intent(user_message)
                logger.info("Intent classification took %.2fs", time.perf_counter() - t1)
                skill_name = intent.get("skill", "chat")

                if skill_name == "chat":
                    reply = await self._chat(user_message, chat_id)
                else:
                    # Look up skill
                    skill = self._skills.get(skill_name)
                    if not skill:
                        logger.warning("LLM routed to unknown skill '%s', falling back to chat", skill_name)
                        reply = await self._chat(user_message, chat_id)
                    else:
                        # Execute skill
                        logger.info("Routing to skill: %s (action=%s)", skill_name, intent.get("action"))
                        context = SkillContext(
                            user_message=user_message,
                            chat_id=chat_id,
                            factory=self._factory,
                            agent=self,
                        )
                        params = {
                            "action": intent.get("action", ""),
                            **intent.get("params", {}),
                        }
                        try:
                            reply = await skill.run(params, context)
                        except Exception as e:
                            logger.error("Skill %s failed: %s", skill_name, e, exc_info=True)
                            reply = f"技能 {skill_name} 执行出错：{e}"

        # Phase 5.2: Persist assistant reply to SQLite
        self._save_msg(chat_id, "assistant", reply)

        # Also keep in-memory history for backward compatibility
        self._histories[chat_id].append({"role": "user", "content": user_message})
        self._histories[chat_id].append({"role": "assistant", "content": reply})

        # Phase 5.2: Extract memories in background (don't block the reply)
        asyncio.ensure_future(self._extract_and_save_memories(user_message, reply))

        elapsed = time.perf_counter() - t0
        logger.info("Agent.process completed in %.2fs", elapsed)
        return reply

    # ─── Keyword fast-path ─────────────────────────────────

    def _needs_skill_routing(self, user_message: str) -> bool:
        """Quick keyword check: does the message look like it might need a skill?

        Returns True if any registered skill's trigger keywords match.
        This avoids an expensive LLM router call for simple chat messages.

        Checks two sources:
        1. Hardcoded _SKILL_KEYWORDS (built-in skills)
        2. skill.manifest.trigger_patterns (works for dynamically installed skills)
        """
        text = user_message.lower().strip()
        for skill_name, skill in self._skills.items():
            # Source 1: hardcoded keywords
            patterns = _SKILL_KEYWORDS.get(skill_name, [])
            for pat in patterns:
                if re.search(pat, text):
                    logger.debug("Keyword '%s' matched for skill '%s'", pat, skill_name)
                    return True
            # Source 2: manifest-declared trigger patterns
            for pat in skill.manifest.trigger_patterns:
                if re.search(pat, text):
                    logger.debug("Manifest trigger '%s' matched for skill '%s'", pat, skill_name)
                    return True
        return False

    # ─── Memory helpers ──────────────────────────────────

    def _save_msg(self, chat_id: str, role: str, content: str) -> None:
        """Save a message to SQLite (best-effort, never block)."""
        try:
            from app.core.memory import save_message
            save_message(chat_id, role, content)
        except Exception as e:
            logger.warning("Failed to save message to memory: %s", e)

    async def _extract_and_save_memories(self, user_message: str, reply: str) -> None:
        """Ask LLM to extract memorable info from this turn. Runs in background."""
        try:
            from app.core.memory import remember
            from app.core.memory.long_term import extract_memories

            memories = await extract_memories(user_message, reply, self._factory)
            for mem in memories:
                content = mem.get("content", "").strip()
                if not content:
                    continue
                category = mem.get("category", "fact")
                if category not in ("preference", "fact", "decision", "context"):
                    category = "fact"
                await remember(content=content, category=category, source="agent_inferred")
        except Exception as e:
            logger.debug("Memory extraction failed (non-critical): %s", e)

    def _load_history_from_db(self, chat_id: str, limit: int = _MAX_HISTORY) -> list[dict[str, str]]:
        """Load conversation history from SQLite."""
        try:
            from app.core.memory import get_history
            messages = get_history(chat_id, limit=limit)
            return [{"role": m.role, "content": m.content} for m in messages]
        except Exception as e:
            logger.warning("Failed to load history from DB, using in-memory: %s", e)
            return list(self._histories.get(chat_id, []))

    def _get_memory_context(self) -> str:
        """Get long-term memory context for system prompt injection."""
        try:
            from app.core.memory import get_memory_context
            return get_memory_context()
        except Exception:
            return ""

    # ─── LLM intent classification ───────────────────────

    async def _classify_intent(self, user_message: str) -> dict[str, Any]:
        """Ask LLM to classify the user's intent into a skill or chat."""
        skills_block = self._build_skills_block()
        system_prompt = _ROUTER_SYSTEM_PROMPT.format(skills_block=skills_block)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            raw = await self._factory.get_response(
                messages, temperature=0.1, max_tokens=200, enable_thinking=False,
            )
            raw = raw.strip()
            # Strip markdown code fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            intent = json.loads(raw)
            logger.debug("Intent classification: %s", intent)
            return intent
        except Exception as e:
            logger.warning("Intent classification failed (%s), falling back to chat", e)
            return {"skill": "chat"}

    def _build_skills_block(self) -> str:
        """Build the skill description block for the router prompt."""
        if not self._skills:
            return "（无可用技能）"

        # Fallback action docs for built-in skills without actions_doc
        _BUILTIN_ACTIONS: dict[str, str] = {
            "reminder": "子动作: set（设置新提醒）, list（查看提醒）, update（修改提醒时间/内容）, cancel（取消/删除提醒）",
            "marketplace": (
                '子动作: search（搜索技能，params: {query: "关键词"}）, '
                'install（安装指定包，params: {name: "sao-skill-xxx"}）, '
                'remove（卸载，params: {name: "sao-skill-xxx"}）, '
                'list（列出已安装的市场技能）'
            ),
        }

        lines = []
        for skill in self._skills.values():
            m = skill.manifest
            examples = "、".join(f"「{e}」" for e in m.usage_examples[:3])
            lines.append(f"- {m.name}: {m.description}")
            if examples:
                lines.append(f"  示例: {examples}")
            # Action docs: prefer manifest-declared, fall back to built-in
            actions_doc = m.actions_doc or _BUILTIN_ACTIONS.get(m.name, "")
            if actions_doc:
                lines.append(f"  {actions_doc}")
        return "\n".join(lines)

    # ─── Plain chat ──────────────────────────────────────

    async def _chat(self, user_message: str, chat_id: str) -> str:
        """Normal multi-turn LLM chat with memory context."""
        t0 = time.perf_counter()

        # Load history from SQLite (already includes the user message we just saved)
        history = self._load_history_from_db(chat_id, limit=_MAX_HISTORY)

        # Build system prompt with memory context
        memory_ctx = self._get_memory_context()
        system_prompt = _CHAT_SYSTEM_PROMPT
        if memory_ctx:
            system_prompt = system_prompt + "\n\n" + memory_ctx

        messages = [{"role": "system", "content": system_prompt}] + history
        logger.debug("_chat: %d history messages, system_prompt_len=%d", len(history), len(system_prompt))

        try:
            reply = await self._factory.get_response(messages)
        except Exception as e:
            logger.error("Agent._chat — LLM call failed: %s", e)
            reply = f"抱歉，AI 模型调用出现问题：{e}"

        logger.info("_chat LLM call took %.2fs", time.perf_counter() - t0)
        return reply
