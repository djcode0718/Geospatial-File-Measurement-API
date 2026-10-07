"""FastAPI Application Entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import setup_logging

logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle manager for startup and shutdown events."""
    settings = get_settings()
    logger.info(
        "Starting %s v%s in %s mode", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT
    )
    yield
    logger.info("Shutting down %s", settings.PROJECT_NAME)


def create_application() -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Register global exception handlers
    register_exception_handlers(app)

    # Attach root API router (health and future endpoints)
    app.include_router(api_router)

    return app


app = create_application()
