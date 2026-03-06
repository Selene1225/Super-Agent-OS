"""Bitable CRUD helpers for the Reminder skill."""

from __future__ import annotations

from datetime import datetime

from app.utils.config import get_settings
from app.utils.logger import logger


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


async def fetch_pending() -> list[dict]:
    """Fetch all pending reminders from Bitable."""
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
