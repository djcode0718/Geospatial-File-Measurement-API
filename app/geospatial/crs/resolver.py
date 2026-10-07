"""Dynamic Coordinate Reference System resolution engine."""

import logging

from app.geospatial.crs.models import (
    BoundingBox,
    CRSResolution,
    CRSStrategy,
    MeasurementPurpose,
)
from app.geospatial.crs.validator import normalize_crs_string, validate_crs

logger = logging.getLogger(__name__)

# Standard UTM limits
MAX_LOCAL_UTM_LON_SPAN_DEG = 6.0
UTM_NORTH_MAX_LAT = 84.0
UTM_SOUTH_MIN_LAT = -80.0

# Standard fallback EPSG codes validated by PROJ
EPSG_GLOBAL_EQUAL_AREA = "EPSG:6933"  # WGS 84 / NSIDC EASE-Grid 2.0 Global
EPSG_ARCTIC_POLAR = "EPSG:3413"  # WGS 84 / NSIDC Sea Ice Polar Stereographic North
EPSG_ANTARCTIC_POLAR = "EPSG:3031"  # WGS 84 / Antarctic Polar Stereographic


class CRSResolver:
    """Evaluates source dataset properties and resolves the optimal projected calculation CRS."""

    def resolve(
        self,
        source_crs_input: str | None,
        extent: BoundingBox | None = None,
        purpose: MeasurementPurpose = MeasurementPurpose.GENERAL,
    ) -> CRSResolution:
        """Determine the optimal projected CRS for accurate metric calculations.

        Args:
            source_crs_input: Source CRS identifier (e.g. 'EPSG:4326', WKT string).
            extent: 2D spatial bounding box of the dataset in source coordinates.
            purpose: Geometric measurement intent (area, length, or general).

        Returns:
            CRSResolution record detailing calculation CRS and technical rationale.

        Raises:
            MissingCRSError: If source CRS is None or empty.
            InvalidCRSError: If source CRS cannot be parsed by PyProj.
        """
        source_pyproj = validate_crs(source_crs_input)
        source_crs_norm = normalize_crs_string(source_pyproj)

        warnings: list[str] = []

        # 1. Check if source CRS is already a projected coordinate system
        if source_pyproj.is_projected:
            return CRSResolution(
                source_crs=source_crs_norm,
                calculation_crs=source_crs_norm,
                strategy=CRSStrategy.PRESERVED_SOURCE_PROJECTED,
                purpose=purpose,
                requires_transformation=False,
                reason=(
                    f"Source CRS '{source_crs_norm}' is already a valid projected coordinate "
                    "system in linear units; preserving source projection."
                ),
                warnings=warnings,
            )

        # 2. Geographic CRS handling (angular coordinates in degrees)
        # Handle cases where spatial extent is not available
        if extent is None:
            warnings.append(
                "No feature geometries available to compute extent; using global fallback."
            )
            return CRSResolution(
                source_crs=source_crs_norm,
                calculation_crs=EPSG_GLOBAL_EQUAL_AREA,
                strategy=CRSStrategy.GLOBAL_EQUAL_AREA,
                purpose=purpose,
                requires_transformation=True,
                reason=(
                    f"Geographic source CRS '{source_crs_norm}' without bounding box; "
                    f"defaulted to global equal-area projection ({EPSG_GLOBAL_EQUAL_AREA})."
                ),
                warnings=warnings,
            )

        lon_c, lat_c = extent.centroid()
        span_x = extent.width()

        # 3. Polar Extent Checks
        if lat_c >= UTM_NORTH_MAX_LAT or extent.min_y >= UTM_NORTH_MAX_LAT:
            return CRSResolution(
                source_crs=source_crs_norm,
                calculation_crs=EPSG_ARCTIC_POLAR,
                strategy=CRSStrategy.POLAR_STEREOGRAPHIC,
                purpose=purpose,
                requires_transformation=True,
                reason=(
                    f"Dataset is located in Arctic polar region (centroid latitude {lat_c:.2f}°N >= {UTM_NORTH_MAX_LAT}°N); "
                    f"selected WGS 84 Polar Stereographic North ({EPSG_ARCTIC_POLAR})."
                ),
                warnings=warnings,
            )

        if lat_c <= UTM_SOUTH_MIN_LAT or extent.max_y <= UTM_SOUTH_MIN_LAT:
            return CRSResolution(
                source_crs=source_crs_norm,
                calculation_crs=EPSG_ANTARCTIC_POLAR,
                strategy=CRSStrategy.POLAR_STEREOGRAPHIC,
                purpose=purpose,
                requires_transformation=True,
                reason=(
                    f"Dataset is located in Antarctic polar region (centroid latitude {lat_c:.2f}°S <= {UTM_SOUTH_MIN_LAT}°S); "
                    f"selected Antarctic Polar Stereographic ({EPSG_ANTARCTIC_POLAR})."
                ),
                warnings=warnings,
            )

        # 4. Compute optimal UTM Zone parameters
        # Clamp longitude to valid range [-180, 180]
        clamped_lon = max(-180.0, min(180.0, lon_c))
        utm_zone = int((clamped_lon + 180.0) // 6.0) + 1
        utm_zone = max(1, min(60, utm_zone))
        is_north = lat_c >= 0.0
        hemisphere_char = "N" if is_north else "S"
        utm_epsg_code = (32600 if is_north else 32700) + utm_zone
        utm_epsg_str = f"EPSG:{utm_epsg_code}"

        # 5. Local UTM Zone Selection (Longitude span <= 6°)
        if span_x <= MAX_LOCAL_UTM_LON_SPAN_DEG:
            return CRSResolution(
                source_crs=source_crs_norm,
                calculation_crs=utm_epsg_str,
                strategy=CRSStrategy.LOCAL_UTM,
                purpose=purpose,
                requires_transformation=True,
                reason=(
                    f"Local geographic dataset centered at ({lon_c:.2f}°E, {lat_c:.2f}°N); "
                    f"selected UTM Zone {utm_zone}{hemisphere_char} ({utm_epsg_str})."
                ),
                warnings=warnings,
            )

        # 6. Broad / Multi-Zone Extent Fallback (Longitude span > 6°)
        if purpose == MeasurementPurpose.AREA:
            warnings.append(
                f"Dataset spans {span_x:.1f}° longitude across UTM zone boundaries; "
                f"using global equal-area projection ({EPSG_GLOBAL_EQUAL_AREA}) to preserve area accuracy."
            )
            return CRSResolution(
                source_crs=source_crs_norm,
                calculation_crs=EPSG_GLOBAL_EQUAL_AREA,
                strategy=CRSStrategy.GLOBAL_EQUAL_AREA,
                purpose=purpose,
                requires_transformation=True,
                reason=(
                    f"Broad extent dataset spanning {span_x:.1f}° longitude; "
                    f"selected WGS 84 NSIDC EASE-Grid 2.0 Global Equal-Area ({EPSG_GLOBAL_EQUAL_AREA}) "
                    "to prevent planar distortion."
                ),
                warnings=warnings,
            )

        # Fallback for distance/general measurement on multi-zone extents
        warnings.append(
            f"Dataset spans {span_x:.1f}° longitude across UTM zone boundaries; "
            f"using centroid UTM Zone {utm_zone}{hemisphere_char} ({utm_epsg_str}) with increased edge distortion risk."
        )
        return CRSResolution(
            source_crs=source_crs_norm,
            calculation_crs=utm_epsg_str,
            strategy=CRSStrategy.CROSS_ZONE_FALLBACK,
            purpose=purpose,
            requires_transformation=True,
            reason=(
                f"Multi-zone extent spanning {span_x:.1f}° longitude; "
                f"selected centroid UTM Zone {utm_zone}{hemisphere_char} ({utm_epsg_str})."
            ),
            warnings=warnings,
        )
