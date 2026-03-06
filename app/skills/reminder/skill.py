"""ReminderSkill — the main skill class."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.skills.base import BaseSkill, SkillContext, SkillManifest
from app.skills.reminder.bitable import fetch_pending, write_reminder
from app.skills.reminder.prompts import EXTRACT_TIME_PROMPT
from app.skills.reminder.scheduler import schedule_job
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")


class ReminderSkill(BaseSkill):
    """Set, list, and manage reminders stored in Feishu Bitable."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="reminder",
            description="设置、查看、取消定时提醒（存储在飞书多维表格）",
            usage_examples=[
                "3月10号下午3点提醒我开会",
                "明天早上9点提醒我给老板打电话",
                "查看我的提醒",
                "我最近有什么安排",
                "半小时后提醒我吃药",
            ],
            version="0.3.0",
        )

    async def run(self, params: dict[str, Any], context: SkillContext) -> str:
        """Route to sub-action: set / list."""
        action = params.get("action", "set")

        if action == "list":
            return await self._list_reminders(context.chat_id)
        else:
            return await self._set_reminder(context)

    # ── Set reminder ──────────────────────────────────────

    async def _set_reminder(self, ctx: SkillContext) -> str:
        parsed = await self._parse_time(ctx.user_message, ctx.factory)

        if "error" in parsed:
            return (
                f"抱歉，我无法理解这个提醒请求：{parsed['error']}\n\n"
                f"请用类似格式：「3月10号下午3点提醒我开会」"
            )

        remind_at_str = parsed.get("remind_at", "")
        content = parsed.get("content", "")
        if not remind_at_str or not content:
            return "抱歉，我无法提取出提醒时间或内容。请再试一次。"

        try:
            remind_at = datetime.fromisoformat(remind_at_str).replace(tzinfo=TZ)
        except ValueError:
            return f"时间格式解析失败：{remind_at_str}"

        now = datetime.now(TZ)
        if remind_at <= now:
            return f"提醒时间 {remind_at.strftime('%Y-%m-%d %H:%M')} 已过，请设置未来的时间。"

        # Write Bitable + schedule
        try:
            record_id = await write_reminder(content, remind_at, ctx.chat_id)
        except Exception as e:
            logger.error("Bitable write failed: %s", e)
            return f"写入飞书多维表格失败：{e}"

        schedule_job(record_id, ctx.chat_id, content, remind_at)

        delta = remind_at - now
        if delta < timedelta(hours=1):
            time_desc = f"{int(delta.total_seconds() // 60)} 分钟后"
        elif delta < timedelta(days=1):
            time_desc = f"{delta.total_seconds() / 3600:.1f} 小时后"
        else:
            time_desc = f"{delta.days} 天后"

        return (
            f"✅ 提醒已设置！\n\n"
            f"📅 时间：{remind_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"📝 内容：{content}\n"
            f"⏳ 大约 {time_desc}\n\n"
            f"已同步到飞书多维表格，到时候我会飞书通知你。"
        )

    # ── List reminders ────────────────────────────────────

    async def _list_reminders(self, open_id: str) -> str:
        try:
            pending = await fetch_pending()
        except Exception as e:
            return f"查询多维表格失败：{e}"

        user_reminders = [r for r in pending if r.get("open_id") == open_id]
        if not user_reminders:
            return "你目前没有待执行的提醒。"

        lines = ["📋 你的待执行提醒：\n"]
        for i, r in enumerate(user_reminders, 1):
            ts = r.get("remind_at_ts")
            if isinstance(ts, (int, float)):
                t = datetime.fromtimestamp(ts / 1000, tz=TZ).strftime("%Y-%m-%d %H:%M")
            else:
                t = "未知时间"
            lines.append(f"{i}. ⏰ {t}  —  {r['content']}")

        lines.append("\n你也可以直接在飞书多维表格中查看和编辑。")
        return "\n".join(lines)

    # ── LLM time parsing ──────────────────────────────────

    async def _parse_time(self, user_text: str, factory: Any) -> dict:
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M (%A)")
        messages = [
            {"role": "system", "content": EXTRACT_TIME_PROMPT.format(now=now_str)},
            {"role": "user", "content": user_text},
        ]
        try:
            raw = await factory.get_response(messages, temperature=0.1, max_tokens=256)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(raw)
        except Exception as e:
            logger.error("Failed to parse reminder time: %s", e)
            return {"error": f"解析失败: {e}"}
