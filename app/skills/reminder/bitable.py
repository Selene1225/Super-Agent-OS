"""Bitable CRUD helpers for the Reminder skill."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.utils.config import get_settings
from app.utils.logger import logger

TZ = ZoneInfo("Asia/Shanghai")


def _get_bitable_config() -> tuple[str, str]:
    settings = get_settings()
    app_token = settings.feishu_bitable_app_token
    table_id = settings.feishu_bitable_reminder_table_id
    if not app_token or not table_id:
        raise RuntimeError(
            "飞书多维表格未配置。请在 .env 中设置 FEISHU_BITABLE_APP_TOKEN 和 FEISHU_BITABLE_REMINDER_TABLE_ID"
        )
    return app_token, table_id


async def write_reminder(content: str, remind_at: datetime, open_id: str) -> str:
    """Create a reminder record in Bitable. Returns the record_id."""
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


async def update_status(record_id: str, status: str) -> None:
    """Update a reminder record's status field."""
    from app.utils.feishu import bitable_update_record

    app_token, table_id = _get_bitable_config()
    await bitable_update_record(app_token, table_id, record_id, {"状态": status})
    logger.info("Bitable reminder %s → %s", record_id, status)


async def update_reminder(record_id: str, fields: dict) -> None:
    """Update arbitrary fields on a reminder record (e.g. time, content)."""
    from app.utils.feishu import bitable_update_record

    app_token, table_id = _get_bitable_config()
    await bitable_update_record(app_token, table_id, record_id, fields)
    logger.info("Bitable reminder %s updated: %s", record_id, list(fields.keys()))


async def delete_reminder(record_id: str) -> None:
    """Delete a reminder record from Bitable."""
    from app.utils.feishu import bitable_delete_record

    app_token, table_id = _get_bitable_config()
    await bitable_delete_record(app_token, table_id, record_id)
    logger.info("Bitable reminder deleted: %s", record_id)


async def fetch_pending(open_id: str | None = None) -> list[dict]:
    """Fetch pending reminders from Bitable.

    Args:
        open_id: If provided, only return reminders for this user.

    Returns:
        List of dicts with record_id, content, remind_at_ts, open_id, remind_at_str.
    """
    from app.utils.feishu import bitable_list_records

    app_token, table_id = _get_bitable_config()
    data = await bitable_list_records(app_token, table_id)
    items = data.get("data", {}).get("items", []) or []
    pending = []
    for item in items:
        fields = item.get("fields", {})
        if fields.get("状态") != "待执行":
            continue
        item_open_id = fields.get("创建人", "")
        if open_id and item_open_id != open_id:
            continue
        ts = fields.get("提醒时间")
        remind_at_str = ""
        if isinstance(ts, (int, float)):
            remind_at_str = datetime.fromtimestamp(ts / 1000, tz=TZ).strftime("%Y-%m-%d %H:%M")
        pending.append({
            "record_id": item["record_id"],
            "content": fields.get("提醒内容", ""),
            "remind_at_ts": ts,
            "remind_at_str": remind_at_str,
            "open_id": item_open_id,
        })
    return pending
