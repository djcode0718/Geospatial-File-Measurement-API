"""Execution boundary and job abstraction decoupling API transport from geospatial processing."""

import logging
from typing import Protocol

from fastapi import BackgroundTasks, UploadFile
from sqlalchemy.orm import Session

from app.db.models import FileRecord, FileType
from app.db.session import get_db_session
from app.services.file_processing import FileProcessingService
from app.storage.staging import StagingArea

logger = logging.getLogger(__name__)


class FileProcessorProtocol(Protocol):
    """Protocol defining the contract for file processing implementations."""

    def process_file_upload(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> FileRecord:
        """Process an uploaded geospatial file and persist results synchronously."""
        ...

    def prepare_upload_staging(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> tuple[FileRecord, StagingArea, str, FileType]:
        """Validate metadata, initialize FileRecord (PROCESSING), and stage payload to disk."""
        ...

    def process_staged_dataset(
        self,
        file_id: str,
        staged_path: str,
        filename: str,
        file_type: FileType,
        db: Session,
        staging: StagingArea | None = None,
    ) -> FileRecord:
        """Process an already-staged geospatial file dataset and atomically persist results."""
        ...


def run_background_processing_task(
    file_id: str,
    staged_path: str,
    filename: str,
    file_type: str,
    staging: StagingArea,
    processor: FileProcessorProtocol | None = None,
) -> None:
    """Standalone background worker task running with an independent database session.

    Ensures that request-scoped database sessions are never leaked or reused across thread boundaries.
    Guarantees staging directory cleanup and final status persistence in all scenarios.

    Args:
        file_id: Unique identifier of the FileRecord to process.
        staged_path: Path to the uploaded file in temporary staging.
        filename: Original sanitized filename.
        file_type: Format string (SHAPEFILE_ZIP or KML).
        staging: StagingArea instance managing the temporary directory lifecycle.
        processor: Processing service implementation.
    """
    proc = processor or FileProcessingService()
    logger.info(
        "Background processing task started for file_id=%s filename=%s",
        file_id,
        filename,
    )

    try:
        # Open independent database session for background task execution
        with get_db_session() as db:
            proc.process_staged_dataset(
                file_id=file_id,
                staged_path=staged_path,
                filename=filename,
                file_type=FileType(file_type),
                db=db,
                staging=staging,
            )
        logger.info(
            "Background processing task completed successfully for file_id=%s",
            file_id,
        )
    except Exception as err:
        logger.warning(
            "Background processing task failed for file_id=%s: %s",
            file_id,
            err,
        )
    finally:
        # Guaranteed staging cleanup after background processing finishes
        staging.cleanup()


class ProcessingExecutor:
    """Execution boundary coordinating the dispatch of file processing jobs.

    Decouples the HTTP/API transport layer from the underlying execution strategy.
    Supports synchronous execution within the active request context as well as
    asynchronous background task execution using FastAPI BackgroundTasks.
    """

    def __init__(self, processor: FileProcessorProtocol | None = None) -> None:
        self.processor = processor or FileProcessingService()

    def submit_sync(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> FileRecord:
        """Synchronously execute file upload, measurement, and persistence.

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
        logger.info("Synchronous processing execution started for filename=%s", filename)

        try:
            file_record = self.processor.process_file_upload(upload_file=upload_file, db=db)
            logger.info(
                "Synchronous processing execution completed for file_id=%s (status=%s, features=%d)",
                file_record.id,
                file_record.status,
                file_record.feature_count,
            )
            return file_record
        except Exception as err:
            logger.warning(
                "Synchronous processing execution failed for filename=%s: %s",
                filename,
                err,
            )
            raise

    def submit_background(
        self,
        upload_file: UploadFile,
        db: Session,
        background_tasks: BackgroundTasks,
    ) -> FileRecord:
        """Stage file upload, initialize FileRecord (PROCESSING), and schedule background task.

        Args:
            upload_file: Uploaded file payload from FastAPI.
            db: Request database session for initial FileRecord persistence.
            background_tasks: FastAPI BackgroundTasks instance to schedule execution on.

        Returns:
            Initial FileRecord in PROCESSING status.
        """
        file_record, staging, saved_path, file_type = self.processor.prepare_upload_staging(
            upload_file=upload_file, db=db
        )

        logger.info(
            "Scheduling background processing task for file_id=%s filename=%s",
            file_record.id,
            file_record.filename,
        )

        background_tasks.add_task(
            run_background_processing_task,
            file_id=file_record.id,
            staged_path=saved_path,
            filename=file_record.filename,
            file_type=file_type.value,
            staging=staging,
            processor=self.processor,
        )

        return file_record

    def submit(
        self,
        upload_file: UploadFile,
        db: Session,
        background_tasks: BackgroundTasks | None = None,
    ) -> FileRecord:
        """Dispatch processing job according to whether background_tasks is supplied."""
        if background_tasks is not None:
            return self.submit_background(
                upload_file=upload_file,
                db=db,
                background_tasks=background_tasks,
            )
        return self.submit_sync(upload_file=upload_file, db=db)


def get_processing_executor() -> ProcessingExecutor:
    """FastAPI dependency provider for ProcessingExecutor."""
    return ProcessingExecutor()
