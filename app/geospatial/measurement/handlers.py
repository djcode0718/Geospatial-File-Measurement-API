"""Geometry-specific measurement handlers implementing metric calculation policies."""

import logging
from abc import ABC, abstractmethod

from shapely.geometry import GeometryCollection

from app.core.exceptions import (
    CRSError,
    GeospatialError,
)
from app.db.models import FeatureStatus, MeasurementType
from app.geospatial.crs.models import BoundingBox, MeasurementPurpose
from app.geospatial.crs.resolver import CRSResolver
from app.geospatial.crs.transformer import GeometryTransformer
from app.geospatial.measurement.models import MeasurementResult, MeasurementUnit
from app.geospatial.models import ParsedFeature

logger = logging.getLogger(__name__)


class BaseMeasurementHandler(ABC):
    """Abstract base class for geometry-specific metric measurement handlers."""

    @abstractmethod
    def can_handle(self, geometry_type: str) -> bool:
        """Check if this handler supports the given geometry type name."""
        ...

    @abstractmethod
    def measure(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
        crs_resolver: CRSResolver | None = None,
    ) -> MeasurementResult:
        """Calculate metric measurement for the given parsed feature."""
        ...


class PolygonMeasurementHandler(BaseMeasurementHandler):
    """Calculates surface area in square meters (m²) for Polygon and MultiPolygon geometries."""

    SUPPORTED_TYPES = {"Polygon", "MultiPolygon"}

    def can_handle(self, geometry_type: str) -> bool:
        return geometry_type in self.SUPPORTED_TYPES

    def measure(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
        crs_resolver: CRSResolver | None = None,
    ) -> MeasurementResult:
        resolver = crs_resolver or CRSResolver()

        # 1. Null geometry check
        if feature.geometry is None:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.AREA,
                value=None,
                unit=MeasurementUnit.SQUARE_METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning="Feature geometry is null or missing.",
            )

        # 2. Empty geometry check
        if feature.geometry.is_empty:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.AREA,
                value=0.0,
                unit=MeasurementUnit.SQUARE_METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning="Empty polygon geometry with 0 area.",
            )

        # 3. Topology validity check
        if not feature.geometry.is_valid:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.AREA,
                value=None,
                unit=MeasurementUnit.SQUARE_METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning=(
                    f"Invalid polygon geometry topology: "
                    f"{feature.warning_message or 'Self-intersection or invalid ring structure'}"
                ),
            )

        # 4. CRS Resolution & Transformation
        try:
            extent = dataset_extent or BoundingBox.from_geometries([feature.geometry])
            crs_res = resolver.resolve(
                source_crs_input=feature.source_crs,
                extent=extent,
                purpose=MeasurementPurpose.AREA,
            )

            transformer = GeometryTransformer(
                source_crs=crs_res.source_crs,
                target_crs=crs_res.calculation_crs,
            )
            transformed_geom = transformer.transform(feature.geometry)

            if transformed_geom is None or transformed_geom.is_empty:
                return MeasurementResult(
                    feature_index=feature.feature_index,
                    geometry_type=feature.geometry_type,
                    measurement_type=MeasurementType.AREA,
                    value=0.0,
                    unit=MeasurementUnit.SQUARE_METERS,
                    calculation_crs=crs_res.calculation_crs,
                    crs_strategy=crs_res.strategy.value,
                    status=FeatureStatus.INVALID,
                    warning="Reprojected geometry is empty.",
                )

            # 5. Metric Area Calculation in m²
            # Shapely .area on projected planar coordinates returns square meters.
            # MultiPolygon area is the sum of constituent polygons.
            # Polygon holes are subtracted automatically by Shapely topology.
            area_m2 = float(transformed_geom.area)

            warning = feature.warning_message or (crs_res.warnings[0] if crs_res.warnings else None)

            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.AREA,
                value=area_m2,
                unit=MeasurementUnit.SQUARE_METERS,
                calculation_crs=crs_res.calculation_crs,
                crs_strategy=crs_res.strategy.value,
                status=FeatureStatus.SUCCESS,
                warning=warning,
            )

        except (CRSError, GeospatialError) as err:
            logger.warning("Measurement failed for feature %d: %s", feature.feature_index, err)
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.AREA,
                value=None,
                unit=MeasurementUnit.SQUARE_METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.FAILED,
                warning=f"Area measurement failed: {err.message if hasattr(err, 'message') else str(err)}",
            )
        except Exception as err:
            logger.exception("Unexpected error during polygon measurement: %s", err)
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.AREA,
                value=None,
                unit=MeasurementUnit.SQUARE_METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.FAILED,
                warning=f"Unexpected measurement error: {err}",
            )


class LineStringMeasurementHandler(BaseMeasurementHandler):
    """Calculates linear length in meters (m) for LineString, MultiLineString, and LinearRing."""

    SUPPORTED_TYPES = {"LineString", "MultiLineString", "LinearRing"}

    def can_handle(self, geometry_type: str) -> bool:
        return geometry_type in self.SUPPORTED_TYPES

    def measure(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
        crs_resolver: CRSResolver | None = None,
    ) -> MeasurementResult:
        resolver = crs_resolver or CRSResolver()

        # 1. Null geometry check
        if feature.geometry is None:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.LENGTH,
                value=None,
                unit=MeasurementUnit.METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning="Feature geometry is null or missing.",
            )

        # 2. Empty geometry check
        if feature.geometry.is_empty:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.LENGTH,
                value=0.0,
                unit=MeasurementUnit.METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning="Empty linear geometry with 0 length.",
            )

        # 3. Topology validity check
        if not feature.geometry.is_valid:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.LENGTH,
                value=None,
                unit=MeasurementUnit.METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning=(
                    f"Invalid linear geometry topology: "
                    f"{feature.warning_message or 'Corrupt coordinate sequence'}"
                ),
            )

        # 4. CRS Resolution & Transformation
        try:
            extent = dataset_extent or BoundingBox.from_geometries([feature.geometry])
            crs_res = resolver.resolve(
                source_crs_input=feature.source_crs,
                extent=extent,
                purpose=MeasurementPurpose.LENGTH,
            )

            transformer = GeometryTransformer(
                source_crs=crs_res.source_crs,
                target_crs=crs_res.calculation_crs,
            )
            transformed_geom = transformer.transform(feature.geometry)

            if transformed_geom is None or transformed_geom.is_empty:
                return MeasurementResult(
                    feature_index=feature.feature_index,
                    geometry_type=feature.geometry_type,
                    measurement_type=MeasurementType.LENGTH,
                    value=0.0,
                    unit=MeasurementUnit.METERS,
                    calculation_crs=crs_res.calculation_crs,
                    crs_strategy=crs_res.strategy.value,
                    status=FeatureStatus.INVALID,
                    warning="Reprojected geometry is empty.",
                )

            # 5. Metric Length Calculation in meters
            # MultiLineString length is the sum of constituent segments.
            length_m = float(transformed_geom.length)

            warning = feature.warning_message or (crs_res.warnings[0] if crs_res.warnings else None)

            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.LENGTH,
                value=length_m,
                unit=MeasurementUnit.METERS,
                calculation_crs=crs_res.calculation_crs,
                crs_strategy=crs_res.strategy.value,
                status=FeatureStatus.SUCCESS,
                warning=warning,
            )

        except (CRSError, GeospatialError) as err:
            logger.warning("Measurement failed for feature %d: %s", feature.feature_index, err)
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.LENGTH,
                value=None,
                unit=MeasurementUnit.METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.FAILED,
                warning=f"Length measurement failed: {err.message if hasattr(err, 'message') else str(err)}",
            )
        except Exception as err:
            logger.exception("Unexpected error during linestring measurement: %s", err)
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=MeasurementType.LENGTH,
                value=None,
                unit=MeasurementUnit.METERS,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.FAILED,
                warning=f"Unexpected measurement error: {err}",
            )


class PointMeasurementHandler(BaseMeasurementHandler):
    """Handles Point and MultiPoint geometries by explicitly skipping metric measurements."""

    SUPPORTED_TYPES = {"Point", "MultiPoint"}

    def can_handle(self, geometry_type: str) -> bool:
        return geometry_type in self.SUPPORTED_TYPES

    def measure(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
        crs_resolver: CRSResolver | None = None,
    ) -> MeasurementResult:
        if feature.geometry is None:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=None,
                value=None,
                unit=None,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning="Feature geometry is null or missing.",
            )

        return MeasurementResult(
            feature_index=feature.feature_index,
            geometry_type=feature.geometry_type,
            measurement_type=None,
            value=None,
            unit=None,
            calculation_crs=None,
            crs_strategy=None,
            status=FeatureStatus.SKIPPED_NOT_APPLICABLE,
            warning="Point geometries do not have area or length measurements; measurement skipped.",
        )


class GeometryCollectionHandler(BaseMeasurementHandler):
    """Handles GeometryCollection objects by inspecting constituent components."""

    def can_handle(self, geometry_type: str) -> bool:
        return geometry_type == "GeometryCollection"

    def measure(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
        crs_resolver: CRSResolver | None = None,
    ) -> MeasurementResult:
        if feature.geometry is None:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=None,
                value=None,
                unit=None,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.INVALID,
                warning="Feature geometry is null or missing.",
            )

        if not isinstance(feature.geometry, GeometryCollection) or feature.geometry.is_empty:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=None,
                value=None,
                unit=None,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.SKIPPED_NOT_APPLICABLE,
                warning="Empty GeometryCollection; measurement skipped.",
            )

        # Inspect sub-geometry types
        sub_types = {g.geom_type for g in feature.geometry.geoms if not g.is_empty}

        # Check homogeneous sub-types
        is_all_polygonal = bool(sub_types) and sub_types.issubset({"Polygon", "MultiPolygon"})
        is_all_linear = bool(sub_types) and sub_types.issubset(
            {"LineString", "MultiLineString", "LinearRing"}
        )
        is_all_point = bool(sub_types) and sub_types.issubset({"Point", "MultiPoint"})

        if is_all_polygonal:
            # Delegate to Polygon measurement
            poly_handler = PolygonMeasurementHandler()
            return poly_handler.measure(feature, dataset_extent, crs_resolver)

        if is_all_linear:
            # Delegate to LineString measurement
            line_handler = LineStringMeasurementHandler()
            return line_handler.measure(feature, dataset_extent, crs_resolver)

        if is_all_point:
            return MeasurementResult(
                feature_index=feature.feature_index,
                geometry_type=feature.geometry_type,
                measurement_type=None,
                value=None,
                unit=None,
                calculation_crs=None,
                crs_strategy=None,
                status=FeatureStatus.SKIPPED_NOT_APPLICABLE,
                warning="GeometryCollection contains only points; measurement skipped.",
            )

        # Mixed geometry collection (e.g. Polygons + LineStrings)
        # Cannot combine incompatible units (m² + m) into a single scalar value.
        return MeasurementResult(
            feature_index=feature.feature_index,
            geometry_type=feature.geometry_type,
            measurement_type=None,
            value=None,
            unit=None,
            calculation_crs=None,
            crs_strategy=None,
            status=FeatureStatus.UNSUPPORTED,
            warning=(
                f"GeometryCollection contains mixed geometry types ({', '.join(sorted(sub_types))}); "
                "combining incompatible units (area and length) into a single measurement is not supported."
            ),
        )


class UnsupportedGeometryHandler(BaseMeasurementHandler):
    """Fallback handler for unsupported geometry types."""

    def can_handle(self, geometry_type: str) -> bool:
        return True

    def measure(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
        crs_resolver: CRSResolver | None = None,
    ) -> MeasurementResult:
        return MeasurementResult(
            feature_index=feature.feature_index,
            geometry_type=feature.geometry_type,
            measurement_type=None,
            value=None,
            unit=None,
            calculation_crs=None,
            crs_strategy=None,
            status=FeatureStatus.UNSUPPORTED,
            warning=f"Geometry type '{feature.geometry_type}' is not supported for metric measurement.",
        )
