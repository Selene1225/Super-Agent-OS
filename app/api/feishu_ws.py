"""Feishu WebSocket long-connection event handler.

Receives events via WebSocket — no public URL / tunnel needed.
The local machine just needs internet access to connect to Feishu servers.
Uses the lark-oapi SDK's built-in WebSocket client.
"""

import asyncio
import json
import threading
import time
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from app.utils.logger import logger

# References set during initialization via start_ws_client()
_agent: Any = None
_loop: asyncio.AbstractEventLoop | None = None

# Message dedup — message_id -> timestamp
_seen_messages: dict[str, float] = {}
_DEDUP_TTL = 300  # 5 minutes


def _is_duplicate(message_id: str) -> bool:
    """Check if we've already processed this message."""
    now = time.time()
    # Cleanup expired entries
    expired = [mid for mid, ts in _seen_messages.items() if now - ts > _DEDUP_TTL]
    for mid in expired:
        del _seen_messages[mid]
    # Check and mark
    if message_id in _seen_messages:
        return True
    _seen_messages[message_id] = now
    return False


async def _process_and_reply(text: str, open_id: str) -> None:
    """Process message through Agent and send reply via Feishu API."""
    from app.utils.feishu import send_text_message

    reply = await _agent.process(text, chat_id=open_id)
    await send_text_message(receive_id=open_id, text=reply)


def _on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """Handle im.message.receive_v1 event from Feishu WebSocket."""
    try:
        message = data.event.message
        sender = data.event.sender

        # Dedup by message_id
        message_id = message.message_id
        if message_id and _is_duplicate(message_id):
            logger.debug("WS: duplicate message ignored: %s", message_id)
            return

        # Only handle text messages
        if message.message_type != "text":
            logger.info("WS: ignoring non-text message type: %s", message.message_type)
            return

        # Parse text content
        content = json.loads(message.content)
        text = content.get("text", "").strip()
        if not text:
            return

        open_id = sender.sender_id.open_id
        if not open_id:
            logger.warning("WS: no open_id in sender")
            return

        logger.info("WS received from %s: %s", open_id, text[:80])

        # Dispatch async processing to the main event loop (this callback runs in WS thread)
        assert _loop is not None, "Event loop not initialized"
        future = asyncio.run_coroutine_threadsafe(
            _process_and_reply(text, open_id),
            _loop,
        )
        # Block WS thread until processing completes (OK — WS SDK handles concurrency)
        future.result(timeout=120)

    except Exception as e:
        logger.error("WS message handler error: %s", e, exc_info=True)


# Build event dispatcher (verification_token and encrypt_key are empty for WS mode)
event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(_on_message_receive)
    .build()
)


def _run_ws_in_new_loop(ws_client: Any) -> None:
    """Run the WS client in a brand-new event loop.

    The lark-oapi SDK uses a module-level `loop = asyncio.get_event_loop()`,
    which grabs uvicorn's loop and causes conflicts. We patch it with a fresh
    loop created in this thread.
    """
    import lark_oapi.ws.client as ws_mod

    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    # Patch the module-level loop so the SDK uses our fresh one
    ws_mod.loop = new_loop

    try:
        ws_client.start()
    except Exception as e:
        logger.error("Feishu WS client stopped: %s", e, exc_info=True)


def start_ws_client(
    app_id: str,
    app_secret: str,
    agent: Any,
    loop: asyncio.AbstractEventLoop,
) -> threading.Thread:
    """Start the Feishu WebSocket client in a daemon thread.

    Args:
        app_id: Feishu app ID.
        app_secret: Feishu app secret.
        agent: The Agent instance for processing messages.
        loop: The main asyncio event loop (for dispatching async work).

    Returns:
        The daemon thread running the WS client.
    """
    global _agent, _loop
    _agent = agent
    _loop = loop

    ws_client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    thread = threading.Thread(target=_run_ws_in_new_loop, args=(ws_client,), name="feishu-ws", daemon=True)
    thread.start()
    logger.info("Feishu WebSocket client started in background thread")
    return thread
