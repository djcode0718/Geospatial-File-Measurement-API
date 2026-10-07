"""Root API router aggregating all resource sub-routers."""

from fastapi import APIRouter

from app.api.health import router as health_router

api_router = APIRouter()

# Include health routes
api_router.include_router(health_router)

# Future endpoints (Phase 1.2+):
# api_router.include_router(files_router, prefix="/files", tags=["Files"])
