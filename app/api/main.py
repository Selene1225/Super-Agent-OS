"""FastAPI application entry point for Super-Agent-OS."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI

from app.core.agent import Agent
from app.core.factory import ModelFactory
from app.utils.config import get_settings
from app.utils.logger import logger

# Module-level references set during lifespan
_agent: Agent | None = None


def get_agent() -> Agent:
    """Return the global Agent instance (available after startup)."""
    assert _agent is not None, "Agent not initialized — app lifespan has not started"
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — initialize core components on startup."""
    global _agent

    settings = get_settings()
    logger.info("=== Super-Agent-OS starting ===")
    logger.info("Log level: %s", settings.log_level)

    # Ensure data directory exists
    Path("data").mkdir(exist_ok=True)

    # ── Phase 5.2: Initialize memory system ──
    from app.core.memory import init_memory
    init_memory()

    # Initialize model factory
    factory = ModelFactory(settings)

    # Initialize agent
    _agent = Agent(factory)
    logger.info("Agent initialized — ready to chat")

    # ── Phase 3: Discover and register skills ──
    from app.skills import discover_and_register_skills, _registry

    skill_count = discover_and_register_skills()
    logger.info("Discovered %d skill(s)", skill_count)
    _agent.register_skills(_registry)

    # ── Initialize reminder scheduler + sync from Bitable ──
    from app.skills.reminder.scheduler import init_scheduler, sync_reminders_from_bitable

    init_scheduler()

    if settings.feishu_bitable_app_token and settings.feishu_bitable_reminder_table_id:
        synced = await sync_reminders_from_bitable()
        logger.info("Synced %d reminders from Bitable", synced)
    else:
        logger.warning("Bitable not configured — reminder sync skipped")

    # Start Feishu WebSocket client (long-connection, no public URL needed)
    if settings.feishu_app_id and settings.feishu_app_secret:
        from app.api.feishu_ws import start_ws_client

        loop = asyncio.get_event_loop()
        start_ws_client(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            agent=_agent,
            loop=loop,
        )
    else:
        logger.warning("Feishu credentials not set — WS client not started")

    yield

    # Shutdown
    from app.skills.reminder.scheduler import get_scheduler

    try:
        get_scheduler().shutdown(wait=False)
        logger.info("Reminder scheduler shut down")
    except Exception:
        pass

    logger.info("=== Super-Agent-OS shutting down ===")
    _agent = None


app = FastAPI(
    title="Super-Agent-OS",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
from app.api.feishu_webhook import router as feishu_router  # noqa: E402

app.include_router(feishu_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
