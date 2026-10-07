from fastapi import APIRouter

from app.api.files import router as files_router
from app.api.health import router as health_router

api_router = APIRouter()

# Include health routes
api_router.include_router(health_router)

# Include file management routes (/api/files)
api_router.include_router(files_router, prefix="/api")
