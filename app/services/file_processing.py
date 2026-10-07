"""Application service layer coordinating file upload, staging, parsing, measurement, and persistence."""

import logging
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    FileRecordNotFoundError,
    MissingCRSError,
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


def sanitize_json_dict(d: dict[str, Any], max_bytes: int | None = None) -> dict[str, Any]:
    """Ensure dictionary values are strictly JSON serializable and within memory bounds."""
    settings = get_settings()
    max_payload = max_bytes or settings.MAX_PROPERTY_PAYLOAD_BYTES
    sanitized: dict[str, Any] = {}
    for k, v in d.items():
        key_str = str(k)[:100]
        if isinstance(v, (int, float, bool)) or v is None:
            sanitized[key_str] = v
        elif isinstance(v, str):
            sanitized[key_str] = v[:max_payload]
        elif isinstance(v, (datetime, date)):
            sanitized[key_str] = v.isoformat()
        elif isinstance(v, (list, tuple)):
            sanitized[key_str] = [
                item.isoformat() if isinstance(item, (datetime, date)) else str(item)[:1000]
                for item in v[:100]
            ]
        elif isinstance(v, dict):
            sanitized[key_str] = sanitize_json_dict(v, max_bytes=max_payload // 2)
        else:
            sanitized[key_str] = str(v)[:max_payload]
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

    def prepare_upload_staging(
        self,
        upload_file: UploadFile,
        db: Session,
    ) -> tuple[FileRecord, StagingArea, str, FileType]:
        """Validate upload metadata, initialize FileRecord (PROCESSING), and stage payload to disk.

        Args:
            upload_file: Uploaded multipart file.
            db: Database session for initial FileRecord persistence.

        Returns:
            Tuple of (FileRecord in PROCESSING status, StagingArea instance, saved file path, FileType).
        """
        raw_filename = upload_file.filename or ""
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
            "Initializing ingestion staging for file_id=%s filename=%s file_type=%s",
            file_id,
            safe_filename,
            file_type.value,
        )

        file_record = FileRecord(
            id=file_id,
            filename=safe_filename,
            file_type=file_type.value,
            status=FileStatus.PROCESSING.value,
        )
        db.add(file_record)
        db.commit()
        db.refresh(file_record)

        staging = StagingArea(operation_id=file_id)
        saved_path, total_bytes = staging.save_upload_stream(
            file_obj=upload_file.file,
            filename=safe_filename,
        )
        file_record.file_size_bytes = total_bytes
        db.commit()
        db.refresh(file_record)

        return file_record, staging, saved_path, file_type

    def process_staged_dataset(
        self,
        file_id: str,
        staged_path: str,
        filename: str,
        file_type: FileType,
        db: Session,
        staging: StagingArea | None = None,
    ) -> FileRecord:
        """Process an already-staged geospatial file dataset and atomically persist results.

        Args:
            file_id: Unique UUID identifier of the target FileRecord.
            staged_path: File system path to the staged file on disk.
            filename: Original sanitized filename.
            file_type: Format enum (SHAPEFILE_ZIP or KML).
            db: Active database session.
            staging: Optional StagingArea instance managing the extraction directory.

        Returns:
            Updated FileRecord with COMPLETED or COMPLETED_WITH_WARNINGS status.

        Raises:
            AppError: On domain validation or extraction failure.
        """
        start_time = time.perf_counter()
        file_record = db.scalar(select(FileRecord).where(FileRecord.id == file_id))
        if file_record is None:
            raise FileRecordNotFoundError(
                f"File with ID '{file_id}' was not found.",
                details={"file_id": file_id},
            )

        logger.info(
            "Processing pipeline initiated: file_id=%s filename=%s file_type=%s",
            file_id,
            filename,
            file_type.value,
        )

        try:
            staging_inst = staging or StagingArea(operation_id=file_id)
            staged_file_path = Path(staged_path)

            # Format-specific validation & reader selection
            if file_type == FileType.SHAPEFILE_ZIP:
                extracted_files = staging_inst.extract_archive(staged_file_path)
                target_dataset_path = validate_shapefile_components(extracted_files)
                reader = ShapefileReader()
            else:
                # KML validation & read
                with open(staged_file_path, "rb") as f:
                    kml_bytes = f.read()
                validate_kml_xml_safety(kml_bytes)
                target_dataset_path = staged_file_path
                reader = KMLReader()

            # Read normalized features and metadata
            features, metadata = reader.read_dataset(target_dataset_path)
            logger.info(
                "Dataset parsed successfully: file_id=%s feature_count=%d detected_crs=%s",
                file_id,
                len(features),
                metadata.source_crs,
            )

            # If a Shapefile is missing .prj, reject dataset explicitly
            if file_type == FileType.SHAPEFILE_ZIP and not metadata.source_crs:
                raise MissingCRSError(
                    "Source CRS is missing or unknown. Shapefile archive must include a valid .prj file.",
                    details={"file_id": file_id, "filename": filename},
                )

            # Compute metric measurements across features
            results, summary = self.measurement_engine.measure_dataset(features)
            logger.info(
                "Geospatial measurements completed: file_id=%s measured=%d skipped=%d invalid=%d unsupported=%d failed=%d calc_crs=%s",
                file_id,
                summary.measured_features,
                summary.skipped_features,
                summary.invalid_features,
                summary.unsupported_features,
                summary.failed_features,
                summary.calculation_crs,
            )

            # Idempotency safety: remove any prior features if re-processing occurred
            db.query(FeatureRecord).filter(FeatureRecord.file_id == file_record.id).delete()

            # Atomic Database Persistence Transaction
            feature_records_to_add: list[FeatureRecord] = []
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

                feature_records_to_add.append(feature_rec)

            if feature_records_to_add:
                db.add_all(feature_records_to_add)

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
                "Processing completed successfully: file_id=%s features=%d status=%s duration_ms=%.2f",
                file_id,
                summary.total_features,
                file_record.status,
                duration_ms,
            )
            return file_record

        except Exception as err:
            duration_ms = (time.perf_counter() - start_time) * 1000
            safe_error_msg = err.message if isinstance(err, AppError) else str(err)
            error_code = getattr(err, "code", "UNHANDLED_ERROR")
            logger.warning(
                "Processing failed: file_id=%s error_code=%s error=%s duration_ms=%.2f",
                file_id,
                error_code,
                safe_error_msg,
                duration_ms,
            )
            db.rollback()

            # Mark FileRecord as FAILED in a clean transaction
            file_record.status = FileStatus.FAILED.value
            file_record.error_message = safe_error_msg
            db.add(file_record)
            db.commit()
            db.refresh(file_record)

            raise

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
        file_record, staging, saved_path, file_type = self.prepare_upload_staging(
            upload_file=upload_file, db=db
        )
        with staging:
            return self.process_staged_dataset(
                file_id=file_record.id,
                staged_path=saved_path,
                filename=file_record.filename,
                file_type=file_type,
                db=db,
                staging=staging,
            )


def get_file_record(file_id: str | uuid.UUID, db: Session) -> FileRecord:
    """Retrieve a persisted FileRecord by primary key UUID.

    Args:
        file_id: UUID string or UUID instance.
        db: Active SQLAlchemy database session.

    Returns:
        FileRecord database model instance.

    Raises:
        FileRecordNotFoundError: If no file record exists for the given ID.
    """
    id_str = str(file_id)
    file_record = db.scalar(select(FileRecord).where(FileRecord.id == id_str))
    if file_record is None:
        raise FileRecordNotFoundError(
            f"File with ID '{id_str}' was not found.",
            details={"file_id": id_str},
        )
    return file_record


def get_file_measurements_page(
    file_id: str | uuid.UUID,
    db: Session,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[FeatureRecord], int]:
    """Retrieve a paginated page of FeatureRecords with eager-loaded MeasurementRecords.

    Args:
        file_id: UUID string or UUID instance of parent FileRecord.
        db: Active SQLAlchemy database session.
        limit: Max items to return (LIMIT).
        offset: Number of items to skip (OFFSET).

    Returns:
        Tuple of (list of FeatureRecords with joined measurement, total feature count).

    Raises:
        FileRecordNotFoundError: If no file record exists for the given ID.
    """
    id_str = str(file_id)

    # 1. Confirm FileRecord exists first (raises 404 if not found)
    get_file_record(file_id=id_str, db=db)

    # 2. Query total feature count at database level
    total = (
        db.scalar(
            select(func.count()).select_from(FeatureRecord).where(FeatureRecord.file_id == id_str)
        )
        or 0
    )

    # 3. Query paginated feature records with joined measurement relationship
    query = (
        select(FeatureRecord)
        .where(FeatureRecord.file_id == id_str)
        .options(joinedload(FeatureRecord.measurement))
        .order_by(FeatureRecord.feature_index.asc())
        .limit(limit)
        .offset(offset)
    )
    features = list(db.scalars(query).unique())

    return features, total
