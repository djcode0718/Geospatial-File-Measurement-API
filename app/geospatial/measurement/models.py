"""Domain models for geospatial measurement results, units, and dataset summaries."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.db.models import FeatureStatus, MeasurementType


class MeasurementUnit(StrEnum):
    """Standard metric measurement units."""

    SQUARE_METERS = "square_meters"
    METERS = "meters"


@dataclass(frozen=True)
class MeasurementResult:
    """Normalized domain result of a metric measurement calculation for a single feature.

    Attributes:
        feature_index: 0-based sequence index of the feature in dataset.
        geometry_type: Type of geometry ('Polygon', 'LineString', 'Point', etc.).
        measurement_type: Geometric measurement category (area, length, or None).
        value: Numeric calculated value in metric units (float) or None.
        unit: Measurement unit ('square_meters', 'meters', or None).
        calculation_crs: Projected CRS used for metric calculation (e.g. 'EPSG:32643').
        crs_strategy: Projection strategy classification (e.g. 'local_utm').
        status: Processing/measurement status (SUCCESS, SKIPPED_NOT_APPLICABLE, etc.).
        warning: Optional explanatory message or diagnostic warning.
    """

    feature_index: int
    geometry_type: str
    measurement_type: MeasurementType | None
    value: float | None
    unit: MeasurementUnit | str | None
    calculation_crs: str | None
    crs_strategy: str | None
    status: FeatureStatus
    warning: str | None = None


@dataclass
class MeasurementSummary:
    """Aggregated summary of measurements across an entire dataset.

    Attributes:
        total_features: Total count of features processed.
        measured_features: Count of features with valid metric measurements.
        skipped_features: Count of non-applicable features (e.g., Points).
        unsupported_features: Count of unsupported geometry types.
        invalid_features: Count of topologically invalid or empty geometries.
        failed_features: Count of features that encountered processing errors.
        total_area_m2: Cumulative area of all measured polygonal features in m².
        total_length_m: Cumulative length of all measured linear features in m.
        geometry_counts: Breakdown of feature count by geometry type.
        calculation_crs: Calculation CRS applied to the dataset or None.
        warnings: Dataset-level or aggregated warnings.
    """

    total_features: int = 0
    measured_features: int = 0
    skipped_features: int = 0
    unsupported_features: int = 0
    invalid_features: int = 0
    failed_features: int = 0
    total_area_m2: float = 0.0
    total_length_m: float = 0.0
    geometry_counts: dict[str, int] = field(default_factory=dict)
    calculation_crs: str | None = None
    warnings: list[str] = field(default_factory=list)
