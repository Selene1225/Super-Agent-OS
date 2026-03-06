"""Central Agent — LLM-powered intent routing to skills or chat.

Phase 3+4: The Agent asks the LLM to classify the user's intent into
either a specific skill (with action+params) or plain chat.
Skills are auto-discovered from `app/skills/`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from app.core.factory import ModelFactory
from app.skills.base import BaseSkill, SkillContext
from app.utils.logger import logger

# Maximum conversation turns to keep per chat_id
_MAX_HISTORY = 20

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
4. 如果用户的意图不明确或不匹配任何技能，返回 {{"skill": "chat"}}
"""

_CHAT_SYSTEM_PROMPT = (
    "你是 Super-Agent-OS (SAO)，一个为用户量身打造的个人 AI 助理。\n"
    "你运行在用户的本地 Docker 容器中，可以通过飞书与用户沟通。\n"
    "请用简洁、专业的中文回答用户的问题。\n"
    "如果用户使用英文提问，请用英文回答。"
)


class Agent:
    """LLM-powered agent with automatic skill routing.

    1. User message comes in
    2. LLM classifies intent → skill name + action, or "chat"
    3. If skill → look up from registry → execute → return result
    4. If chat → normal multi-turn conversation
    """

    def __init__(self, factory: ModelFactory) -> None:
        self._factory = factory
        self._skills: dict[str, BaseSkill] = {}
        self._histories: dict[str, list[dict[str, str]]] = defaultdict(list)

    @property
    def factory(self) -> ModelFactory:
        return self._factory

    def register_skills(self, skills: dict[str, BaseSkill]) -> None:
        """Register discovered skills. Called after skill discovery."""
        self._skills = skills
        names = list(skills.keys())
        logger.info("Agent registered %d skills: %s", len(names), names)

    # ─── Main entry point ─────────────────────────────────

    async def process(self, user_message: str, chat_id: str) -> str:
        """Process user message: route to skill or chat."""
        logger.info("Agent.process — chat_id=%s, msg_len=%d", chat_id, len(user_message))

        # If no skills registered, go straight to chat
        if not self._skills:
            return await self._chat(user_message, chat_id)

        # Ask LLM to route
        intent = await self._classify_intent(user_message)
        skill_name = intent.get("skill", "chat")

        if skill_name == "chat":
            return await self._chat(user_message, chat_id)

        # Look up skill
        skill = self._skills.get(skill_name)
        if not skill:
            logger.warning("LLM routed to unknown skill '%s', falling back to chat", skill_name)
            return await self._chat(user_message, chat_id)

        # Execute skill
        logger.info("Routing to skill: %s (action=%s)", skill_name, intent.get("action"))
        context = SkillContext(
            user_message=user_message,
            chat_id=chat_id,
            factory=self._factory,
        )
        params = {
            "action": intent.get("action", ""),
            **intent.get("params", {}),
        }

        try:
            result = await skill.run(params, context)
        except Exception as e:
            logger.error("Skill %s failed: %s", skill_name, e, exc_info=True)
            result = f"技能 {skill_name} 执行出错：{e}"

        # Save to history
        self._histories[chat_id].append({"role": "user", "content": user_message})
        self._histories[chat_id].append({"role": "assistant", "content": result})

        return result

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
            raw = await self._factory.get_response(messages, temperature=0.1, max_tokens=200)
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

        lines = []
        for skill in self._skills.values():
            m = skill.manifest
            examples = "、".join(f"「{e}」" for e in m.usage_examples[:3])
            lines.append(f"- {m.name}: {m.description}")
            if examples:
                lines.append(f"  示例: {examples}")
            # Document available actions per skill
            if m.name == "reminder":
                lines.append("  子动作: set（设置新提醒）, list（查看提醒）, update（修改提醒时间/内容）, cancel（取消/删除提醒）")
        return "\n".join(lines)

    # ─── Plain chat ──────────────────────────────────────

    async def _chat(self, user_message: str, chat_id: str) -> str:
        """Normal multi-turn LLM chat."""
        history = self._histories[chat_id]
        history.append({"role": "user", "content": user_message})

        if len(history) > _MAX_HISTORY * 2:
            history[:] = history[-_MAX_HISTORY * 2 :]

        messages = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}] + history

        try:
            reply = await self._factory.get_response(messages)
        except Exception as e:
            logger.error("Agent._chat — LLM call failed: %s", e)
            reply = f"抱歉，AI 模型调用出现问题：{e}"

        history.append({"role": "assistant", "content": reply})
        return reply
