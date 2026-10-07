"""SQLAlchemy 2.x relational ORM data models."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class FileType(StrEnum):
    """Supported uploaded geospatial file types."""

    SHAPEFILE_ZIP = "SHAPEFILE_ZIP"
    KML = "KML"


class FileStatus(StrEnum):
    """Lifecycle processing status of an uploaded file."""

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"


class FeatureStatus(StrEnum):
    """Processing and measurement status of an individual feature."""

    SUCCESS = "SUCCESS"
    SKIPPED_NOT_APPLICABLE = "SKIPPED_NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"
    FAILED = "FAILED"


class MeasurementType(StrEnum):
    """Types of geometric measurements."""

    AREA = "area"
    LENGTH = "length"


class FileRecord(Base):
    """Database model representing an uploaded geospatial file dataset."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        doc="Unique UUID primary key",
    )
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Original sanitized filename",
    )
    file_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="File format type (SHAPEFILE_ZIP or KML)",
    )
    file_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Size of the uploaded file in bytes",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=FileStatus.UPLOADED.value,
        index=True,
        doc="Current lifecycle processing status",
    )
    feature_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Total number of features extracted from dataset",
    )
    source_crs: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Detected source Coordinate Reference System (e.g., EPSG:4326)",
    )
    calculation_crs: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Projected Coordinate Reference System used for measurement",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Fatal error diagnostic message if status is FAILED",
    )
    summary_metrics: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Summary breakdown of geometries (polygon_count, linestring_count, etc.)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        doc="Timestamp when file record was created",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
        doc="Timestamp when file record was last updated",
    )

    # Relationships
    features: Mapped[list["FeatureRecord"]] = relationship(
        "FeatureRecord",
        back_populates="file",
        cascade="all, delete-orphan",
        order_by="FeatureRecord.feature_index",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<FileRecord id={self.id!r} filename={self.filename!r} status={self.status!r}>"


class FeatureRecord(Base):
    """Database model representing an individual geospatial vector feature."""

    __tablename__ = "features"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        doc="Unique UUID primary key",
    )
    file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Foreign key reference to parent FileRecord",
    )
    feature_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="0-indexed sequence position of feature in dataset",
    )
    geometry_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Geometry type string (Polygon, LineString, Point, etc.)",
    )
    geometry_geojson: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Standard GeoJSON geometry object representation",
    )
    properties: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Key-value attribute dictionary",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=FeatureStatus.SUCCESS.value,
        doc="Feature processing status (SUCCESS, SKIPPED, UNSUPPORTED, INVALID)",
    )
    warning_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Non-fatal warning or repair explanation",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        doc="Timestamp when feature was persisted",
    )

    # Relationships
    file: Mapped["FileRecord"] = relationship(
        "FileRecord",
        back_populates="features",
    )
    measurement: Mapped["MeasurementRecord | None"] = relationship(
        "MeasurementRecord",
        back_populates="feature",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="joined",
    )

    __table_args__ = (Index("ix_features_file_id_feature_index", "file_id", "feature_index"),)

    def __repr__(self) -> str:
        return (
            f"<FeatureRecord id={self.id!r} file_id={self.file_id!r} "
            f"index={self.feature_index} type={self.geometry_type!r} status={self.status!r}>"
        )


class MeasurementRecord(Base):
    """Database model representing a calculated metric measurement for a feature."""

    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        doc="Unique UUID primary key",
    )
    feature_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("features.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        doc="Foreign key reference to parent FeatureRecord",
    )
    measurement_type: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        doc="Type of measurement (area or length, null for points/unsupported)",
    )
    measurement_value: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Calculated numeric value in metric units",
    )
    unit: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        doc="Standard metric unit string (square_meters or meters)",
    )
    calculation_crs: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Projected CRS used during measurement calculation",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        doc="Timestamp when measurement was recorded",
    )

    # Relationships
    feature: Mapped["FeatureRecord"] = relationship(
        "FeatureRecord",
        back_populates="measurement",
    )

    def __repr__(self) -> str:
        return (
            f"<MeasurementRecord id={self.id!r} type={self.measurement_type!r} "
            f"value={self.measurement_value!r} unit={self.unit!r}>"
        )
