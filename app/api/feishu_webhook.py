"""Feishu event webhook — receives and routes Feishu Bot event callbacks."""

import json
import time
from typing import Any

from fastapi import APIRouter, Request, Response

from app.utils.config import get_settings
from app.utils.feishu import decrypt_event, send_text_message
from app.utils.logger import logger

router = APIRouter(prefix="/feishu", tags=["feishu"])

# Simple in-memory event dedup — event_id -> timestamp
_seen_events: dict[str, float] = {}
_DEDUP_TTL = 300  # 5 minutes


def _cleanup_seen_events() -> None:
    """Remove expired entries from the dedup cache."""
    now = time.time()
    expired = [eid for eid, ts in _seen_events.items() if now - ts > _DEDUP_TTL]
    for eid in expired:
        del _seen_events[eid]


def _is_duplicate(event_id: str) -> bool:
    """Check if we've already processed this event."""
    _cleanup_seen_events()
    if event_id in _seen_events:
        return True
    _seen_events[event_id] = time.time()
    return False


async def _handle_message_event(event: dict[str, Any]) -> None:
    """Handle im.message.receive_v1 — extract text, call Agent, reply via Feishu."""
    from app.api.main import get_agent  # Avoid circular import

    message = event.get("message", {})
    sender = event.get("sender", {})

    # Only handle text messages
    msg_type = message.get("message_type", "")
    if msg_type != "text":
        logger.info("Ignoring non-text message type: %s", msg_type)
        return

    # Extract text content
    try:
        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "").strip()
    except json.JSONDecodeError:
        logger.warning("Failed to parse message content")
        return

    if not text:
        return

    # Use sender open_id as conversation identifier
    open_id = sender.get("sender_id", {}).get("open_id", "")
    if not open_id:
        logger.warning("No open_id found in event sender")
        return

    logger.info("Received message from %s: %s", open_id, text[:80])

    # Call Agent
    agent = get_agent()
    reply = await agent.process(text, chat_id=open_id)

    # Send reply
    await send_text_message(receive_id=open_id, text=reply, receive_id_type="open_id")


@router.post("/event")
async def feishu_event(request: Request) -> Response:
    """Feishu event subscription callback endpoint.

    Handles:
    - URL verification challenge (plain JSON, no encryption)
    - Encrypted event callbacks (when encrypt_key is set)
    - im.message.receive_v1 events
    """
    raw_body = await request.json()
    settings = get_settings()

    # --- Decrypt if needed ---
    if "encrypt" in raw_body:
        if not settings.feishu_encrypt_key:
            logger.error("Received encrypted event but FEISHU_ENCRYPT_KEY is not set")
            return Response(status_code=400)
        decrypted = decrypt_event(settings.feishu_encrypt_key, raw_body["encrypt"])
        body = json.loads(decrypted)
    else:
        body = raw_body

    # --- URL Verification Challenge ---
    if "challenge" in body:
        logger.info("Feishu URL verification challenge received")
        return Response(
            content=json.dumps({"challenge": body["challenge"]}),
            media_type="application/json",
        )

    # --- Event Schema v2.0 ---
    schema_version = body.get("schema")
    if schema_version == "2.0":
        header = body.get("header", {})
        event_type = header.get("event_type", "")
        event_id = header.get("event_id", "")

        # Dedup
        if event_id and _is_duplicate(event_id):
            logger.debug("Duplicate event ignored: %s", event_id)
            return Response(status_code=200)

        # Verify token
        token = header.get("token", "")
        if settings.feishu_verify_token and token != settings.feishu_verify_token:
            logger.warning("Event token mismatch — ignoring")
            return Response(status_code=403)

        logger.info("Event received: type=%s, id=%s", event_type, event_id)

        if event_type == "im.message.receive_v1":
            event_data = body.get("event", {})
            # Process asynchronously but don't block the webhook response
            import asyncio
            asyncio.create_task(_handle_message_event(event_data))

        return Response(status_code=200)

    # --- Event Schema v1.0 (legacy) ---
    event_type = body.get("type") or body.get("event", {}).get("type", "")
    logger.info("Legacy event: type=%s", event_type)
    return Response(status_code=200)
