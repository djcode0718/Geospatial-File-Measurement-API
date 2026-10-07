"""Execution boundary and job abstraction decoupling API transport from geospatial processing."""

import logging
from typing import Protocol

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.db.models import FileRecord
from app.services.file_processing import FileProcessingService

logger = logging.getLogger(__name__)


class FileProcessorProtocol(Protocol):
    """Protocol defining the contract for file processing implementations."""

    def process_file_upload(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> FileRecord:
        """Process an uploaded geospatial file and persist results."""
        ...


class ProcessingExecutor:
    """Execution boundary coordinating the dispatch of file processing jobs.

    Decouples the HTTP/API transport layer from the underlying execution strategy.
    Currently executes processing synchronously within the active request context,
    while establishing the boundary for future background execution (e.g. FastAPI
    BackgroundTasks or dedicated worker systems) without altering geospatial domain logic.
    """

    def __init__(self, processor: FileProcessorProtocol | None = None) -> None:
        self.processor = processor or FileProcessingService()

    def submit(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> FileRecord:
        """Submit and execute a geospatial file processing job.

        Args:
            upload_file: Uploaded file payload from FastAPI.
            db: Active database session.

        Returns:
            Persisted FileRecord with processed features and measurements.

        Raises:
            AppError: Domain or validation errors propagated from processor.
            Exception: Unexpected processing failures.
        """
        filename = upload_file.filename or "unnamed"
        logger.info("Processing execution started for filename=%s", filename)

        try:
            file_record = self.processor.process_file_upload(upload_file=upload_file, db=db)
            logger.info(
                "Processing execution completed for file_id=%s (status=%s, features=%d)",
                file_record.id,
                file_record.status,
                file_record.feature_count,
            )
            return file_record
        except Exception as err:
            logger.warning(
                "Processing execution failed for filename=%s: %s",
                filename,
                err,
            )
            raise


def get_processing_executor() -> ProcessingExecutor:
    """FastAPI dependency provider for ProcessingExecutor."""
    return ProcessingExecutor()
