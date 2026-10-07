"""Comprehensive tests for Phase 5.2 failure isolation, transaction integrity, and observability."""

import io
import logging
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FeatureRecord, FileRecord, FileStatus, MeasurementRecord
from app.db.session import get_db_session
from app.services.file_processing import FileProcessingService, FileType
from app.storage.staging import StagingArea

SAMPLE_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Parity Test</name>
    <Placemark>
      <name>Parcel Alpha</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5900,12.9700,0
              77.5910,12.9700,0
              77.5910,12.9710,0
              77.5900,12.9710,0
              77.5900,12.9700,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""


@pytest.mark.anyio
async def test_transaction_rollback_leaves_no_partial_features(
    test_db_session: Session, tmp_path: Path
):
    """Verify that an exception during feature/measurement persistence rolls back completely."""
    # 1. Create a staged KML file
    staging = StagingArea(auto_cleanup=False)
    staged_path = staging.save_upload(SAMPLE_KML, "rollback_test.kml")

    file_record = FileRecord(
        id=staging.operation_id,
        filename="rollback_test.kml",
        file_type=FileType.KML.value,
        status=FileStatus.PROCESSING.value,
    )
    test_db_session.add(file_record)
    test_db_session.commit()
    test_db_session.refresh(file_record)

    service = FileProcessingService()

    # Monkeypatch measurement_engine to raise an unexpected exception during measurement
    def faulty_measure(features):
        raise RuntimeError("Simulated mid-processing database or calculation failure")

    service.measurement_engine.measure_dataset = faulty_measure

    with pytest.raises(RuntimeError):
        service.process_staged_dataset(
            file_id=file_record.id,
            staged_path=str(staged_path),
            filename=file_record.filename,
            file_type=FileType.KML,
            db=test_db_session,
            staging=staging,
        )

    staging.cleanup()

    # 2. Verify database state: FileRecord is FAILED and 0 features/measurements exist
    with get_db_session() as verify_db:
        refreshed = verify_db.scalar(select(FileRecord).where(FileRecord.id == file_record.id))
        assert refreshed is not None
        assert refreshed.status == "FAILED"
        assert "Simulated mid-processing" in refreshed.error_message

        feature_count = verify_db.scalar(
            select(func.count())
            .select_from(FeatureRecord)
            .where(FeatureRecord.file_id == file_record.id)
        )
        assert feature_count == 0

        meas_count = verify_db.scalar(select(func.count()).select_from(MeasurementRecord))
        assert meas_count == 0


@pytest.mark.anyio
async def test_sync_async_parity(async_client: AsyncClient):
    """Verify that synchronous and asynchronous processing produce identical database results."""
    # 1. Upload synchronously
    sync_resp = await async_client.post(
        "/api/files/",
        files={
            "file": ("parity.kml", io.BytesIO(SAMPLE_KML), "application/vnd.google-earth.kml+xml")
        },
    )
    assert sync_resp.status_code == 201
    sync_data = sync_resp.json()
    sync_id = sync_data["id"]

    # 2. Upload asynchronously
    async_resp = await async_client.post(
        "/api/files/?async_mode=true",
        files={
            "file": ("parity.kml", io.BytesIO(SAMPLE_KML), "application/vnd.google-earth.kml+xml")
        },
    )
    assert async_resp.status_code == 202
    async_id = async_resp.json()["id"]

    # 3. Retrieve metadata for both
    sync_meta = (await async_client.get(f"/api/files/{sync_id}/")).json()
    async_meta = (await async_client.get(f"/api/files/{async_id}/")).json()

    assert sync_meta["status"] == async_meta["status"] == "COMPLETED"
    assert sync_meta["feature_count"] == async_meta["feature_count"] == 1
    assert sync_meta["source_crs"] == async_meta["source_crs"] == "EPSG:4326"
    assert sync_meta["calculation_crs"] == async_meta["calculation_crs"]
    assert sync_meta["summary"]["total_area_m2"] == async_meta["summary"]["total_area_m2"]

    # 4. Retrieve measurements for both
    sync_meas = (await async_client.get(f"/api/files/{sync_id}/measurements/")).json()
    async_meas = (await async_client.get(f"/api/files/{async_id}/measurements/")).json()

    assert sync_meas["total"] == async_meas["total"] == 1
    assert (
        sync_meas["items"][0]["measurement"]["value"]
        == async_meas["items"][0]["measurement"]["value"]
    )
    assert (
        sync_meas["items"][0]["measurement"]["unit"]
        == async_meas["items"][0]["measurement"]["unit"]
    )


@pytest.mark.anyio
async def test_measurements_endpoint_after_failure(async_client: AsyncClient):
    """Verify that querying measurements for a FAILED file returns 0 items cleanly."""
    malformed_kml = b"<xml><bad-kml>"
    resp = await async_client.post(
        "/api/files/?async_mode=true",
        files={
            "file": ("bad.kml", io.BytesIO(malformed_kml), "application/vnd.google-earth.kml+xml")
        },
    )
    assert resp.status_code == 202
    file_id = resp.json()["id"]

    # Verify status is FAILED
    status_resp = await async_client.get(f"/api/files/{file_id}/")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "FAILED"

    # Verify measurements endpoint returns 0 items without 500 error
    meas_resp = await async_client.get(f"/api/files/{file_id}/measurements/")
    assert meas_resp.status_code == 200
    meas_data = meas_resp.json()
    assert meas_data["total"] == 0
    assert meas_data["items"] == []


def test_idempotent_reprocessing(test_db_session: Session):
    """Verify that re-processing an existing file_id does not create duplicate FeatureRecords."""
    staging = StagingArea(auto_cleanup=False)
    staged_path = staging.save_upload(SAMPLE_KML, "idempotent.kml")

    file_record = FileRecord(
        id=staging.operation_id,
        filename="idempotent.kml",
        file_type=FileType.KML.value,
        status=FileStatus.PROCESSING.value,
    )
    test_db_session.add(file_record)
    test_db_session.commit()

    service = FileProcessingService()

    # Pass 1
    service.process_staged_dataset(
        file_id=file_record.id,
        staged_path=str(staged_path),
        filename=file_record.filename,
        file_type=FileType.KML,
        db=test_db_session,
        staging=staging,
    )

    feat_count_1 = test_db_session.scalar(
        select(func.count())
        .select_from(FeatureRecord)
        .where(FeatureRecord.file_id == file_record.id)
    )
    assert feat_count_1 == 1

    # Pass 2 (Re-processing same file_id)
    service.process_staged_dataset(
        file_id=file_record.id,
        staged_path=str(staged_path),
        filename=file_record.filename,
        file_type=FileType.KML,
        db=test_db_session,
        staging=staging,
    )

    feat_count_2 = test_db_session.scalar(
        select(func.count())
        .select_from(FeatureRecord)
        .where(FeatureRecord.file_id == file_record.id)
    )
    assert feat_count_2 == 1  # Still exactly 1, no duplicate rows

    meas_count = test_db_session.scalar(select(func.count()).select_from(MeasurementRecord))
    assert meas_count == 1  # Still exactly 1 measurement record, no orphans

    staging.cleanup()


@pytest.mark.anyio
async def test_observability_lifecycle_logging(
    caplog: pytest.LogCaptureFixture, async_client: AsyncClient
):
    """Verify that structured lifecycle log checkpoints are captured during execution."""
    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/api/files/",
            files={
                "file": (
                    "logging_test.kml",
                    io.BytesIO(SAMPLE_KML),
                    "application/vnd.google-earth.kml+xml",
                )
            },
        )
        assert response.status_code == 201

    log_messages = [record.message for record in caplog.records]
    log_text = " ".join(log_messages)

    assert "Processing pipeline initiated" in log_text
    assert "Dataset parsed successfully" in log_text
    assert "Geospatial measurements completed" in log_text
    assert "Processing completed successfully" in log_text
