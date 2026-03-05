"""FastAPI application entry point for Super-Agent-OS."""

import asyncio
from contextlib import asynccontextmanager
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

    # Initialize model factory
    factory = ModelFactory(settings)
    logger.info("Available models: %s", factory.available_models())

    # Initialize agent
    _agent = Agent(factory)
    logger.info("Agent initialized — ready to chat")

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
