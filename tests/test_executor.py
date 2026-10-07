"""Unit and integration tests for Phase 4.1 processing boundary and execution abstraction."""

import io
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.exceptions import UnsupportedFileTypeError
from app.db.models import FileRecord, FileStatus, FileType
from app.main import app
from app.services.executor import ProcessingExecutor, get_processing_executor
from app.services.file_processing import FileProcessingService


def create_dummy_file_record(file_id: str | None = None) -> FileRecord:
    """Helper to create a representative in-memory FileRecord for mock tests."""
    fid = file_id or str(uuid.uuid4())
    return FileRecord(
        id=fid,
        filename="test_dataset.zip",
        file_type=FileType.SHAPEFILE_ZIP.value,
        file_size_bytes=1024,
        status=FileStatus.COMPLETED.value,
        feature_count=10,
        source_crs="EPSG:4326",
        calculation_crs="EPSG:32643",
        summary_metrics={"total_features": 10, "measured_features": 10},
        error_message=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_executor_submit_invokes_processor_and_returns_record():
    """ProcessingExecutor.submit() delegates to processor.process_file_upload and returns FileRecord."""
    mock_processor = MagicMock(spec=FileProcessingService)
    dummy_record = create_dummy_file_record()
    mock_processor.process_file_upload.return_value = dummy_record

    executor = ProcessingExecutor(processor=mock_processor)

    dummy_upload = UploadFile(
        file=io.BytesIO(b"dummy zip content"),
        filename="cadastral.zip",
    )
    mock_db = MagicMock(spec=Session)

    result = executor.submit(upload_file=dummy_upload, db=mock_db)

    assert result == dummy_record
    mock_processor.process_file_upload.assert_called_once_with(
        upload_file=dummy_upload,
        db=mock_db,
    )


def test_executor_propagates_processor_exceptions():
    """ProcessingExecutor.submit() preserves and re-raises domain and runtime exceptions."""
    mock_processor = MagicMock(spec=FileProcessingService)
    mock_processor.process_file_upload.side_effect = UnsupportedFileTypeError(
        "Unsupported file format"
    )

    executor = ProcessingExecutor(processor=mock_processor)

    dummy_upload = UploadFile(
        file=io.BytesIO(b"invalid"),
        filename="invalid.txt",
    )
    mock_db = MagicMock(spec=Session)

    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        executor.submit(upload_file=dummy_upload, db=mock_db)

    assert "Unsupported file format" in str(exc_info.value)


def test_executor_default_instantiation():
    """ProcessingExecutor instantiates a default FileProcessingService if none provided."""
    executor = ProcessingExecutor()
    assert isinstance(executor.processor, FileProcessingService)


def test_get_processing_executor_dependency():
    """FastAPI dependency provider get_processing_executor returns a ProcessingExecutor."""
    executor = get_processing_executor()
    assert isinstance(executor, ProcessingExecutor)
    assert isinstance(executor.processor, FileProcessingService)


@pytest.mark.asyncio
async def test_api_upload_with_mock_executor_dependency_override(async_client: AsyncClient):
    """Verify POST /api/files/ can be tested with a mocked executor via FastAPI dependency injection."""
    mock_record = create_dummy_file_record()

    mock_executor = MagicMock(spec=ProcessingExecutor)
    mock_executor.submit.return_value = mock_record

    # Override the FastAPI dependency
    app.dependency_overrides[get_processing_executor] = lambda: mock_executor

    try:
        response = await async_client.post(
            "/api/files/",
            files={"file": ("mock_dataset.zip", b"fake zip bytes", "application/zip")},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == mock_record.id
        assert data["filename"] == mock_record.filename
        assert data["status"] == "COMPLETED"
        assert data["feature_count"] == 10

        assert mock_executor.submit.called
    finally:
        app.dependency_overrides.pop(get_processing_executor, None)


def test_processor_isolation_from_fastapi(test_db_session: Session):
    """FileProcessingService can be invoked and tested completely isolated from FastAPI HTTP context."""
    kml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Unit Square</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              0,0,0
              0.01,0,0
              0.01,0.01,0
              0,0.01,0
              0,0,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>"""

    upload_file = UploadFile(
        file=io.BytesIO(kml_bytes),
        filename="isolated_test.kml",
    )

    service = FileProcessingService()
    record = service.process_file_upload(upload_file=upload_file, db=test_db_session)

    assert record.id is not None
    assert record.filename == "isolated_test.kml"
    assert record.file_type == FileType.KML.value
    assert record.status == FileStatus.COMPLETED.value
    assert record.feature_count == 1
    assert record.calculation_crs is not None
