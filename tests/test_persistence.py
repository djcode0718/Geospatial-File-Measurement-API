"""Tests for database models, persistence, WAL configuration, and relationships."""

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import (
    FeatureRecord,
    FeatureStatus,
    FileRecord,
    FileStatus,
    FileType,
    MeasurementRecord,
    MeasurementType,
)
from app.db.session import create_db_engine


@pytest.fixture
def test_db_session() -> Generator[Session, None, None]:
    """Provide an isolated, file-backed SQLite session for persistence testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        db_path = tmp_file.name

    db_url = f"sqlite:///{db_path}"
    engine = create_db_engine(db_url)
    Base.metadata.create_all(bind=engine)

    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = session_factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        # Clean up temporary database files including WAL and SHM
        for suffix in ["", "-wal", "-shm", "-journal"]:
            target = Path(f"{db_path}{suffix}")
            if target.exists():
                os.remove(target)


def test_sqlite_wal_and_foreign_keys(test_db_session: Session) -> None:
    """Verify SQLite connection enables WAL journal mode and enforces foreign keys."""
    # Check foreign keys pragma
    fk_result = test_db_session.execute(text("PRAGMA foreign_keys")).scalar()
    assert fk_result == 1

    # Check journal mode pragma (WAL on file-backed database)
    journal_mode = test_db_session.execute(text("PRAGMA journal_mode")).scalar()
    assert journal_mode.upper() == "WAL"


def test_create_file_record(test_db_session: Session) -> None:
    """Verify creation and retrieval of a FileRecord entity with defaults."""
    file_record = FileRecord(
        filename="bangalore_survey.zip",
        file_type=FileType.SHAPEFILE_ZIP.value,
        file_size_bytes=1048576,
        source_crs="EPSG:4326",
    )
    test_db_session.add(file_record)
    test_db_session.commit()
    test_db_session.refresh(file_record)

    assert file_record.id is not None
    assert len(file_record.id) == 36  # UUID string
    assert file_record.status == FileStatus.UPLOADED.value
    assert file_record.feature_count == 0
    assert file_record.created_at is not None
    assert file_record.updated_at is not None


def test_file_features_measurements_relationships(test_db_session: Session) -> None:
    """Verify parent-child cascading relationships across File, Feature, and Measurement."""
    # Create File
    file_record = FileRecord(
        filename="road_network.kml",
        file_type=FileType.KML.value,
        file_size_bytes=52428,
        status=FileStatus.PROCESSING.value,
        source_crs="EPSG:4326",
        calculation_crs="EPSG:32643",
    )
    test_db_session.add(file_record)
    test_db_session.flush()

    # Create Feature 0 (Polygon with Area Measurement)
    poly_feature = FeatureRecord(
        file_id=file_record.id,
        feature_index=0,
        geometry_type="Polygon",
        geometry_geojson={
            "type": "Polygon",
            "coordinates": [[[77.5, 12.9], [77.6, 12.9], [77.6, 13.0], [77.5, 13.0], [77.5, 12.9]]],
        },
        properties={"zone": "Industrial", "parcel_no": 42},
        status=FeatureStatus.SUCCESS.value,
    )
    test_db_session.add(poly_feature)
    test_db_session.flush()

    poly_measurement = MeasurementRecord(
        feature_id=poly_feature.id,
        measurement_type=MeasurementType.AREA.value,
        measurement_value=1254300.75,
        unit="square_meters",
        calculation_crs="EPSG:32643",
    )
    test_db_session.add(poly_measurement)

    # Create Feature 1 (Point with null measurement)
    point_feature = FeatureRecord(
        file_id=file_record.id,
        feature_index=1,
        geometry_type="Point",
        geometry_geojson={"type": "Point", "coordinates": [77.5, 12.9]},
        properties={"name": "Observation Tower"},
        status=FeatureStatus.SKIPPED_NOT_APPLICABLE.value,
    )
    test_db_session.add(point_feature)

    test_db_session.commit()

    # Query back file with relationships
    queried_file = test_db_session.query(FileRecord).filter_by(id=file_record.id).one()
    assert len(queried_file.features) == 2
    assert queried_file.features[0].feature_index == 0
    assert queried_file.features[0].geometry_type == "Polygon"
    assert queried_file.features[0].measurement is not None
    assert queried_file.features[0].measurement.measurement_value == 1254300.75
    assert queried_file.features[0].measurement.unit == "square_meters"

    assert queried_file.features[1].feature_index == 1
    assert queried_file.features[1].geometry_type == "Point"
    assert queried_file.features[1].measurement is None


def test_cascade_delete(test_db_session: Session) -> None:
    """Verify deleting a FileRecord cascades and removes all associated features and measurements."""
    file_record = FileRecord(
        filename="temp.kml",
        file_type=FileType.KML.value,
        file_size_bytes=100,
    )
    test_db_session.add(file_record)
    test_db_session.flush()

    feature = FeatureRecord(
        file_id=file_record.id,
        feature_index=0,
        geometry_type="LineString",
    )
    test_db_session.add(feature)
    test_db_session.flush()

    measurement = MeasurementRecord(
        feature_id=feature.id,
        measurement_type=MeasurementType.LENGTH.value,
        measurement_value=500.0,
        unit="meters",
    )
    test_db_session.add(measurement)
    test_db_session.commit()

    feature_id = feature.id
    measurement_id = measurement.id

    # Delete parent file record
    test_db_session.delete(file_record)
    test_db_session.commit()

    # Assert cascade delete cleaned up child features and measurements
    assert test_db_session.query(FeatureRecord).filter_by(id=feature_id).first() is None
    assert test_db_session.query(MeasurementRecord).filter_by(id=measurement_id).first() is None


def test_transaction_rollback(test_db_session: Session) -> None:
    """Verify transaction rollback discards uncommitted database mutations."""
    file_record = FileRecord(
        filename="uncommitted.kml",
        file_type=FileType.KML.value,
        file_size_bytes=500,
    )
    test_db_session.add(file_record)
    test_db_session.flush()

    file_id = file_record.id
    test_db_session.rollback()

    queried = test_db_session.query(FileRecord).filter_by(id=file_id).first()
    assert queried is None
