"""Integration tests for Phase 3.2 file metadata retrieval endpoint (GET /api/files/{id}/)."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FileRecord, FileStatus, FileType


@pytest.fixture
def populated_file_record(test_db_session: Session) -> FileRecord:
    """Create and persist a representative completed FileRecord in the test database."""
    file_id = str(uuid.uuid4())
    summary = {
        "total_features": 120,
        "measured_features": 105,
        "skipped_features": 15,
        "invalid_features": 0,
        "unsupported_features": 0,
        "failed_features": 0,
        "polygon_count": 80,
        "linestring_count": 25,
        "point_count": 15,
        "unsupported_count": 0,
        "total_area_m2": 142050.25,
        "total_length_m": 8432.10,
    }

    record = FileRecord(
        id=file_id,
        filename="cadastral_survey.zip",
        file_type=FileType.SHAPEFILE_ZIP.value,
        file_size_bytes=18432,
        status=FileStatus.COMPLETED.value,
        feature_count=120,
        source_crs="EPSG:32643",
        calculation_crs="EPSG:32643",
        summary_metrics=summary,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_db_session.add(record)
    test_db_session.commit()
    test_db_session.refresh(record)
    return record


@pytest.mark.asyncio
async def test_get_file_metadata_success(
    async_client: AsyncClient, populated_file_record: FileRecord
):
    """GET /api/files/{id}/ returns full persisted metadata, metrics, and CRS for a completed file."""
    file_id = populated_file_record.id
    response = await async_client.get(f"/api/files/{file_id}/")

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == file_id
    assert data["filename"] == "cadastral_survey.zip"
    assert data["file_type"] == "SHAPEFILE_ZIP"
    assert data["file_size_bytes"] == 18432
    assert data["status"] == "COMPLETED"
    assert data["feature_count"] == 120
    assert data["source_crs"] == "EPSG:32643"
    assert data["calculation_crs"] == "EPSG:32643"
    assert data["error_message"] is None
    assert "created_at" in data
    assert "updated_at" in data

    summary = data["summary"]
    assert summary["total_features"] == 120
    assert summary["measured_features"] == 105
    assert summary["polygon_count"] == 80
    assert summary["total_area_m2"] == 142050.25
    assert summary["total_length_m"] == 8432.10


@pytest.mark.asyncio
async def test_get_file_metadata_not_found(async_client: AsyncClient):
    """GET /api/files/{id}/ with a valid but non-existent UUID returns 404 with RFC 7807 error."""
    random_uuid = str(uuid.uuid4())
    response = await async_client.get(f"/api/files/{random_uuid}/")

    assert response.status_code == 404
    data = response.json()
    assert data["status"] == 404
    assert data["title"] == "Not Found"
    assert random_uuid in data["detail"]
    assert "errors.geomeasure.internal" in data["type"]


@pytest.mark.asyncio
async def test_get_file_metadata_invalid_uuid_syntax(async_client: AsyncClient):
    """GET /api/files/{id}/ with malformed UUID returns 400 Bad Request."""
    response = await async_client.get("/api/files/not-a-valid-uuid/")

    assert response.status_code == 400
    data = response.json()
    assert data["status"] == 400
    assert data["title"] == "Bad Request"
    assert "validation" in data["type"] or "invalid" in data["detail"].lower()


@pytest.mark.asyncio
async def test_get_file_metadata_all_lifecycle_statuses(
    async_client: AsyncClient, test_db_session: Session
):
    """Verify metadata retrieval across all possible lifecycle statuses."""
    statuses = [
        FileStatus.PROCESSING,
        FileStatus.COMPLETED,
        FileStatus.COMPLETED_WITH_WARNINGS,
        FileStatus.FAILED,
    ]

    for st in statuses:
        rec_id = str(uuid.uuid4())
        err_msg = "Corrupted shapefile archive structure" if st == FileStatus.FAILED else None
        record = FileRecord(
            id=rec_id,
            filename=f"test_{st.value.lower()}.zip",
            file_type=FileType.SHAPEFILE_ZIP.value,
            file_size_bytes=4096,
            status=st.value,
            feature_count=10 if st != FileStatus.FAILED else 0,
            source_crs="EPSG:4326" if st != FileStatus.FAILED else None,
            calculation_crs="EPSG:32644" if st != FileStatus.FAILED else None,
            error_message=err_msg,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        test_db_session.add(record)
        test_db_session.commit()

        response = await async_client.get(f"/api/files/{rec_id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == st.value
        assert data["error_message"] == err_msg


@pytest.mark.asyncio
async def test_get_file_metadata_nullable_crs(async_client: AsyncClient, test_db_session: Session):
    """Verify that nullable source_crs and calculation_crs are preserved as null without fabricating values."""
    rec_id = str(uuid.uuid4())
    record = FileRecord(
        id=rec_id,
        filename="unprojected.kml",
        file_type=FileType.KML.value,
        file_size_bytes=1024,
        status=FileStatus.COMPLETED.value,
        feature_count=0,
        source_crs=None,
        calculation_crs=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_db_session.add(record)
    test_db_session.commit()

    response = await async_client.get(f"/api/files/{rec_id}/")
    assert response.status_code == 200
    data = response.json()
    assert data["source_crs"] is None
    assert data["calculation_crs"] is None


@pytest.mark.asyncio
async def test_get_file_metadata_is_read_only_and_idempotent(
    async_client: AsyncClient, populated_file_record: FileRecord, test_db_session: Session
):
    """Repeated GET calls must not mutate the database record or alter updated_at timestamps."""
    file_id = populated_file_record.id

    # First request
    res1 = await async_client.get(f"/api/files/{file_id}/")
    assert res1.status_code == 200
    data1 = res1.json()

    # Second request
    res2 = await async_client.get(f"/api/files/{file_id}/")
    assert res2.status_code == 200
    data2 = res2.json()

    # Verify identical output
    assert data1 == data2

    # Verify database record is untouched
    db_rec = test_db_session.scalar(select(FileRecord).where(FileRecord.id == file_id))
    assert db_rec is not None
    assert db_rec.status == FileStatus.COMPLETED.value
