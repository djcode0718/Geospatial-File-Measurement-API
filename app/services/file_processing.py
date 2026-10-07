"""Application service layer coordinating file upload, staging, parsing, measurement, and persistence."""

import logging
import time
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import UploadFile
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppError,
    UnsupportedFileTypeError,
)
from app.db.models import (
    FeatureRecord,
    FileRecord,
    FileStatus,
    FileType,
    MeasurementRecord,
)
from app.geospatial.measurement.engine import MeasurementEngine
from app.geospatial.readers.kml import KMLReader
from app.geospatial.readers.shapefile import ShapefileReader
from app.storage.sanitizer import sanitize_filename, validate_file_extension
from app.storage.staging import StagingArea
from app.storage.xml_validator import validate_kml_xml_safety
from app.storage.zip_handler import validate_shapefile_components

logger = logging.getLogger(__name__)


def sanitize_json_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Ensure dictionary values are strictly JSON serializable."""
    sanitized: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            sanitized[k] = v
        elif isinstance(v, (datetime, date)):
            sanitized[k] = v.isoformat()
        elif isinstance(v, (list, tuple)):
            sanitized[k] = [
                item.isoformat() if isinstance(item, (datetime, date)) else item for item in v
            ]
        elif isinstance(v, dict):
            sanitized[k] = sanitize_json_dict(v)
        else:
            sanitized[k] = str(v)
    return sanitized


def geometry_to_geojson(geom: BaseGeometry | None) -> dict[str, Any] | None:
    """Convert a Shapely geometry to a GeoJSON-compatible dictionary."""
    if geom is None or geom.is_empty:
        return None
    try:
        return dict(mapping(geom))
    except Exception as err:
        logger.warning("Failed to convert geometry to GeoJSON: %s", err)
        return None


class FileProcessingService:
    """Coordinates end-to-end ingestion, measurement, and persistence workflows."""

    def __init__(self, measurement_engine: MeasurementEngine | None = None) -> None:
        self.measurement_engine = measurement_engine or MeasurementEngine()

    def process_file_upload(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> FileRecord:
        """Handle synchronous upload ingestion, validation, measurement, and persistence.

        Args:
            upload_file: Uploaded multipart file from FastAPI request.
            db: Active SQLAlchemy database session.

        Returns:
            Fully persisted FileRecord with associated features and measurements.

        Raises:
            UnsupportedFileTypeError: If file extension is unsupported.
            FileValidationError: If upload is malformed or violates security policies.
            AppError: For domain and infrastructure errors.
        """
        start_time = time.perf_counter()
        raw_filename = upload_file.filename or ""

        # 1. Filename & Extension Validation
        safe_filename = sanitize_filename(raw_filename)
        extension = validate_file_extension(safe_filename)

        if extension == ".zip":
            file_type = FileType.SHAPEFILE_ZIP
        elif extension == ".kml":
            file_type = FileType.KML
        else:
            raise UnsupportedFileTypeError(
                f"Unsupported file format '{extension}'. Allowed: .zip (Shapefile), .kml",
                details={"filename": safe_filename, "extension": extension},
            )

        file_id = str(uuid.uuid4())
        logger.info(
            "Starting ingestion for file_id=%s filename=%s file_type=%s",
            file_id,
            safe_filename,
            file_type.value,
        )

        # 2. Initialize FileRecord in database with PROCESSING status
        file_record = FileRecord(
            id=file_id,
            filename=safe_filename,
            file_type=file_type.value,
            status=FileStatus.PROCESSING.value,
        )
        db.add(file_record)
        db.commit()
        db.refresh(file_record)

        # 3. Secure Staging and Pipeline Execution
        try:
            with StagingArea(operation_id=file_id) as staging:
                # Stream uploaded content into secure staging area
                saved_path, total_bytes = staging.save_upload_stream(
                    file_obj=upload_file.file,
                    filename=safe_filename,
                )
                file_record.file_size_bytes = total_bytes

                # Format-specific validation & reader selection
                if file_type == FileType.SHAPEFILE_ZIP:
                    extracted_files = staging.extract_archive(saved_path)
                    target_dataset_path = validate_shapefile_components(extracted_files)
                    reader = ShapefileReader()
                else:
                    # KML validation & read
                    with open(saved_path, "rb") as f:
                        kml_bytes = f.read()
                    validate_kml_xml_safety(kml_bytes)
                    target_dataset_path = saved_path
                    reader = KMLReader()

                # Read normalized features and metadata
                features, metadata = reader.read_dataset(target_dataset_path)

                # If a Shapefile is missing .prj, reject dataset explicitly
                if file_type == FileType.SHAPEFILE_ZIP and not metadata.source_crs:
                    from app.core.exceptions import MissingCRSError

                    raise MissingCRSError(
                        "Source CRS is missing or unknown. Shapefile archive must include a valid .prj file.",
                        details={"file_id": file_id, "filename": safe_filename},
                    )

                # Compute metric measurements across features
                results, summary = self.measurement_engine.measure_dataset(features)

                # 4. Atomic Database Persistence Transaction
                for feat, res in zip(features, results, strict=True):
                    geojson_geom = geometry_to_geojson(feat.geometry)
                    safe_props = sanitize_json_dict(feat.properties)

                    feature_rec = FeatureRecord(
                        id=str(uuid.uuid4()),
                        file_id=file_record.id,
                        feature_index=feat.feature_index,
                        geometry_type=feat.geometry_type,
                        geometry_geojson=geojson_geom,
                        properties=safe_props,
                        status=res.status.value,
                        warning_message=res.warning,
                    )

                    # Only attach MeasurementRecord if feature yielded a real measurement
                    if res.measurement_type is not None and res.value is not None:
                        unit_str = res.unit.value if hasattr(res.unit, "value") else str(res.unit)
                        meas_rec = MeasurementRecord(
                            id=str(uuid.uuid4()),
                            feature_id=feature_rec.id,
                            measurement_type=res.measurement_type.value,
                            measurement_value=res.value,
                            unit=unit_str,
                            calculation_crs=res.calculation_crs,
                        )
                        feature_rec.measurement = meas_rec

                    db.add(feature_rec)

                # Compile summary metrics dict
                summary_metrics = {
                    "total_features": summary.total_features,
                    "measured_features": summary.measured_features,
                    "skipped_features": summary.skipped_features,
                    "invalid_features": summary.invalid_features,
                    "unsupported_features": summary.unsupported_features,
                    "failed_features": summary.failed_features,
                    "polygon_count": summary.geometry_counts.get("Polygon", 0)
                    + summary.geometry_counts.get("MultiPolygon", 0),
                    "linestring_count": summary.geometry_counts.get("LineString", 0)
                    + summary.geometry_counts.get("MultiLineString", 0),
                    "point_count": summary.geometry_counts.get("Point", 0)
                    + summary.geometry_counts.get("MultiPoint", 0),
                    "unsupported_count": summary.unsupported_features,
                    "total_area_m2": round(summary.total_area_m2, 4),
                    "total_length_m": round(summary.total_length_m, 4),
                }

                # Update FileRecord summary and status
                file_record.feature_count = summary.total_features
                file_record.source_crs = metadata.source_crs
                file_record.calculation_crs = summary.calculation_crs
                file_record.summary_metrics = summary_metrics

                has_anomalies = (
                    summary.invalid_features > 0
                    or summary.unsupported_features > 0
                    or summary.failed_features > 0
                    or bool(metadata.warnings)
                )
                if has_anomalies:
                    file_record.status = FileStatus.COMPLETED_WITH_WARNINGS.value
                else:
                    file_record.status = FileStatus.COMPLETED.value

                db.commit()
                db.refresh(file_record)

                duration_ms = (time.perf_counter() - start_time) * 1000
                logger.info(
                    "Successfully processed file_id=%s (%d features, status=%s, duration=%.2fms)",
                    file_id,
                    summary.total_features,
                    file_record.status,
                    duration_ms,
                )
                return file_record

        except Exception as err:
            logger.warning("Ingestion failed for file_id=%s: %s", file_id, err)
            db.rollback()

            # Mark FileRecord as FAILED in a clean transaction
            safe_error_msg = err.message if isinstance(err, AppError) else str(err)
            file_record.status = FileStatus.FAILED.value
            file_record.error_message = safe_error_msg
            db.add(file_record)
            db.commit()
            db.refresh(file_record)

            raise
