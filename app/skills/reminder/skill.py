"""ReminderSkill — the main skill class."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.skills.base import BaseSkill, SkillContext, SkillManifest
from app.skills.reminder.bitable import (
    delete_reminder,
    fetch_pending,
    update_reminder,
    write_reminder,
)
from app.skills.reminder.prompts import EXTRACT_TIME_PROMPT, MATCH_REMINDER_PROMPT
from app.skills.reminder.scheduler import cancel_job, schedule_job
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")


class ReminderSkill(BaseSkill):
    """Set, list, update, and cancel reminders stored in Feishu Bitable."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="reminder",
            description="设置、查看、修改、取消定时提醒（存储在飞书多维表格）",
            usage_examples=[
                "3月10号下午3点提醒我开会",
                "明天早上9点提醒我给老板打电话",
                "查看我的提醒",
                "把开会那个提醒改成8点",
                "取消报名考试的提醒",
                "半小时后提醒我吃药",
            ],
            version="0.4.0",
        )

    async def run(self, params: dict[str, Any], context: SkillContext) -> str:
        """Route to sub-action: set / list / update / cancel."""
        action = params.get("action", "set")

        if action == "list":
            return await self._list_reminders(context.chat_id)
        elif action == "update":
            return await self._update_reminder(context)
        elif action == "cancel":
            return await self._cancel_reminder(context)
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

        return self._format_set_reply(remind_at, content, now)

    # ── Update reminder ───────────────────────────────────

    async def _update_reminder(self, ctx: SkillContext) -> str:
        # Fetch user's pending reminders
        try:
            pending = await fetch_pending(open_id=ctx.chat_id)
        except Exception as e:
            return f"查询多维表格失败：{e}"

        if not pending:
            return "你目前没有待执行的提醒，无法修改。"

        # If only one reminder, LLM matching is easier
        # Ask LLM to match which reminder and what to change
        matched = await self._match_reminder(ctx.user_message, pending, ctx.factory)

        if "error" in matched:
            return f"抱歉，{matched['error']}\n\n你可以先说「查看我的提醒」看看有哪些。"

        idx = matched.get("index", 0)
        if not (1 <= idx <= len(pending)):
            return f"序号 {idx} 不在范围内（共 {len(pending)} 条提醒）。请说「查看我的提醒」确认后再试。"

        target = pending[idx - 1]
        record_id = target["record_id"]
        update_fields: dict[str, Any] = {}
        new_remind_at: datetime | None = None

        # Handle time update
        if "new_remind_at" in matched:
            try:
                new_remind_at = datetime.fromisoformat(matched["new_remind_at"]).replace(tzinfo=TZ)
            except ValueError:
                return f"时间格式解析失败：{matched['new_remind_at']}"

            now = datetime.now(TZ)
            if new_remind_at <= now:
                return f"新时间 {new_remind_at.strftime('%Y-%m-%d %H:%M')} 已过，请设置未来的时间。"
            update_fields["提醒时间"] = int(new_remind_at.timestamp() * 1000)

        # Handle content update
        new_content = matched.get("new_content")
        if new_content:
            update_fields["提醒内容"] = new_content

        if not update_fields:
            return "没有检测到需要修改的内容。请说明要改时间还是改内容。"

        # Apply update
        try:
            await update_reminder(record_id, update_fields)
        except Exception as e:
            logger.error("Bitable update failed: %s", e)
            return f"更新多维表格失败：{e}"

        # Reschedule APScheduler job
        final_content = new_content or target["content"]
        if new_remind_at:
            schedule_job(record_id, ctx.chat_id, final_content, new_remind_at)
        elif new_content:
            # Content changed but time didn't — reschedule with same time
            ts = target.get("remind_at_ts")
            if isinstance(ts, (int, float)):
                orig_time = datetime.fromtimestamp(ts / 1000, tz=TZ)
                schedule_job(record_id, ctx.chat_id, final_content, orig_time)

        # Build reply
        parts = ["✅ 提醒已更新！\n"]
        if new_remind_at:
            parts.append(f"📅 新时间：{new_remind_at.strftime('%Y-%m-%d %H:%M')}")
        if new_content:
            parts.append(f"📝 新内容：{new_content}")
        parts.append(f"\n原提醒：{target['remind_at_str']} — {target['content']}")
        return "\n".join(parts)

    # ── Cancel reminder ───────────────────────────────────

    async def _cancel_reminder(self, ctx: SkillContext) -> str:
        try:
            pending = await fetch_pending(open_id=ctx.chat_id)
        except Exception as e:
            return f"查询多维表格失败：{e}"

        if not pending:
            return "你目前没有待执行的提醒，无需取消。"

        matched = await self._match_reminder(ctx.user_message, pending, ctx.factory)

        if "error" in matched:
            return f"抱歉，{matched['error']}\n\n你可以先说「查看我的提醒」看看有哪些。"

        idx = matched.get("index", 0)
        if not (1 <= idx <= len(pending)):
            return f"序号 {idx} 不在范围内（共 {len(pending)} 条提醒）。"

        target = pending[idx - 1]
        record_id = target["record_id"]

        # Delete from Bitable and cancel APScheduler job
        try:
            await delete_reminder(record_id)
        except Exception as e:
            logger.error("Bitable delete failed: %s", e)
            return f"删除多维表格记录失败：{e}"

        cancel_job(record_id)

        return (
            f"✅ 提醒已取消！\n\n"
            f"🗑️ {target['remind_at_str']} — {target['content']}\n\n"
            f"已从多维表格中删除。"
        )

    # ── List reminders ────────────────────────────────────

    async def _list_reminders(self, open_id: str) -> str:
        try:
            pending = await fetch_pending(open_id=open_id)
        except Exception as e:
            return f"查询多维表格失败：{e}"

        if not pending:
            return "你目前没有待执行的提醒。"

        lines = ["📋 你的待执行提醒：\n"]
        for i, r in enumerate(pending, 1):
            lines.append(f"{i}. ⏰ {r['remind_at_str'] or '未知时间'}  —  {r['content']}")

        lines.append("\n💡 你可以说「把第X个改成...」或「取消第X个提醒」来管理。")
        lines.append("也可以直接在飞书多维表格中查看和编辑。")
        return "\n".join(lines)

    # ── LLM: parse time for "set" ─────────────────────────

    async def _parse_time(self, user_text: str, factory: Any) -> dict:
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M (%A)")
        messages = [
            {"role": "system", "content": EXTRACT_TIME_PROMPT.format(now=now_str)},
            {"role": "user", "content": user_text},
        ]
        try:
            raw = await factory.get_response(messages, temperature=0.1, max_tokens=256, enable_thinking=False)
            return self._parse_json(raw)
        except Exception as e:
            logger.error("Failed to parse reminder time: %s", e)
            return {"error": f"解析失败: {e}"}

    # ── LLM: match existing reminder for "update"/"cancel" ──

    async def _match_reminder(self, user_text: str, pending: list[dict], factory: Any) -> dict:
        """Ask LLM to match user request to one of the pending reminders."""
        # Build readable list
        reminder_lines = []
        for i, r in enumerate(pending, 1):
            reminder_lines.append(f"{i}. {r['remind_at_str']} — {r['content']}")
        reminders_block = "\n".join(reminder_lines)

        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M (%A)")
        prompt = MATCH_REMINDER_PROMPT.format(
            reminders_block=reminders_block,
            user_message=user_text,
            now=now_str,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text},
        ]
        try:
            raw = await factory.get_response(messages, temperature=0.1, max_tokens=256, enable_thinking=False)
            result = self._parse_json(raw)

            # Auto-match: if only 1 reminder and no index specified, default to 1
            if "index" not in result and not result.get("error") and len(pending) == 1:
                result["index"] = 1

            return result
        except Exception as e:
            logger.error("Failed to match reminder: %s", e)
            return {"error": f"解析失败: {e}"}

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Parse LLM response as JSON, stripping markdown fences."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)

    @staticmethod
    def _format_set_reply(remind_at: datetime, content: str, now: datetime) -> str:
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
