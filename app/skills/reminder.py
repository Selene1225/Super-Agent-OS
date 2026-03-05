"""Reminder skill — set / list / cancel reminders via Feishu Bitable + APScheduler.

This skill:
- Accepts sub-actions: "set", "list", "cancel"
- Uses LLM to parse natural-language time for "set"
- Stores reminders in Feishu Bitable (source of truth)
- Schedules APScheduler jobs for timely push
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.skills.base import BaseSkill, SkillContext, SkillManifest
from app.utils.config import get_settings
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")

# ─── Singleton scheduler (shared across the skill) ───────────────────────

_scheduler: AsyncIOScheduler | None = None


def init_scheduler() -> AsyncIOScheduler:
    """Create and start an in-memory APScheduler."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone=TZ)
    _scheduler.start()
    logger.info("Reminder scheduler started (in-memory, syncs from Bitable)")
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    assert _scheduler is not None, "Scheduler not initialized"
    return _scheduler


# ─── LLM time-extraction prompt ──────────────────────────────────────────

_EXTRACT_PROMPT = """\
你是一个时间解析助手。用户会用自然语言描述一个提醒需求。
请从中提取出 **提醒时间** 和 **提醒内容**，以JSON格式输出。

规则:
1. 当前时间: {now}
2. 输出严格JSON格式，不要包含其他文字。
3. 时间字段 "remind_at" 为 ISO 8601 格式: "YYYY-MM-DDTHH:MM"
4. 内容字段 "content" 为提醒文本
5. 如果用户没有明确说年份，默认为当前年份；如果时间已过，推到明年
6. "明天" = 当前日期+1天, "后天" = +2天, "下周一" = 下一个周一, etc.
7. 如果无法解析出合理的时间，返回 {{"error": "无法解析时间"}}

示例:
用户: "3月10号下午3点提醒我开会"
输出: {{"remind_at": "2026-03-10T15:00", "content": "开会"}}

用户: "明天早上9点提醒我给老板打电话"
输出: {{"remind_at": "2026-03-06T09:00", "content": "给老板打电话"}}

用户: "半小时后提醒我吃药"
输出: {{"remind_at": "2026-03-05T16:30", "content": "吃药"}}
"""


# ─── Bitable helpers ─────────────────────────────────────────────────────

def _get_bitable_config() -> tuple[str, str]:
    settings = get_settings()
    app_token = settings.feishu_bitable_app_token
    table_id = settings.feishu_bitable_reminder_table_id
    if not app_token or not table_id:
        raise RuntimeError(
            "飞书多维表格未配置。请在 .env 中设置 FEISHU_BITABLE_APP_TOKEN 和 FEISHU_BITABLE_REMINDER_TABLE_ID"
        )
    return app_token, table_id


async def _write_to_bitable(content: str, remind_at: datetime, open_id: str) -> str:
    from app.utils.feishu import bitable_create_record
    app_token, table_id = _get_bitable_config()
    fields = {
        "提醒内容": content,
        "提醒时间": int(remind_at.timestamp() * 1000),
        "状态": "待执行",
        "创建人": open_id,
    }
    data = await bitable_create_record(app_token, table_id, fields)
    record_id = data.get("data", {}).get("record", {}).get("record_id", "")
    if not record_id:
        raise RuntimeError(f"Bitable create failed: {data}")
    logger.info("Bitable reminder created: record_id=%s", record_id)
    return record_id


async def _update_status(record_id: str, status: str) -> None:
    from app.utils.feishu import bitable_update_record
    app_token, table_id = _get_bitable_config()
    await bitable_update_record(app_token, table_id, record_id, {"状态": status})
    logger.info("Bitable reminder %s → %s", record_id, status)


async def _fetch_pending() -> list[dict]:
    from app.utils.feishu import bitable_list_records
    app_token, table_id = _get_bitable_config()
    data = await bitable_list_records(app_token, table_id)
    items = data.get("data", {}).get("items", []) or []
    pending = []
    for item in items:
        fields = item.get("fields", {})
        if fields.get("状态") == "待执行":
            pending.append({
                "record_id": item["record_id"],
                "content": fields.get("提醒内容", ""),
                "remind_at_ts": fields.get("提醒时间"),
                "open_id": fields.get("创建人", ""),
            })
    return pending


# ─── APScheduler job callback ────────────────────────────────────────────

async def _fire_reminder(record_id: str, open_id: str, content: str) -> None:
    from app.utils.feishu import send_text_message
    text = f"⏰ 提醒：{content}"
    logger.info("Firing reminder %s → %s: %s", record_id, open_id, content)
    try:
        await send_text_message(receive_id=open_id, text=text)
    except Exception as e:
        logger.error("Failed to send reminder: %s", e)
    try:
        await _update_status(record_id, "已完成")
    except Exception as e:
        logger.error("Failed to update Bitable: %s", e)


def _schedule_job(record_id: str, open_id: str, content: str, remind_at: datetime) -> None:
    scheduler = get_scheduler()
    job_id = f"reminder_{record_id}"
    scheduler.add_job(
        _fire_reminder,
        trigger="date",
        run_date=remind_at,
        args=[record_id, open_id, content],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Scheduled job %s at %s", job_id, remind_at)


# ─── Startup sync ────────────────────────────────────────────────────────

async def sync_reminders_from_bitable() -> int:
    """Load pending reminders from Bitable → APScheduler. Call on startup."""
    try:
        pending = await _fetch_pending()
    except Exception as e:
        logger.warning("Could not sync reminders from Bitable: %s", e)
        return 0

    now = datetime.now(TZ)
    count = 0
    for item in pending:
        ts = item.get("remind_at_ts")
        if not ts or not isinstance(ts, (int, float)):
            continue
        remind_at = datetime.fromtimestamp(ts / 1000, tz=TZ)
        record_id, open_id, content = item["record_id"], item["open_id"], item["content"]
        if remind_at <= now:
            logger.info("Overdue reminder %s, firing now", record_id)
            asyncio.create_task(_fire_reminder(record_id, open_id, content))
        else:
            _schedule_job(record_id, open_id, content, remind_at)
        count += 1
    logger.info("Synced %d pending reminders from Bitable", count)
    return count


# ─── The Skill class ─────────────────────────────────────────────────────

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
            version="0.2.0",
        )

    async def run(self, params: dict[str, Any], context: SkillContext) -> str:
        """Route to sub-action: set / list.

        params["action"]:
            - "set": parse + write + schedule a reminder
            - "list": list pending reminders
        """
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
            record_id = await _write_to_bitable(content, remind_at, ctx.chat_id)
        except Exception as e:
            logger.error("Bitable write failed: %s", e)
            return f"写入飞书多维表格失败：{e}"

        _schedule_job(record_id, ctx.chat_id, content, remind_at)

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
            pending = await _fetch_pending()
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
            {"role": "system", "content": _EXTRACT_PROMPT.format(now=now_str)},
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
