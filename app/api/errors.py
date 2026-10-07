"""Global exception handlers mapping domain exceptions to RFC 7807 problem details."""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AppError,
    ArchiveSecurityError,
    CRSError,
    FileSizeLimitExceededError,
    FileValidationError,
    GeospatialError,
    ResourceNotFoundError,
    StorageError,
    UnsupportedFileTypeError,
    XMLSecurityError,
)

logger = logging.getLogger(__name__)


def create_error_response(
    status_code: int,
    title: str,
    detail: str,
    instance: str,
    error_type: str = "about:blank",
) -> JSONResponse:
    """Generate an RFC 7807 compliant JSONResponse."""
    payload = {
        "type": error_type,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return JSONResponse(status_code=status_code, content=payload)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers to the FastAPI application."""

    @app.exception_handler(ResourceNotFoundError)
    async def not_found_handler(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        logger.info("Resource not found on %s: %s", request.url.path, exc.message)
        return create_error_response(
            status_code=404,
            title="Not Found",
            detail=exc.message,
            instance=request.url.path,
            error_type=f"https://errors.geomeasure.internal/{exc.code.lower().replace('_', '-')}",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("Request validation error on %s: %s", request.url.path, exc)
        return create_error_response(
            status_code=400,
            title="Bad Request",
            detail="Invalid request parameter or payload syntax.",
            instance=request.url.path,
            error_type="https://errors.geomeasure.internal/validation-error",
        )

    @app.exception_handler(UnsupportedFileTypeError)
    async def unsupported_file_type_handler(
        request: Request, exc: UnsupportedFileTypeError
    ) -> JSONResponse:
        logger.warning("Unsupported file type upload on %s: %s", request.url.path, exc.message)
        return create_error_response(
            status_code=415,
            title="Unsupported Media Type",
            detail=exc.message,
            instance=request.url.path,
            error_type="https://errors.geomeasure.internal/unsupported-file-type",
        )

    @app.exception_handler(FileSizeLimitExceededError)
    async def file_size_limit_handler(
        request: Request, exc: FileSizeLimitExceededError
    ) -> JSONResponse:
        logger.warning("Payload too large on %s: %s", request.url.path, exc.message)
        return create_error_response(
            status_code=413,
            title="Payload Too Large",
            detail=exc.message,
            instance=request.url.path,
            error_type="https://errors.geomeasure.internal/file-size-limit-exceeded",
        )

    @app.exception_handler(ArchiveSecurityError)
    @app.exception_handler(XMLSecurityError)
    @app.exception_handler(FileValidationError)
    @app.exception_handler(CRSError)
    @app.exception_handler(GeospatialError)
    async def client_validation_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "Client validation error on %s (%s): %s", request.url.path, exc.code, exc.message
        )
        return create_error_response(
            status_code=400,
            title="Bad Request",
            detail=exc.message,
            instance=request.url.path,
            error_type=f"https://errors.geomeasure.internal/{exc.code.lower().replace('_', '-')}",
        )

    @app.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError) -> JSONResponse:
        logger.error("Storage error on %s: %s", request.url.path, exc.message)
        return create_error_response(
            status_code=500,
            title="Storage Error",
            detail="A storage operation failed during file processing.",
            instance=request.url.path,
            error_type="https://errors.geomeasure.internal/storage-error",
        )

    @app.exception_handler(AppError)
    async def generic_app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.error("Application error on %s (%s): %s", request.url.path, exc.code, exc.message)
        return create_error_response(
            status_code=500,
            title="Internal Server Error",
            detail=exc.message,
            instance=request.url.path,
            error_type=f"https://errors.geomeasure.internal/{exc.code.lower().replace('_', '-')}",
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception on %s: %s", request.url.path, exc)
        return create_error_response(
            status_code=500,
            title="Internal Server Error",
            detail="An unexpected internal error occurred while processing the request.",
            instance=request.url.path,
            error_type="https://errors.geomeasure.internal/internal-server-error",
        )
