"""Domain data structures for normalized geospatial features and datasets."""

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class ParsedFeature:
    """Normalized domain representation of a parsed geospatial vector feature.

    Attributes:
        feature_index: 0-based deterministic sequence index of feature in dataset.
        geometry: Shapely geometry object, or None if feature has null/empty geometry.
        geometry_type: Geometry type name ('Polygon', 'LineString', 'Point', etc.).
        properties: Attribute key-value dictionary.
        source_crs: Detected source Coordinate Reference System (e.g. 'EPSG:4326') or None.
        feature_id: Optional native identifier from source dataset.
        is_valid_geometry: Whether geometry topology is valid according to Shapely.
        warning_message: Diagnostic warning if geometry required repair or had anomalies.
    """

    feature_index: int
    geometry_type: str
    geometry: BaseGeometry | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    source_crs: str | None = None
    feature_id: str | int | None = None
    is_valid_geometry: bool = True
    warning_message: str | None = None


@dataclass
class DatasetMetadata:
    """Summary metadata for an ingested geospatial dataset.

    Attributes:
        total_features: Total count of parsed features.
        source_crs: Original detected Coordinate Reference System or None.
        geometry_counts: Breakdown of feature count by geometry type.
        warnings: List of dataset-level or feature-level warnings.
    """

    total_features: int = 0
    source_crs: str | None = None
    geometry_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
