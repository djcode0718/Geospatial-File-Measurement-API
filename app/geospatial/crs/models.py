"""Domain models for spatial extents, CRS resolution decisions, and strategies."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from shapely.geometry.base import BaseGeometry


class MeasurementPurpose(StrEnum):
    """Target geometric measurement intent determining optimal projection characteristics."""

    AREA = "area"
    LENGTH = "length"
    GENERAL = "general"


class CRSStrategy(StrEnum):
    """Classification of the selected projection strategy."""

    LOCAL_UTM = "local_utm"
    PRESERVED_SOURCE_PROJECTED = "preserved_source_projected"
    GLOBAL_EQUAL_AREA = "global_equal_area"
    POLAR_STEREOGRAPHIC = "polar_stereographic"
    CROSS_ZONE_FALLBACK = "cross_zone_fallback"


@dataclass(frozen=True)
class BoundingBox:
    """2D spatial bounding box representing geometric extent.

    Coordinates are in source CRS units (longitude/latitude for geographic CRS).
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def centroid(self) -> tuple[float, float]:
        """Calculate the arithmetic center (x_center, y_center) of the bounding box."""
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    def width(self) -> float:
        """Return span along X-axis (longitude span in degrees for geographic CRS)."""
        return abs(self.max_x - self.min_x)

    def height(self) -> float:
        """Return span along Y-axis (latitude span in degrees for geographic CRS)."""
        return abs(self.max_y - self.min_y)

    @classmethod
    def from_geometries(cls, geometries: Iterable[BaseGeometry | None]) -> "BoundingBox | None":
        """Compute cumulative bounding box across an iterable of Shapely geometries."""
        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")
        found = False

        for geom in geometries:
            if geom is not None and not geom.is_empty:
                b = geom.bounds  # (minx, miny, maxx, maxy)
                min_x = min(min_x, b[0])
                min_y = min(min_y, b[1])
                max_x = max(max_x, b[2])
                max_y = max(max_y, b[3])
                found = True

        if not found:
            return None

        return cls(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)


@dataclass(frozen=True)
class CRSResolution:
    """Structured result explaining the chosen calculation CRS and projection rationale.

    Attributes:
        source_crs: Original detected or validated source CRS identifier (e.g. 'EPSG:4326').
        calculation_crs: Target projected coordinate system for measurement (e.g. 'EPSG:32643').
        strategy: Strategy category used to select calculation CRS.
        purpose: Measurement purpose for which the CRS was optimized (area or length).
        requires_transformation: True if coordinates must be reprojected from source to target.
        reason: Human-readable technical justification for the chosen calculation CRS.
        warnings: List of edge-case warnings (e.g., cross-zone distortion, missing .prj).
    """

    source_crs: str
    calculation_crs: str
    strategy: CRSStrategy
    purpose: MeasurementPurpose
    requires_transformation: bool
    reason: str
    warnings: list[str] = field(default_factory=list)
