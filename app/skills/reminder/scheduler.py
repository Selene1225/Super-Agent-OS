"""APScheduler management and Bitable sync for reminders."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.utils.config import get_settings
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")

# ─── Singleton scheduler ─────────────────────────────────────────────────

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


# ─── APScheduler job callback ────────────────────────────────────────────

async def _fire_reminder(record_id: str, open_id: str, content: str) -> None:
    from app.utils.feishu import send_text_message
    from app.skills.reminder.bitable import update_status

    text = f"⏰ 提醒：{content}"
    logger.info("Firing reminder %s → %s: %s", record_id, open_id, content)
    try:
        await send_text_message(receive_id=open_id, text=text)
    except Exception as e:
        logger.error("Failed to send reminder: %s", e)
    try:
        await update_status(record_id, "已完成")
    except Exception as e:
        logger.error("Failed to update Bitable: %s", e)


def schedule_job(record_id: str, open_id: str, content: str, remind_at: datetime) -> None:
    """Add a one-shot APScheduler job for a reminder."""
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
    from app.skills.reminder.bitable import fetch_pending

    try:
        pending = await fetch_pending()
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
            schedule_job(record_id, open_id, content, remind_at)
        count += 1
    logger.info("Synced %d pending reminders from Bitable", count)
    return count
