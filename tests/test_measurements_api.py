"""Integration tests for Phase 3.3 feature and measurement retrieval endpoint (GET /api/files/{id}/measurements/)."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    FeatureRecord,
    FeatureStatus,
    FileRecord,
    FileStatus,
    FileType,
    MeasurementRecord,
    MeasurementType,
)


@pytest.fixture
def populated_dataset_with_features(
    test_db_session: Session,
) -> tuple[FileRecord, list[FeatureRecord]]:
    """Create and persist a complete file dataset with heterogeneous features and measurements."""
    file_id = str(uuid.uuid4())
    file_record = FileRecord(
        id=file_id,
        filename="cadastral_survey.zip",
        file_type=FileType.SHAPEFILE_ZIP.value,
        file_size_bytes=45000,
        status=FileStatus.COMPLETED.value,
        feature_count=5,
        source_crs="EPSG:4326",
        calculation_crs="EPSG:32643",
        summary_metrics={
            "total_features": 5,
            "measured_features": 4,
            "skipped_features": 1,
            "polygon_count": 2,
            "linestring_count": 2,
            "point_count": 1,
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_db_session.add(file_record)

    features: list[FeatureRecord] = []

    # 1. Feature 0: Polygon (Area)
    feat0 = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=0,
        geometry_type="Polygon",
        geometry_geojson={
            "type": "Polygon",
            "coordinates": [
                [[77.59, 12.97], [77.60, 12.97], [77.60, 12.98], [77.59, 12.98], [77.59, 12.97]]
            ],
        },
        properties={"parcel_id": "P-101", "land_use": "Residential"},
        status=FeatureStatus.SUCCESS.value,
        warning_message=None,
    )
    feat0.measurement = MeasurementRecord(
        id=str(uuid.uuid4()),
        feature_id=feat0.id,
        measurement_type=MeasurementType.AREA.value,
        measurement_value=1210543.75,
        unit="square_meters",
        calculation_crs="EPSG:32643",
    )
    features.append(feat0)

    # 2. Feature 1: LineString (Length)
    feat1 = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=1,
        geometry_type="LineString",
        geometry_geojson={
            "type": "LineString",
            "coordinates": [[77.59, 12.97], [77.60, 12.98]],
        },
        properties={"road_id": "R-502", "speed_limit": 40},
        status=FeatureStatus.SUCCESS.value,
        warning_message=None,
    )
    feat1.measurement = MeasurementRecord(
        id=str(uuid.uuid4()),
        feature_id=feat1.id,
        measurement_type=MeasurementType.LENGTH.value,
        measurement_value=1534.20,
        unit="meters",
        calculation_crs="EPSG:32643",
    )
    features.append(feat1)

    # 3. Feature 2: Point (Skipped, No Measurement)
    feat2 = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=2,
        geometry_type="Point",
        geometry_geojson={
            "type": "Point",
            "coordinates": [77.595, 12.975],
        },
        properties={"station_id": "ST-09"},
        status=FeatureStatus.SKIPPED_NOT_APPLICABLE.value,
        warning_message="Point features do not possess metric area or length",
    )
    # Explicitly NO measurement record attached
    features.append(feat2)

    # 4. Feature 3: MultiPolygon (Area)
    feat3 = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=3,
        geometry_type="MultiPolygon",
        geometry_geojson={
            "type": "MultiPolygon",
            "coordinates": [
                [[[77.50, 12.90], [77.51, 12.90], [77.51, 12.91], [77.50, 12.91], [77.50, 12.90]]],
                [[[77.52, 12.90], [77.53, 12.90], [77.53, 12.91], [77.52, 12.91], [77.52, 12.90]]],
            ],
        },
        properties={"zone": "North-East Complex"},
        status=FeatureStatus.SUCCESS.value,
        warning_message=None,
    )
    feat3.measurement = MeasurementRecord(
        id=str(uuid.uuid4()),
        feature_id=feat3.id,
        measurement_type=MeasurementType.AREA.value,
        measurement_value=2421087.50,
        unit="square_meters",
        calculation_crs="EPSG:32643",
    )
    features.append(feat3)

    # 5. Feature 4: MultiLineString (Length)
    feat4 = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=4,
        geometry_type="MultiLineString",
        geometry_geojson={
            "type": "MultiLineString",
            "coordinates": [
                [[77.50, 12.90], [77.51, 12.90]],
                [[77.52, 12.90], [77.53, 12.90]],
            ],
        },
        properties={"corridor": "Dual-Carrier Track"},
        status=FeatureStatus.SUCCESS.value,
        warning_message=None,
    )
    feat4.measurement = MeasurementRecord(
        id=str(uuid.uuid4()),
        feature_id=feat4.id,
        measurement_type=MeasurementType.LENGTH.value,
        measurement_value=2190.80,
        unit="meters",
        calculation_crs="EPSG:32643",
    )
    features.append(feat4)

    for feat in features:
        test_db_session.add(feat)

    test_db_session.commit()
    test_db_session.refresh(file_record)
    return file_record, features


@pytest.mark.asyncio
async def test_get_measurements_basic_success(
    async_client: AsyncClient,
    populated_dataset_with_features: tuple[FileRecord, list[FeatureRecord]],
):
    """GET /api/files/{id}/measurements/ returns full feature list with structured GeoJSON and measurements."""
    file_record, _ = populated_dataset_with_features
    file_id = file_record.id

    response = await async_client.get(f"/api/files/{file_id}/measurements/")
    assert response.status_code == 200
    data = response.json()

    assert data["file_id"] == file_id
    assert data["limit"] == 100
    assert data["offset"] == 0
    assert data["total"] == 5
    assert len(data["items"]) == 5

    # Check Feature 0: Polygon
    f0 = data["items"][0]
    assert f0["feature_index"] == 0
    assert f0["geometry_type"] == "Polygon"
    assert f0["geometry"]["type"] == "Polygon"
    assert isinstance(f0["geometry"]["coordinates"], list)
    assert f0["properties"]["parcel_id"] == "P-101"
    assert f0["status"] == "SUCCESS"
    assert f0["warning_message"] is None
    assert f0["measurement"] == {
        "type": "area",
        "value": 1210543.75,
        "unit": "square_meters",
        "calculation_crs": "EPSG:32643",
    }

    # Check Feature 1: LineString
    f1 = data["items"][1]
    assert f1["feature_index"] == 1
    assert f1["geometry_type"] == "LineString"
    assert f1["properties"]["road_id"] == "R-502"
    assert f1["status"] == "SUCCESS"
    assert f1["measurement"] == {
        "type": "length",
        "value": 1534.20,
        "unit": "meters",
        "calculation_crs": "EPSG:32643",
    }

    # Check Feature 2: Point (Skipped, No Measurement)
    f2 = data["items"][2]
    assert f2["feature_index"] == 2
    assert f2["geometry_type"] == "Point"
    assert f2["geometry"]["type"] == "Point"
    assert f2["status"] == "SKIPPED_NOT_APPLICABLE"
    assert "Point features do not possess" in f2["warning_message"]
    assert f2["measurement"] is None

    # Check Feature 3: MultiPolygon
    f3 = data["items"][3]
    assert f3["feature_index"] == 3
    assert f3["geometry_type"] == "MultiPolygon"
    assert f3["measurement"]["type"] == "area"
    assert f3["measurement"]["value"] == 2421087.50

    # Check Feature 4: MultiLineString
    f4 = data["items"][4]
    assert f4["feature_index"] == 4
    assert f4["geometry_type"] == "MultiLineString"
    assert f4["measurement"]["type"] == "length"
    assert f4["measurement"]["value"] == 2190.80


@pytest.mark.asyncio
async def test_get_measurements_pagination_pages(
    async_client: AsyncClient,
    populated_dataset_with_features: tuple[FileRecord, list[FeatureRecord]],
):
    """Verify deterministic pagination across multiple pages with limit and offset."""
    file_record, _ = populated_dataset_with_features
    file_id = file_record.id

    # Page 1: limit=2, offset=0
    res1 = await async_client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=0")
    assert res1.status_code == 200
    page1 = res1.json()
    assert page1["limit"] == 2
    assert page1["offset"] == 0
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert page1["items"][0]["feature_index"] == 0
    assert page1["items"][1]["feature_index"] == 1

    # Page 2: limit=2, offset=2
    res2 = await async_client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=2")
    assert res2.status_code == 200
    page2 = res2.json()
    assert page2["limit"] == 2
    assert page2["offset"] == 2
    assert page2["total"] == 5
    assert len(page2["items"]) == 2
    assert page2["items"][0]["feature_index"] == 2
    assert page2["items"][1]["feature_index"] == 3

    # Page 3: limit=2, offset=4
    res3 = await async_client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=4")
    assert res3.status_code == 200
    page3 = res3.json()
    assert page3["limit"] == 2
    assert page3["offset"] == 4
    assert page3["total"] == 5
    assert len(page3["items"]) == 1
    assert page3["items"][0]["feature_index"] == 4

    # Page 4: offset beyond total features -> empty items
    res4 = await async_client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=10")
    assert res4.status_code == 200
    page4 = res4.json()
    assert page4["limit"] == 2
    assert page4["offset"] == 10
    assert page4["total"] == 5
    assert len(page4["items"]) == 0


@pytest.mark.asyncio
async def test_get_measurements_pagination_boundaries_and_validation(
    async_client: AsyncClient,
    populated_dataset_with_features: tuple[FileRecord, list[FeatureRecord]],
):
    """Verify pagination boundary limits (1 <= limit <= 1000, offset >= 0)."""
    file_record, _ = populated_dataset_with_features
    file_id = file_record.id

    # 1. Max limit = 1000 is allowed
    res_max = await async_client.get(f"/api/files/{file_id}/measurements/?limit=1000&offset=0")
    assert res_max.status_code == 200
    assert res_max.json()["limit"] == 1000

    # 2. Limit exceeding max (1001) -> 400 Bad Request
    res_over = await async_client.get(f"/api/files/{file_id}/measurements/?limit=1001&offset=0")
    assert res_over.status_code == 400
    err_over = res_over.json()
    assert err_over["status"] == 400
    assert err_over["title"] == "Bad Request"

    # 3. Limit zero (0) -> 400 Bad Request
    res_zero = await async_client.get(f"/api/files/{file_id}/measurements/?limit=0&offset=0")
    assert res_zero.status_code == 400

    # 4. Limit negative (-10) -> 400 Bad Request
    res_neg_limit = await async_client.get(f"/api/files/{file_id}/measurements/?limit=-10&offset=0")
    assert res_neg_limit.status_code == 400

    # 5. Offset negative (-1) -> 400 Bad Request
    res_neg_offset = await async_client.get(
        f"/api/files/{file_id}/measurements/?limit=10&offset=-1"
    )
    assert res_neg_offset.status_code == 400


@pytest.mark.asyncio
async def test_get_measurements_nonexistent_file_404(async_client: AsyncClient):
    """GET /api/files/{id}/measurements/ for non-existent file UUID returns RFC 7807 404."""
    random_uuid = str(uuid.uuid4())
    response = await async_client.get(f"/api/files/{random_uuid}/measurements/")

    assert response.status_code == 404
    data = response.json()
    assert data["status"] == 404
    assert data["title"] == "Not Found"
    assert random_uuid in data["detail"]
    assert "errors.geomeasure.internal" in data["type"]


@pytest.mark.asyncio
async def test_get_measurements_malformed_uuid_400(async_client: AsyncClient):
    """GET /api/files/{id}/measurements/ with invalid UUID syntax returns 400."""
    response = await async_client.get("/api/files/invalid-uuid-syntax/measurements/")

    assert response.status_code == 400
    data = response.json()
    assert data["status"] == 400
    assert data["title"] == "Bad Request"


@pytest.mark.asyncio
async def test_get_measurements_empty_dataset_returns_empty_items(
    async_client: AsyncClient, test_db_session: Session
):
    """A file record with zero persisted features returns total=0 and items=[]."""
    file_id = str(uuid.uuid4())
    file_record = FileRecord(
        id=file_id,
        filename="empty_dataset.kml",
        file_type=FileType.KML.value,
        file_size_bytes=512,
        status=FileStatus.COMPLETED.value,
        feature_count=0,
        source_crs=None,
        calculation_crs=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_db_session.add(file_record)
    test_db_session.commit()

    response = await async_client.get(f"/api/files/{file_id}/measurements/")
    assert response.status_code == 200
    data = response.json()
    assert data["file_id"] == file_id
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_get_measurements_unsupported_and_failed_features(
    async_client: AsyncClient, test_db_session: Session
):
    """Features with UNSUPPORTED, INVALID, or FAILED statuses are exposed cleanly with warnings."""
    file_id = str(uuid.uuid4())
    file_record = FileRecord(
        id=file_id,
        filename="anomalous.zip",
        file_type=FileType.SHAPEFILE_ZIP.value,
        file_size_bytes=8192,
        status=FileStatus.COMPLETED_WITH_WARNINGS.value,
        feature_count=2,
        source_crs="EPSG:4326",
        calculation_crs="EPSG:32643",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_db_session.add(file_record)

    feat_unsupported = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=0,
        geometry_type="GeometryCollection",
        geometry_geojson={"type": "GeometryCollection", "geometries": []},
        properties={"note": "mixed types"},
        status=FeatureStatus.UNSUPPORTED.value,
        warning_message="Heterogeneous GeometryCollection measurement is unsupported",
    )
    feat_invalid = FeatureRecord(
        id=str(uuid.uuid4()),
        file_id=file_id,
        feature_index=1,
        geometry_type="Polygon",
        geometry_geojson={"type": "Polygon", "coordinates": []},
        properties={"note": "self intersecting"},
        status=FeatureStatus.INVALID.value,
        warning_message="Invalid geometry topology could not be repaired",
    )
    test_db_session.add(feat_unsupported)
    test_db_session.add(feat_invalid)
    test_db_session.commit()

    response = await async_client.get(f"/api/files/{file_id}/measurements/")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    item0 = data["items"][0]
    assert item0["status"] == "UNSUPPORTED"
    assert "GeometryCollection measurement is unsupported" in item0["warning_message"]
    assert item0["measurement"] is None

    item1 = data["items"][1]
    assert item1["status"] == "INVALID"
    assert "Invalid geometry topology" in item1["warning_message"]
    assert item1["measurement"] is None


@pytest.mark.asyncio
async def test_get_measurements_is_strictly_read_only(
    async_client: AsyncClient,
    populated_dataset_with_features: tuple[FileRecord, list[FeatureRecord]],
    test_db_session: Session,
):
    """GET /api/files/{id}/measurements/ must not mutate database state or change timestamps."""
    file_record, _ = populated_dataset_with_features
    file_id = file_record.id

    orig_updated_at = file_record.updated_at

    # Make multiple retrieval calls
    res1 = await async_client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=0")
    assert res1.status_code == 200

    res2 = await async_client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=0")
    assert res2.status_code == 200

    assert res1.json() == res2.json()

    # Verify database record was not updated
    db_file = test_db_session.scalar(select(FileRecord).where(FileRecord.id == file_id))
    assert db_file is not None
    assert db_file.updated_at == orig_updated_at


@pytest.mark.asyncio
async def test_upload_and_retrieve_measurements_end_to_end(async_client: AsyncClient):
    """Full workflow: Upload KML file via POST /api/files/ -> Paginate results via GET /api/files/{id}/measurements/."""
    kml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>E2E Survey</name>
    <Placemark>
      <name>Parcel Alpha</name>
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
      <name>Road Beta</name>
      <LineString>
        <coordinates>
          78.4800,17.3800,0
          78.4900,17.3800,0
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Marker Gamma</name>
      <Point>
        <coordinates>78.4850,17.3850,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""

    # 1. Upload
    upload_res = await async_client.post(
        "/api/files/",
        files={"file": ("e2e_survey.kml", kml_content, "application/vnd.google-earth.kml+xml")},
    )
    assert upload_res.status_code == 201
    file_id = upload_res.json()["id"]

    # 2. Retrieve Measurements
    meas_res = await async_client.get(f"/api/files/{file_id}/measurements/?limit=10&offset=0")
    assert meas_res.status_code == 200
    data = meas_res.json()

    assert data["file_id"] == file_id
    assert data["total"] == 3
    assert len(data["items"]) == 3

    # Feature 0: Polygon -> area measurement in m2
    poly_item = data["items"][0]
    assert poly_item["geometry_type"] == "Polygon"
    assert poly_item["status"] == "SUCCESS"
    assert poly_item["measurement"]["type"] == "area"
    assert poly_item["measurement"]["unit"] == "square_meters"
    assert poly_item["measurement"]["value"] > 0
    assert poly_item["measurement"]["calculation_crs"] == "EPSG:32644"

    # Feature 1: LineString -> length measurement in meters
    line_item = data["items"][1]
    assert line_item["geometry_type"] == "LineString"
    assert line_item["status"] == "SUCCESS"
    assert line_item["measurement"]["type"] == "length"
    assert line_item["measurement"]["unit"] == "meters"
    assert line_item["measurement"]["value"] > 0
    assert line_item["measurement"]["calculation_crs"] == "EPSG:32644"

    # Feature 2: Point -> skipped, no measurement
    point_item = data["items"][2]
    assert point_item["geometry_type"] == "Point"
    assert point_item["status"] == "SKIPPED_NOT_APPLICABLE"
    assert point_item["measurement"] is None
