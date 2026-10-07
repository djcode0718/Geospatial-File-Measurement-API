"""Integration tests for Phase 4.2 asynchronous background execution and lifecycle."""

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FeatureRecord, FileRecord, FileStatus
from app.db.session import get_db_session
from app.services.executor import run_background_processing_task
from app.services.file_processing import FileProcessingService
from app.storage.staging import StagingArea

SAMPLE_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Async Survey</name>
    <Placemark>
      <name>Parcel 101</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              78.4800,17.3800,0
              78.4810,17.3800,0
              78.4810,17.3810,0
              78.4800,17.3810,0
              78.4800,17.3800,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
    <Placemark>
      <name>Access Road</name>
      <LineString>
        <coordinates>
          78.4800,17.3800,0
          78.4900,17.3800,0
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Marker Post</name>
      <Point>
        <coordinates>78.4850,17.3850,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""


@pytest.mark.asyncio
async def test_async_upload_returns_202_with_processing_status(async_client: AsyncClient):
    """POST /api/files/?async_mode=true immediately returns 202 Accepted and status=PROCESSING."""
    response = await async_client.post(
        "/api/files/?async_mode=true",
        files={"file": ("survey_async.kml", SAMPLE_KML, "application/vnd.google-earth.kml+xml")},
    )

    assert response.status_code == 202
    data = response.json()

    assert data["id"] is not None
    assert data["filename"] == "survey_async.kml"
    assert data["file_type"] == "KML"
    assert data["status"] in (FileStatus.PROCESSING.value, FileStatus.COMPLETED.value)


@pytest.mark.asyncio
async def test_async_upload_full_lifecycle_and_measurements(async_client: AsyncClient):
    """Full async lifecycle: POST (202) -> background execution -> GET metadata (COMPLETED) -> GET measurements."""
    # 1. Upload in async mode
    upload_res = await async_client.post(
        "/api/files/?async_mode=true",
        files={"file": ("pipeline_async.kml", SAMPLE_KML, "application/vnd.google-earth.kml+xml")},
    )
    assert upload_res.status_code == 202
    file_id = upload_res.json()["id"]

    # 2. In httpx / FastAPI test client, background tasks execute before or upon request completion
    meta_res = await async_client.get(f"/api/files/{file_id}/")
    assert meta_res.status_code == 200
    meta = meta_res.json()

    assert meta["status"] == "COMPLETED"
    assert meta["feature_count"] == 3
    assert meta["calculation_crs"] == "EPSG:32644"
    assert meta["summary"]["polygon_count"] == 1
    assert meta["summary"]["linestring_count"] == 1
    assert meta["summary"]["point_count"] == 1
    assert meta["summary"]["total_area_m2"] > 0
    assert meta["summary"]["total_length_m"] > 0

    # 3. Retrieve measurements
    meas_res = await async_client.get(f"/api/files/{file_id}/measurements/")
    assert meas_res.status_code == 200
    meas = meas_res.json()

    assert meas["total"] == 3
    assert len(meas["items"]) == 3
    assert meas["items"][0]["measurement"]["type"] == "area"
    assert meas["items"][1]["measurement"]["type"] == "length"
    assert meas["items"][2]["measurement"] is None


@pytest.mark.asyncio
async def test_async_upload_failure_lifecycle(async_client: AsyncClient):
    """An invalid/corrupt dataset processed in background correctly transitions to FAILED status."""
    corrupt_kml = b"NOT VALID XML <<< >>>"

    upload_res = await async_client.post(
        "/api/files/?async_mode=true",
        files={"file": ("corrupt.kml", corrupt_kml, "application/vnd.google-earth.kml+xml")},
    )
    assert upload_res.status_code == 202
    file_id = upload_res.json()["id"]

    meta_res = await async_client.get(f"/api/files/{file_id}/")
    assert meta_res.status_code == 200
    meta = meta_res.json()

    assert meta["status"] == "FAILED"
    assert meta["error_message"] is not None


def test_background_task_worker_independent_session(test_db_session: Session):
    """run_background_processing_task operates with an independent session and cleans staging."""
    service = FileProcessingService()

    # 1. Setup in test session
    file_record = FileRecord(
        id="async-test-worker-uuid",
        filename="worker_test.kml",
        file_type="KML",
        status=FileStatus.PROCESSING.value,
    )
    test_db_session.add(file_record)
    test_db_session.commit()

    staging = StagingArea(operation_id=file_record.id)
    saved_path = staging.save_upload(raw_bytes=SAMPLE_KML, filename="worker_test.kml")

    staging_dir = staging.staging_dir
    assert Path(staging_dir).exists()

    # 2. Run background task function (which creates its own DB session)
    run_background_processing_task(
        file_id=file_record.id,
        staged_path=str(saved_path),
        filename=file_record.filename,
        file_type="KML",
        staging=staging,
        processor=service,
    )

    # 3. Verify staging was cleaned up
    assert not Path(staging_dir).exists()

    # 4. Verify results persisted via independent session
    with get_db_session() as verify_db:
        updated = verify_db.scalar(select(FileRecord).where(FileRecord.id == file_record.id))
        assert updated is not None
        assert updated.status == FileStatus.COMPLETED.value
        assert updated.feature_count == 3

        features = list(
            verify_db.scalars(
                select(FeatureRecord).where(FeatureRecord.file_id == file_record.id)
            ).all()
        )
        assert len(features) == 3


@pytest.mark.asyncio
async def test_async_and_sync_produce_identical_measurements(async_client: AsyncClient):
    """Verify that synchronous and asynchronous execution paths produce identical feature measurements."""
    # 1. Synchronous upload
    sync_res = await async_client.post(
        "/api/files/?async_mode=false",
        files={"file": ("sync_test.kml", SAMPLE_KML, "application/vnd.google-earth.kml+xml")},
    )
    assert sync_res.status_code == 201
    sync_id = sync_res.json()["id"]

    # 2. Asynchronous upload
    async_res = await async_client.post(
        "/api/files/?async_mode=true",
        files={"file": ("async_test.kml", SAMPLE_KML, "application/vnd.google-earth.kml+xml")},
    )
    assert async_res.status_code == 202
    async_id = async_res.json()["id"]

    # 3. Retrieve measurements for both
    sync_meas = (await async_client.get(f"/api/files/{sync_id}/measurements/")).json()
    async_meas = (await async_client.get(f"/api/files/{async_id}/measurements/")).json()

    assert sync_meas["total"] == async_meas["total"]
    assert len(sync_meas["items"]) == len(async_meas["items"])

    for s_item, a_item in zip(sync_meas["items"], async_meas["items"], strict=True):
        assert s_item["geometry_type"] == a_item["geometry_type"]
        assert s_item["geometry"] == a_item["geometry"]
        assert s_item["status"] == a_item["status"]
        assert s_item["measurement"] == a_item["measurement"]
