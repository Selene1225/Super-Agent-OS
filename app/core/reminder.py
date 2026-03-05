"""Reminder scheduler — parse natural language, store in Feishu Bitable, push on time.

Architecture:
- **Feishu Bitable** (多维表格) = source of truth for all reminders
- **APScheduler** (in-memory) = timer that fires push notifications
- On startup, sync pending reminders from Bitable → APScheduler
- On new reminder: LLM extracts time → write to Bitable → schedule in APScheduler
- On trigger: push Feishu msg → update Bitable status to "已完成"

Required Bitable table fields (user creates manually):
  - 提醒内容 (Text)
  - 提醒时间 (DateTime, format: yyyy-MM-dd HH:mm)
  - 状态     (SingleSelect: 待执行 / 已完成 / 已取消)
  - 创建人   (Text — stores open_id)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.utils.config import get_settings
from app.utils.logger import logger

# Default timezone — Beijing
TZ = ZoneInfo("Asia/Shanghai")

# Singleton scheduler
_scheduler: AsyncIOScheduler | None = None

# ─── LLM prompt for extracting reminder info ────────────────────────────

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


def _build_extract_messages(user_text: str) -> list[dict[str, str]]:
    """Build messages for LLM to extract reminder info."""
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M (%A)")
    return [
        {"role": "system", "content": _EXTRACT_PROMPT.format(now=now_str)},
        {"role": "user", "content": user_text},
    ]


# ─── Scheduler lifecycle ────────────────────────────────────────────────

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
    """Return the global scheduler instance."""
    assert _scheduler is not None, "Scheduler not initialized"
    return _scheduler


# ─── Bitable helpers ─────────────────────────────────────────────────────

def _get_bitable_config() -> tuple[str, str]:
    """Return (app_token, table_id) from settings, or raise."""
    settings = get_settings()
    app_token = settings.feishu_bitable_app_token
    table_id = settings.feishu_bitable_reminder_table_id
    if not app_token or not table_id:
        raise RuntimeError(
            "飞书多维表格未配置。请在 .env 中设置 FEISHU_BITABLE_APP_TOKEN 和 FEISHU_BITABLE_REMINDER_TABLE_ID"
        )
    return app_token, table_id


async def _write_reminder_to_bitable(
    content: str,
    remind_at: datetime,
    open_id: str,
) -> str:
    """Create a record in the Bitable reminder table. Returns the record_id."""
    from app.utils.feishu import bitable_create_record

    app_token, table_id = _get_bitable_config()

    # Bitable DateTime expects millisecond timestamp
    remind_ts = int(remind_at.timestamp() * 1000)

    fields = {
        "提醒内容": content,
        "提醒时间": remind_ts,
        "状态": "待执行",
        "创建人": open_id,
    }

    data = await bitable_create_record(app_token, table_id, fields)
    record_id = data.get("data", {}).get("record", {}).get("record_id", "")
    if not record_id:
        raise RuntimeError(f"Bitable create failed: {data}")
    logger.info("Bitable reminder created: record_id=%s", record_id)
    return record_id


async def _update_reminder_status(record_id: str, status: str) -> None:
    """Update the 状态 field of a reminder record."""
    from app.utils.feishu import bitable_update_record

    app_token, table_id = _get_bitable_config()
    await bitable_update_record(app_token, table_id, record_id, {"状态": status})
    logger.info("Bitable reminder %s → %s", record_id, status)


async def _fetch_pending_reminders() -> list[dict]:
    """Fetch all 待执行 reminders from Bitable."""
    from app.utils.feishu import bitable_list_records

    app_token, table_id = _get_bitable_config()

    body: dict[str, Any] = {
        "filter": {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": "状态",
                    "operator": "is",
                    "value": ["待执行"],
                }
            ],
        },
        "sort": [{"field_name": "提醒时间", "desc": False}],
    }

    data = await bitable_list_records(app_token, table_id)
    items = data.get("data", {}).get("items", []) or []

    # Filter client-side for "待执行" as fallback (in case filter param differs)
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
    """Triggered by APScheduler — send Feishu message and update Bitable."""
    from app.utils.feishu import send_text_message

    text = f"⏰ 提醒：{content}"
    logger.info("Firing reminder %s → %s: %s", record_id, open_id, content)

    try:
        await send_text_message(receive_id=open_id, text=text)
    except Exception as e:
        logger.error("Failed to send reminder message: %s", e)

    try:
        await _update_reminder_status(record_id, "已完成")
    except Exception as e:
        logger.error("Failed to update Bitable status: %s", e)


def _schedule_job(record_id: str, open_id: str, content: str, remind_at: datetime) -> None:
    """Register a one-shot APScheduler job for a reminder."""
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
    """Load all pending reminders from Bitable and register APScheduler jobs.

    Call this on startup to restore scheduled reminders.
    Returns the number of reminders synced.
    """
    try:
        pending = await _fetch_pending_reminders()
    except Exception as e:
        logger.warning("Could not sync reminders from Bitable: %s", e)
        return 0

    now = datetime.now(TZ)
    count = 0

    for item in pending:
        ts = item.get("remind_at_ts")
        if not ts:
            continue

        # Bitable returns datetime as millisecond timestamp
        if isinstance(ts, (int, float)):
            remind_at = datetime.fromtimestamp(ts / 1000, tz=TZ)
        else:
            continue

        record_id = item["record_id"]
        open_id = item["open_id"]
        content = item["content"]

        if remind_at <= now:
            # Overdue — fire immediately
            logger.info("Overdue reminder %s, firing now", record_id)
            asyncio.create_task(_fire_reminder(record_id, open_id, content))
        else:
            _schedule_job(record_id, open_id, content, remind_at)

        count += 1

    logger.info("Synced %d pending reminders from Bitable", count)
    return count


# ─── LLM parsing ─────────────────────────────────────────────────────────

async def parse_reminder(user_text: str, factory: Any) -> dict:
    """Use LLM to extract reminder time and content from user text."""
    messages = _build_extract_messages(user_text)

    try:
        raw = await factory.get_response(messages, temperature=0.1, max_tokens=256)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to parse reminder from LLM: %s", e)
        return {"error": f"解析失败: {e}"}


# ─── High-level handlers (called by Agent) ───────────────────────────────

async def handle_reminder_request(
    user_text: str,
    open_id: str,
    factory: Any,
) -> str:
    """End-to-end: parse → write Bitable → schedule → confirm."""
    parsed = await parse_reminder(user_text, factory)

    if "error" in parsed:
        return f"抱歉，我无法理解这个提醒请求：{parsed['error']}\n\n请用类似格式：「3月10号下午3点提醒我开会」"

    remind_at_str = parsed.get("remind_at", "")
    content = parsed.get("content", "")

    if not remind_at_str or not content:
        return "抱歉，我无法从你的消息中提取出提醒时间或内容。请再试一次。"

    try:
        remind_at = datetime.fromisoformat(remind_at_str).replace(tzinfo=TZ)
    except ValueError:
        return f"时间格式解析失败：{remind_at_str}"

    now = datetime.now(TZ)
    if remind_at <= now:
        return f"提醒时间 {remind_at.strftime('%Y-%m-%d %H:%M')} 已经过去了，请设置一个未来的时间。"

    # 1) Write to Bitable
    try:
        record_id = await _write_reminder_to_bitable(content, remind_at, open_id)
    except Exception as e:
        logger.error("Bitable write failed: %s", e)
        return f"写入飞书多维表格失败：{e}"

    # 2) Schedule APScheduler job
    _schedule_job(record_id, open_id, content, remind_at)

    # Friendly time estimate
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


async def handle_list_reminders(open_id: str) -> str:
    """List all pending reminders for the user from Bitable."""
    try:
        pending = await _fetch_pending_reminders()
    except Exception as e:
        return f"查询多维表格失败：{e}"

    # Filter to this user
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

    lines.append("\n你也可以直接在飞书多维表格中查看和编辑提醒。")
    return "\n".join(lines)
