"""Pydantic request and response schemas for the Geospatial API."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FileSummarySchema(BaseModel):
    """Breakdown of feature counts and metric totals within a dataset."""

    model_config = ConfigDict(extra="ignore")

    total_features: int = 0
    measured_features: int = 0
    skipped_features: int = 0
    invalid_features: int = 0
    unsupported_features: int = 0
    polygon_count: int = 0
    linestring_count: int = 0
    point_count: int = 0
    unsupported_count: int = 0
    total_area_m2: float = 0.0
    total_length_m: float = 0.0


class FileUploadResponse(BaseModel):
    """Response schema returned after file upload and ingestion."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Unique UUID identifier for the uploaded file dataset")
    filename: str = Field(description="Original sanitized filename")
    file_type: str = Field(description="Detected format (SHAPEFILE_ZIP or KML)")
    feature_count: int = Field(description="Total number of features extracted from the file")
    source_crs: str | None = Field(
        default=None, description="Original source Coordinate Reference System"
    )
    calculation_crs: str | None = Field(
        default=None, description="Target projected CRS used for geometric measurements"
    )
    status: str = Field(
        description="Processing status (COMPLETED, COMPLETED_WITH_WARNINGS, FAILED)"
    )
    created_at: datetime = Field(description="Timestamp of file creation")
    summary: dict[str, Any] | None = Field(
        default=None, description="Detailed summary metrics and geometry counts"
    )
    error_message: str | None = Field(
        default=None, description="Diagnostic error details if processing failed"
    )


class FileMetadataResponse(BaseModel):
    """Response schema returned when retrieving persisted file metadata and status."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Unique UUID identifier for the file dataset")
    filename: str = Field(description="Original sanitized filename")
    file_type: str = Field(description="File format type (SHAPEFILE_ZIP or KML)")
    file_size_bytes: int = Field(description="Size of the uploaded file in bytes")
    status: str = Field(description="Processing lifecycle status")
    feature_count: int = Field(description="Total number of extracted features")
    source_crs: str | None = Field(
        default=None, description="Original source Coordinate Reference System"
    )
    calculation_crs: str | None = Field(
        default=None, description="Projected Coordinate Reference System used for measurements"
    )
    summary: dict[str, Any] | None = Field(
        default=None, description="Detailed summary metrics and geometry counts"
    )
    error_message: str | None = Field(
        default=None, description="Diagnostic error details if status is FAILED"
    )
    created_at: datetime = Field(description="Timestamp when the file was created")
    updated_at: datetime = Field(description="Timestamp when the file was last updated")


class ErrorDetailSchema(BaseModel):
    """Standard RFC 7807 Problem Details error schema."""

    type: str = Field(
        default="about:blank", description="URI reference identifying the problem type"
    )
    title: str = Field(description="Short human-readable summary of problem")
    status: int = Field(description="HTTP status code")
    detail: str = Field(description="Human-readable explanation specific to this occurrence")
    instance: str = Field(description="URI reference of the request endpoint")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when error was generated",
    )
