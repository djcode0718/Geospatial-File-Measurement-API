"""Measurement engine and dispatcher coordinating geometric measurement across datasets."""

import logging
from collections.abc import Iterable, Sequence

from app.db.models import FeatureStatus, MeasurementType
from app.geospatial.crs.models import BoundingBox
from app.geospatial.crs.resolver import CRSResolver
from app.geospatial.measurement.handlers import (
    BaseMeasurementHandler,
    GeometryCollectionHandler,
    LineStringMeasurementHandler,
    PointMeasurementHandler,
    PolygonMeasurementHandler,
    UnsupportedGeometryHandler,
)
from app.geospatial.measurement.models import MeasurementResult, MeasurementSummary
from app.geospatial.models import ParsedFeature

logger = logging.getLogger(__name__)


class MeasurementEngine:
    """Central coordinator for dispatching features to geometry handlers and aggregating metrics."""

    def __init__(
        self,
        crs_resolver: CRSResolver | None = None,
        custom_handlers: Sequence[BaseMeasurementHandler] | None = None,
    ) -> None:
        """Initialize measurement engine with handlers and CRS resolver.

        Args:
            crs_resolver: Resolver instance for determining optimal calculation CRS.
            custom_handlers: Optional custom handlers overriding or prepending default handlers.
        """
        self.crs_resolver = crs_resolver or CRSResolver()
        self._handlers: list[BaseMeasurementHandler] = list(custom_handlers or [])

        # Default handler registry order (specialized handlers first, fallback last)
        self._handlers.extend(
            [
                PolygonMeasurementHandler(),
                LineStringMeasurementHandler(),
                PointMeasurementHandler(),
                GeometryCollectionHandler(),
                UnsupportedGeometryHandler(),  # Catch-all fallback
            ]
        )

    def get_handler(self, geometry_type: str) -> BaseMeasurementHandler:
        """Find the first matching handler for the given geometry type name."""
        for handler in self._handlers:
            if handler.can_handle(geometry_type):
                return handler
        return UnsupportedGeometryHandler()

    def measure_feature(
        self,
        feature: ParsedFeature,
        dataset_extent: BoundingBox | None = None,
    ) -> MeasurementResult:
        """Calculate metric measurement for a single parsed feature.

        Args:
            feature: Parsed vector feature with geometry and source CRS.
            dataset_extent: Optional pre-computed bounding box for the parent dataset.

        Returns:
            MeasurementResult with metric values and status.
        """
        handler = self.get_handler(feature.geometry_type)
        return handler.measure(
            feature=feature,
            dataset_extent=dataset_extent,
            crs_resolver=self.crs_resolver,
        )

    def measure_dataset(
        self,
        features: Iterable[ParsedFeature],
    ) -> tuple[list[MeasurementResult], MeasurementSummary]:
        """Process and measure all features in a dataset, generating individual results and a summary.

        Feature-level errors are isolated so one malformed feature does not abort the dataset.

        Args:
            features: Iterable of ParsedFeature records.

        Returns:
            Tuple of (list of individual MeasurementResults, aggregated MeasurementSummary).
        """
        feature_list = list(features)
        summary = MeasurementSummary(total_features=len(feature_list))

        if not feature_list:
            return [], summary

        # 1. Compute cumulative spatial extent for dataset-level CRS resolution
        dataset_extent = BoundingBox.from_geometries(
            [f.geometry for f in feature_list if f.geometry is not None]
        )

        results: list[MeasurementResult] = []
        unique_warnings: set[str] = set()

        # 2. Process each feature independently with failure isolation
        for feat in feature_list:
            # Track geometry counts in summary
            summary.geometry_counts[feat.geometry_type] = (
                summary.geometry_counts.get(feat.geometry_type, 0) + 1
            )

            res = self.measure_feature(feat, dataset_extent=dataset_extent)
            results.append(res)

            # Record calculation CRS if not yet set
            if res.calculation_crs and not summary.calculation_crs:
                summary.calculation_crs = res.calculation_crs

            # 3. Categorize status and aggregate totals
            if res.status == FeatureStatus.SUCCESS:
                summary.measured_features += 1
                if res.measurement_type == MeasurementType.AREA and res.value is not None:
                    summary.total_area_m2 += res.value
                elif res.measurement_type == MeasurementType.LENGTH and res.value is not None:
                    summary.total_length_m += res.value
            elif res.status == FeatureStatus.SKIPPED_NOT_APPLICABLE:
                summary.skipped_features += 1
            elif res.status == FeatureStatus.UNSUPPORTED:
                summary.unsupported_features += 1
            elif res.status == FeatureStatus.INVALID:
                summary.invalid_features += 1
            elif res.status == FeatureStatus.FAILED:
                summary.failed_features += 1

            if res.warning and res.warning not in unique_warnings:
                unique_warnings.add(res.warning)
                summary.warnings.append(res.warning)

        return results, summary
