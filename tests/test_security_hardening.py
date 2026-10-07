"""Comprehensive security and resource-exhaustion hardening tests for Phase 5.1."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.exceptions import ResourceLimitExceededError
from app.geospatial.readers.kml import KMLReader
from app.storage.zip_handler import inspect_and_extract_zip


def test_kml_feature_count_limit_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Test that a KML file with more features than MAX_FEATURES_PER_FILE raises ResourceLimitExceededError."""
    monkeypatch.setattr(get_settings(), "MAX_FEATURES_PER_FILE", 3)

    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark><name>P1</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
        <Placemark><name>P2</name><Point><coordinates>77.6,12.9</coordinates></Point></Placemark>
        <Placemark><name>P3</name><Point><coordinates>77.7,12.9</coordinates></Point></Placemark>
        <Placemark><name>P4</name><Point><coordinates>77.8,12.9</coordinates></Point></Placemark>
      </Document>
    </kml>
    """
    kml_path = tmp_path / "excess_features.kml"
    kml_path.write_text(kml_content)

    reader = KMLReader()
    with pytest.raises(ResourceLimitExceededError) as exc_info:
        list(reader.read_features(kml_path))

    assert "exceeding maximum limit" in str(exc_info.value.message)


@pytest.mark.anyio
async def test_api_upload_kml_feature_count_limit_rejection(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Test that API endpoint returns 400 Bad Request when feature count limit is exceeded."""
    monkeypatch.setattr(get_settings(), "MAX_FEATURES_PER_FILE", 2)

    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark><name>P1</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
        <Placemark><name>P2</name><Point><coordinates>77.6,12.9</coordinates></Point></Placemark>
        <Placemark><name>P3</name><Point><coordinates>77.7,12.9</coordinates></Point></Placemark>
      </Document>
    </kml>
    """
    response = await async_client.post(
        "/api/files/",
        files={
            "file": (
                "features_overflow.kml",
                io.BytesIO(kml_content.encode()),
                "application/vnd.google-earth.kml+xml",
            )
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert "exceeding maximum limit" in data["detail"]
    assert "/tmp/" not in json.dumps(data)


def test_kml_coordinate_count_limit_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Test that a geometry with more coordinates than MAX_COORDINATES_PER_GEOMETRY raises ResourceLimitExceededError."""
    monkeypatch.setattr(get_settings(), "MAX_COORDINATES_PER_GEOMETRY", 4)

    # 5 coordinates
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark>
          <name>Oversized Line</name>
          <LineString>
            <coordinates>
              77.1,12.1 77.2,12.2 77.3,12.3 77.4,12.4 77.5,12.5
            </coordinates>
          </LineString>
        </Placemark>
      </Document>
    </kml>
    """
    kml_path = tmp_path / "excess_coords.kml"
    kml_path.write_text(kml_content)

    reader = KMLReader()
    with pytest.raises(ResourceLimitExceededError) as exc_info:
        list(reader.read_features(kml_path))

    assert "coordinate count exceeds maximum limit" in str(exc_info.value.message)


def test_kml_non_finite_coordinates_discarded(tmp_path: Path):
    """Test that non-finite coordinates (NaN, Inf) in KML are safely skipped without crash."""
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark>
          <name>NaN Line</name>
          <LineString>
            <coordinates>
              77.1,12.1 nan,inf 77.3,12.3
            </coordinates>
          </LineString>
        </Placemark>
      </Document>
    </kml>
    """
    kml_path = tmp_path / "nan_coords.kml"
    kml_path.write_text(kml_content)

    reader = KMLReader()
    features = list(reader.read_features(kml_path))
    assert len(features) == 1
    # LineString should only have the 2 valid finite coordinates
    assert features[0].geometry is not None
    assert len(features[0].geometry.coords) == 2


def test_zip_duplicate_or_colliding_entries_rejected(tmp_path: Path):
    """Test that ZIP archives with duplicate entry names or case collisions are rejected."""
    zip_path = tmp_path / "duplicate_entries.zip"
    extract_to = tmp_path / "extracted"

    # Create zip with duplicate entries
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("layer.shp", b"dummy shp")
        zf.writestr("LAYER.SHP", b"conflicting duplicate shp")

    with pytest.raises(Exception) as exc_info:
        inspect_and_extract_zip(zip_path=zip_path, extract_to=extract_to)

    assert "Duplicate or colliding entry" in str(exc_info.value)


@pytest.mark.anyio
async def test_api_error_responses_do_not_disclose_system_internals(async_client: AsyncClient):
    """Verify that error responses for various failure modes never leak system paths, tracebacks, or SQL."""
    # 1. Non-existent file
    r1 = await async_client.get("/api/files/00000000-0000-0000-0000-000000000000/")
    assert r1.status_code == 404
    body1 = json.dumps(r1.json())
    assert "/tmp/" not in body1
    assert "Traceback" not in body1
    assert "sqlite" not in body1

    # 2. Malformed XML upload
    r2 = await async_client.post(
        "/api/files/",
        files={
            "file": (
                "malformed.kml",
                io.BytesIO(b"<xml><unclosed>"),
                "application/vnd.google-earth.kml+xml",
            )
        },
    )
    assert r2.status_code == 400
    body2 = json.dumps(r2.json())
    assert "/tmp/" not in body2
    assert "Traceback" not in body2
    assert "geomeasure_staging" not in body2

    # 3. Malformed UUID parameter
    r3 = await async_client.get("/api/files/not-a-valid-uuid/")
    assert r3.status_code == 400
    body3 = json.dumps(r3.json())
    assert "/tmp/" not in body3
    assert "Traceback" not in body3


@pytest.mark.anyio
async def test_async_staging_cleanup_after_failure(async_client: AsyncClient, tmp_path: Path):
    """Verify that staging files are cleaned up even when background processing fails."""
    # Upload corrupt zip in async mode
    corrupt_zip = b"PK\x03\x04corrupt content that is not a valid zip"
    response = await async_client.post(
        "/api/files/?async_mode=true",
        files={"file": ("async_corrupt.zip", io.BytesIO(corrupt_zip), "application/zip")},
    )
    assert response.status_code == 202
    file_id = response.json()["id"]

    # Check that file status becomes FAILED
    status_resp = await async_client.get(f"/api/files/{file_id}/")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "FAILED"
    assert status_resp.json()["error_message"] is not None

    # Verify staging directory for this operation_id does not exist
    settings = get_settings()
    staging_dir = Path(settings.UPLOAD_DIR) / file_id
    assert not staging_dir.exists()


def test_sanitize_json_dict_payload_bounds(monkeypatch: pytest.MonkeyPatch):
    """Test that sanitize_json_dict caps oversized property strings to protect against memory exhaustion."""
    from app.services.file_processing import sanitize_json_dict

    monkeypatch.setattr(get_settings(), "MAX_PROPERTY_PAYLOAD_BYTES", 50)
    huge_string = "A" * 1000
    sanitized = sanitize_json_dict({"description": huge_string, "count": 42})
    assert len(sanitized["description"]) <= 50
    assert sanitized["count"] == 42


def test_shapefile_feature_count_limit_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Test that ShapefileReader raises ResourceLimitExceededError when layer exceeds limit."""
    import fiona
    from shapely.geometry import Polygon, mapping

    from app.geospatial.readers.shapefile import ShapefileReader

    monkeypatch.setattr(get_settings(), "MAX_FEATURES_PER_FILE", 2)

    shp_path = tmp_path / "test_limit.shp"
    schema = {"geometry": "Polygon", "properties": {"id": "int"}}
    crs = "EPSG:4326"

    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])

    with fiona.open(str(shp_path), "w", driver="ESRI Shapefile", crs=crs, schema=schema) as dst:
        for i in range(5):
            dst.write({"geometry": mapping(poly), "properties": {"id": i}})

    reader = ShapefileReader()
    with pytest.raises(ResourceLimitExceededError) as exc_info:
        list(reader.read_features(shp_path))

    assert "exceeding maximum limit" in str(exc_info.value.message)
