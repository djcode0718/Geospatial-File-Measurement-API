"""Integration tests for Phase 3.1 file upload API (POST /api/files/)."""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import fiona
import pytest
from httpx import AsyncClient
from shapely.geometry import Polygon, mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import FeatureRecord, FeatureStatus, FileRecord, FileStatus


@pytest.fixture
def sample_kml_bytes() -> bytes:
    """Generate a valid KML payload containing a Polygon, a LineString, and a Point."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Test Survey</name>
    <Placemark>
      <name>Zone Alpha</name>
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
      <name>Tower 1</name>
      <Point>
        <coordinates>78.4850,17.3850,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>
"""


@pytest.fixture
def shapefile_zip_bytes() -> bytes:
    """Generate a valid in-memory ZIP archive containing an ESRI Shapefile with .prj."""
    temp_dir = Path(tempfile.mkdtemp())
    shp_path = temp_dir / "parcels.shp"

    schema = {
        "geometry": "Polygon",
        "properties": {"name": "str", "area_id": "int"},
    }

    # Write shapefile in EPSG:32643 (UTM 43N)
    with fiona.open(
        shp_path,
        mode="w",
        driver="ESRI Shapefile",
        schema=schema,
        crs="EPSG:32643",
    ) as layer:
        poly1 = Polygon([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
        layer.write(
            {
                "geometry": mapping(poly1),
                "properties": {"name": "Plot A", "area_id": 101},
            }
        )
        poly2 = Polygon([(200, 200), (250, 200), (250, 250), (200, 250), (200, 200)])
        layer.write(
            {
                "geometry": mapping(poly2),
                "properties": {"name": "Plot B", "area_id": 102},
            }
        )

    # Pack into zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
            comp_file = temp_dir / f"parcels{ext}"
            if comp_file.exists():
                zf.write(comp_file, arcname=comp_file.name)

    shutil.rmtree(temp_dir, ignore_errors=True)
    return zip_buffer.getvalue()


@pytest.mark.asyncio
async def test_upload_kml_success(
    async_client: AsyncClient, test_db_session: Session, sample_kml_bytes: bytes
):
    """Test successful KML upload, measurement calculation, and persistence."""
    files = {"file": ("survey.kml", sample_kml_bytes, "application/vnd.google-earth.kml+xml")}

    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 201
    data = response.json()

    assert "id" in data
    file_id = data["id"]
    assert data["filename"] == "survey.kml"
    assert data["file_type"] == "KML"
    assert data["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
    assert data["feature_count"] == 3
    assert data["source_crs"] == "EPSG:4326"
    assert data["calculation_crs"] == "EPSG:32644"  # 78.48°E resolved to UTM 44N

    summary = data["summary"]
    assert summary["total_features"] == 3
    assert summary["polygon_count"] == 1
    assert summary["linestring_count"] == 1
    assert summary["point_count"] == 1
    assert summary["measured_features"] == 2
    assert summary["skipped_features"] == 1
    assert summary["total_area_m2"] > 0.0
    assert summary["total_length_m"] > 0.0

    # Query DB to verify persistence consistency
    db_file = test_db_session.scalar(select(FileRecord).where(FileRecord.id == file_id))
    assert db_file is not None
    assert db_file.feature_count == 3
    assert db_file.status in (FileStatus.COMPLETED.value, FileStatus.COMPLETED_WITH_WARNINGS.value)

    # Verify features and measurements
    features = list(
        test_db_session.scalars(
            select(FeatureRecord)
            .where(FeatureRecord.file_id == file_id)
            .order_by(FeatureRecord.feature_index)
        )
    )
    assert len(features) == 3

    # Feature 0: Polygon
    assert features[0].geometry_type == "Polygon"
    assert features[0].status == FeatureStatus.SUCCESS.value
    assert features[0].measurement is not None
    assert features[0].measurement.measurement_type == "area"
    assert features[0].measurement.measurement_value > 0.0
    assert features[0].measurement.unit == "square_meters"

    # Feature 1: LineString
    assert features[1].geometry_type == "LineString"
    assert features[1].status == FeatureStatus.SUCCESS.value
    assert features[1].measurement is not None
    assert features[1].measurement.measurement_type == "length"
    assert features[1].measurement.measurement_value > 0.0
    assert features[1].measurement.unit == "meters"

    # Feature 2: Point
    assert features[2].geometry_type == "Point"
    assert features[2].status == FeatureStatus.SKIPPED_NOT_APPLICABLE.value
    assert features[2].measurement is None


@pytest.mark.asyncio
async def test_upload_shapefile_zip_success(
    async_client: AsyncClient, test_db_session: Session, shapefile_zip_bytes: bytes
):
    """Test successful Shapefile ZIP upload with already projected EPSG:32643 coordinates."""
    files = {"file": ("parcels.zip", shapefile_zip_bytes, "application/zip")}

    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 201
    data = response.json()

    assert data["filename"] == "parcels.zip"
    assert data["file_type"] == "SHAPEFILE_ZIP"
    assert data["status"] == "COMPLETED"
    assert data["feature_count"] == 2
    assert data["source_crs"] == "EPSG:32643"
    assert data["calculation_crs"] == "EPSG:32643"

    summary = data["summary"]
    assert summary["polygon_count"] == 2
    assert summary["measured_features"] == 2
    # Plot A (100x100 = 10,000 m²) + Plot B (50x50 = 2,500 m²) = 12,500 m²
    assert summary["total_area_m2"] == pytest.approx(12500.0, rel=1e-3)


@pytest.mark.asyncio
async def test_upload_unsupported_file_extension(async_client: AsyncClient):
    """Uploading a non-supported extension returns 415 Unsupported Media Type."""
    files = {"file": ("dataset.geojson", b'{"type": "FeatureCollection"}', "application/json")}

    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 415
    data = response.json()
    assert data["status"] == 415
    assert "not supported" in data["detail"].lower()


@pytest.mark.asyncio
async def test_upload_corrupt_zip(async_client: AsyncClient):
    """Uploading a corrupted zip file returns 400 Bad Request."""
    files = {"file": ("corrupt.zip", b"PK\x03\x04not-a-valid-zip-data", "application/zip")}

    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 400
    data = response.json()
    assert data["status"] == 400
    assert "archive" in data["detail"].lower() or "zip" in data["detail"].lower()


@pytest.mark.asyncio
async def test_upload_zip_missing_shapefile_companions(async_client: AsyncClient):
    """ZIP containing only .shp without .shx or .dbf returns 400 Bad Request."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("test.shp", b"dummy shp content")

    files = {"file": ("incomplete.zip", zip_buffer.getvalue(), "application/zip")}
    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 400
    data = response.json()
    assert "missing mandatory companion" in data["detail"].lower()


@pytest.mark.asyncio
async def test_upload_malformed_kml(async_client: AsyncClient):
    """Uploading malformed XML in KML returns 400 Bad Request."""
    files = {
        "file": ("broken.kml", b"<kml><Document><unclosed>", "application/vnd.google-earth.kml+xml")
    }

    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 400
    data = response.json()
    assert "xml" in data["detail"].lower()


@pytest.mark.asyncio
async def test_upload_xxe_kml_security_defense(async_client: AsyncClient):
    """KML containing XXE injection is rejected with 400 Bad Request."""
    xxe_kml = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE test [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>&xxe;</Document>
</kml>"""

    files = {"file": ("xxe.kml", xxe_kml, "application/vnd.google-earth.kml+xml")}
    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 400
    data = response.json()
    assert "security violation" in data["detail"].lower() or "entity" in data["detail"].lower()


@pytest.mark.asyncio
async def test_upload_oversized_file(async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """Uploads exceeding configured size limit return 413 Payload Too Large."""
    from app.core import config

    test_settings = config.Settings(MAX_UPLOAD_SIZE_BYTES=100)
    monkeypatch.setattr(config, "get_settings", lambda: test_settings)
    monkeypatch.setattr("app.storage.staging.get_settings", lambda: test_settings)
    monkeypatch.setattr("app.storage.sanitizer.get_settings", lambda: test_settings)

    large_payload = b"A" * 500
    files = {"file": ("toolarge.kml", large_payload, "application/vnd.google-earth.kml+xml")}

    response = await async_client.post("/api/files/", files=files)
    assert response.status_code == 413
    data = response.json()
    assert data["status"] == 413
    assert "exceeds maximum limit" in data["detail"].lower()


@pytest.mark.asyncio
async def test_staging_area_cleaned_up_after_upload(
    async_client: AsyncClient, sample_kml_bytes: bytes
):
    """Verify that staging temporary directories are cleaned up after request completes."""
    staging_root = Path(get_settings().UPLOAD_DIR)

    files = {"file": ("cleanup_test.kml", sample_kml_bytes, "application/vnd.google-earth.kml+xml")}
    response = await async_client.post("/api/files/", files=files)
    assert response.status_code == 201
    file_id = response.json()["id"]

    # Verify directory /tmp/geomeasure_staging/{file_id} no longer exists
    file_staging_dir = staging_root / file_id
    assert not file_staging_dir.exists()


@pytest.mark.asyncio
async def test_upload_point_only_kml(async_client: AsyncClient, test_db_session: Session):
    """Point-only dataset completes successfully with point counts and null measurement values."""
    point_kml = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>P1</name><Point><coordinates>78.4,17.3,0</coordinates></Point></Placemark>
    <Placemark><name>P2</name><Point><coordinates>78.5,17.4,0</coordinates></Point></Placemark>
  </Document>
</kml>"""

    files = {"file": ("points.kml", point_kml, "application/vnd.google-earth.kml+xml")}
    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["feature_count"] == 2
    assert data["summary"]["point_count"] == 2
    assert data["summary"]["measured_features"] == 0
    assert data["summary"]["skipped_features"] == 2


@pytest.mark.asyncio
async def test_upload_shapefile_missing_prj_fails_without_guessing(async_client: AsyncClient):
    """Shapefile lacking .prj fails explicitly without silently guessing EPSG:4326."""
    temp_dir = Path(tempfile.mkdtemp())
    shp_path = temp_dir / "noprj.shp"
    schema = {"geometry": "Polygon", "properties": {"id": "int"}}

    # Write shapefile without CRS (.prj is not created)
    with fiona.open(shp_path, mode="w", driver="ESRI Shapefile", schema=schema, crs=None) as layer:
        layer.write(
            {
                "geometry": mapping(Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])),
                "properties": {"id": 1},
            }
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in [".shp", ".shx", ".dbf"]:
            f = temp_dir / f"noprj{ext}"
            if f.exists():
                zf.write(f, arcname=f.name)

    shutil.rmtree(temp_dir, ignore_errors=True)

    files = {"file": ("noprj.zip", zip_buffer.getvalue(), "application/zip")}
    response = await async_client.post("/api/files/", files=files)

    # When all features fail CRS resolution due to missing CRS, the dataset returns 400 or records failure
    assert response.status_code == 400
    data = response.json()
    assert "source crs is missing" in data["detail"].lower() or "missing" in data["detail"].lower()


@pytest.mark.asyncio
async def test_upload_transaction_rollback_on_persistence_failure(
    async_client: AsyncClient,
    test_db_session: Session,
    sample_kml_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
):
    """If an error occurs during persistence, partial features are rolled back and FileRecord is marked FAILED."""
    from app.services import file_processing

    # Monkeypatch measure_dataset to raise an unexpected exception mid-pipeline
    def forced_failure(*args, **kwargs):
        raise RuntimeError("Simulated internal database persistence failure")

    monkeypatch.setattr(file_processing.MeasurementEngine, "measure_dataset", forced_failure)

    files = {"file": ("fail_test.kml", sample_kml_bytes, "application/vnd.google-earth.kml+xml")}
    response = await async_client.post("/api/files/", files=files)

    assert response.status_code == 500

    # Query DB: FileRecord should be marked FAILED, and 0 FeatureRecords should exist
    failed_file = test_db_session.scalar(
        select(FileRecord).where(FileRecord.filename == "fail_test.kml")
    )
    assert failed_file is not None
    assert failed_file.status == FileStatus.FAILED.value
    assert "Simulated internal database persistence failure" in (failed_file.error_message or "")

    features = list(
        test_db_session.scalars(
            select(FeatureRecord).where(FeatureRecord.file_id == failed_file.id)
        )
    )
    assert len(features) == 0
