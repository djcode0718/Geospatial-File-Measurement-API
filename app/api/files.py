"""API endpoints for geospatial file upload and processing."""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.schemas import ErrorDetailSchema, FileUploadResponse
from app.db.session import get_db
from app.services.file_processing import FileProcessingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["Files"])


@router.post(
    "/",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a geospatial file",
    description=(
        "Accepts a `.zip` archive containing an ESRI Shapefile or a `.kml` file. "
        "Extracts vector geometries, detects CRS, computes metric area/length measurements, "
        "and persists the dataset."
    ),
    responses={
        201: {
            "model": FileUploadResponse,
            "description": "File successfully uploaded and processed.",
        },
        400: {
            "model": ErrorDetailSchema,
            "description": "Invalid format, corrupt archive, or security violation.",
        },
        413: {"model": ErrorDetailSchema, "description": "File size exceeds 50 MB limit."},
        415: {"model": ErrorDetailSchema, "description": "Unsupported file media type."},
        500: {"model": ErrorDetailSchema, "description": "Internal processing failure."},
    },
)
def upload_file(
    file: UploadFile = File(..., description="Geospatial file (.zip Shapefile archive or .kml)"),
    db: Session = Depends(get_db),
) -> FileUploadResponse:
    """Handle multipart file upload, validation, parsing, measurement, and persistence."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided in upload payload.",
        )

    service = FileProcessingService()
    file_record = service.process_file_upload(upload_file=file, db=db)

    return FileUploadResponse(
        id=file_record.id,
        filename=file_record.filename,
        file_type=file_record.file_type,
        feature_count=file_record.feature_count,
        source_crs=file_record.source_crs,
        calculation_crs=file_record.calculation_crs,
        status=file_record.status,
        created_at=file_record.created_at,
        summary=file_record.summary_metrics,
        error_message=file_record.error_message,
    )
