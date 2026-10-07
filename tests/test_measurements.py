"""Unit and integration tests for Phase 2.3 measurement engine and geometry handlers."""

import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.db.models import FeatureStatus, MeasurementType
from app.geospatial.measurement.engine import MeasurementEngine
from app.geospatial.measurement.handlers import (
    GeometryCollectionHandler,
    LineStringMeasurementHandler,
    PointMeasurementHandler,
    PolygonMeasurementHandler,
    UnsupportedGeometryHandler,
)
from app.geospatial.measurement.models import MeasurementUnit
from app.geospatial.models import ParsedFeature


class TestPolygonMeasurementHandler:
    """Test surface area calculation policies for Polygon and MultiPolygon."""

    def test_measure_simple_square_projected(self):
        """A 10m x 10m square in EPSG:32643 should measure exactly 100.0 m²."""
        poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="Polygon",
            geometry=poly,
            source_crs="EPSG:32643",
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.measurement_type == MeasurementType.AREA
        assert res.unit == MeasurementUnit.SQUARE_METERS
        assert res.value == pytest.approx(100.0, rel=1e-5)
        assert res.calculation_crs == "EPSG:32643"

    def test_measure_rectangle_projected(self):
        """A 20m x 50m rectangle in EPSG:32643 should measure exactly 1000.0 m²."""
        poly = Polygon([(100, 200), (120, 200), (120, 250), (100, 250), (100, 200)])
        feature = ParsedFeature(
            feature_index=1,
            geometry_type="Polygon",
            geometry=poly,
            source_crs="EPSG:32643",
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.value == pytest.approx(1000.0, rel=1e-5)

    def test_measure_polygon_with_hole(self):
        """A 100m x 100m outer square (10000 m²) with a 20m x 20m inner hole (400 m²) = 9600 m²."""
        exterior = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
        interior = [[(40, 40), (60, 40), (60, 60), (40, 60), (40, 40)]]
        poly = Polygon(shell=exterior, holes=interior)

        feature = ParsedFeature(
            feature_index=2,
            geometry_type="Polygon",
            geometry=poly,
            source_crs="EPSG:32643",
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.value == pytest.approx(9600.0, rel=1e-5)

    def test_measure_multipolygon(self):
        """MultiPolygon containing two 10m x 10m squares should measure 200.0 m²."""
        p1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
        p2 = Polygon([(50, 50), (60, 50), (60, 60), (50, 60), (50, 50)])
        multipoly = MultiPolygon([p1, p2])

        feature = ParsedFeature(
            feature_index=3,
            geometry_type="MultiPolygon",
            geometry=multipoly,
            source_crs="EPSG:32643",
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.value == pytest.approx(200.0, rel=1e-5)

    def test_empty_and_null_polygon(self):
        """Empty and null polygon geometries should be classified as INVALID without crashing."""
        handler = PolygonMeasurementHandler()

        null_feat = ParsedFeature(feature_index=0, geometry_type="Polygon", geometry=None)
        res_null = handler.measure(null_feat)
        assert res_null.status == FeatureStatus.INVALID
        assert res_null.value is None

        empty_feat = ParsedFeature(
            feature_index=1,
            geometry_type="Polygon",
            geometry=Polygon(),
            source_crs="EPSG:32643",
        )
        res_empty = handler.measure(empty_feat)
        assert res_empty.status == FeatureStatus.INVALID
        assert res_empty.value == 0.0

    def test_invalid_polygon_topology(self):
        """Self-intersecting bow-tie polygon is marked INVALID without silent mutation."""
        bowtie = Polygon([(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)])
        assert not bowtie.is_valid

        feature = ParsedFeature(
            feature_index=4,
            geometry_type="Polygon",
            geometry=bowtie,
            source_crs="EPSG:32643",
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.INVALID
        assert "Invalid polygon geometry topology" in (res.warning or "")


class TestLineStringMeasurementHandler:
    """Test linear length measurement policies for LineString and MultiLineString."""

    def test_measure_straight_horizontal_line(self):
        """A 300m horizontal line in EPSG:32643 should measure exactly 300.0 m."""
        line = LineString([(1000, 2000), (1300, 2000)])
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="LineString",
            geometry=line,
            source_crs="EPSG:32643",
        )
        handler = LineStringMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.measurement_type == MeasurementType.LENGTH
        assert res.unit == MeasurementUnit.METERS
        assert res.value == pytest.approx(300.0, rel=1e-5)

    def test_measure_pythagorean_triangle_line(self):
        """A line spanning dx=300m, dy=400m should measure 500.0 m (3-4-5 triangle)."""
        line = LineString([(0, 0), (300, 400)])
        feature = ParsedFeature(
            feature_index=1,
            geometry_type="LineString",
            geometry=line,
            source_crs="EPSG:32643",
        )
        handler = LineStringMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.value == pytest.approx(500.0, rel=1e-5)

    def test_measure_multilinestring(self):
        """MultiLineString with two 150m segments should total 300.0 m."""
        mls = MultiLineString(
            [
                [(0, 0), (150, 0)],
                [(200, 0), (350, 0)],
            ]
        )
        feature = ParsedFeature(
            feature_index=2,
            geometry_type="MultiLineString",
            geometry=mls,
            source_crs="EPSG:32643",
        )
        handler = LineStringMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.value == pytest.approx(300.0, rel=1e-5)

    def test_empty_and_null_linestring(self):
        """Empty and null linear geometries return INVALID status without crashing."""
        handler = LineStringMeasurementHandler()

        null_feat = ParsedFeature(feature_index=0, geometry_type="LineString", geometry=None)
        res_null = handler.measure(null_feat)
        assert res_null.status == FeatureStatus.INVALID

        empty_feat = ParsedFeature(
            feature_index=1,
            geometry_type="LineString",
            geometry=LineString(),
            source_crs="EPSG:32643",
        )
        res_empty = handler.measure(empty_feat)
        assert res_empty.status == FeatureStatus.INVALID
        assert res_empty.value == 0.0


class TestPointMeasurementHandler:
    """Test point handling policy where points are explicitly skipped."""

    def test_point_is_explicitly_skipped(self):
        """Points must have None for value/unit and status SKIPPED_NOT_APPLICABLE."""
        pt = Point(78.48, 17.38)
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="Point",
            geometry=pt,
            source_crs="EPSG:4326",
        )
        handler = PointMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SKIPPED_NOT_APPLICABLE
        assert res.measurement_type is None
        assert res.value is None
        assert res.unit is None
        assert "skipped" in (res.warning or "").lower()

    def test_multipoint_is_explicitly_skipped(self):
        """MultiPoint must also be skipped without fabricating 0 values."""
        mpt = MultiPoint([(78.48, 17.38), (78.50, 17.40)])
        feature = ParsedFeature(
            feature_index=1,
            geometry_type="MultiPoint",
            geometry=mpt,
            source_crs="EPSG:4326",
        )
        handler = PointMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SKIPPED_NOT_APPLICABLE
        assert res.value is None


class TestGeometryCollectionHandler:
    """Test GeometryCollection handling and unit incompatibility defense."""

    def test_homogeneous_polygonal_collection(self):
        """Homogeneous collection of Polygons calculates cumulative area."""
        gc = GeometryCollection(
            [
                Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
                Polygon([(20, 20), (30, 20), (30, 30), (20, 30), (20, 20)]),
            ]
        )
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="GeometryCollection",
            geometry=gc,
            source_crs="EPSG:32643",
        )
        handler = GeometryCollectionHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.measurement_type == MeasurementType.AREA
        assert res.value == pytest.approx(200.0, rel=1e-5)

    def test_homogeneous_linear_collection(self):
        """Homogeneous collection of LineStrings calculates cumulative length."""
        gc = GeometryCollection(
            [
                LineString([(0, 0), (100, 0)]),
                LineString([(0, 0), (0, 200)]),
            ]
        )
        feature = ParsedFeature(
            feature_index=1,
            geometry_type="GeometryCollection",
            geometry=gc,
            source_crs="EPSG:32643",
        )
        handler = GeometryCollectionHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.measurement_type == MeasurementType.LENGTH
        assert res.value == pytest.approx(300.0, rel=1e-5)

    def test_mixed_geometry_collection_rejected_without_bogus_summation(self):
        """Mixed collection of Polygon + LineString must NOT sum m² and m."""
        gc = GeometryCollection(
            [
                Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
                LineString([(0, 0), (100, 0)]),
            ]
        )
        feature = ParsedFeature(
            feature_index=2,
            geometry_type="GeometryCollection",
            geometry=gc,
            source_crs="EPSG:32643",
        )
        handler = GeometryCollectionHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.UNSUPPORTED
        assert res.value is None
        assert "combining incompatible units" in (res.warning or "")


class TestCRSIntegrationAndReprojection:
    """Test CRS resolution and coordinate transformation integration during measurement."""

    def test_geographic_polygon_reprojects_to_utm_and_measures_m2(self):
        """Geographic polygon in EPSG:4326 must be reprojected to metric UTM before area measurement."""
        # Synthetic ~111m x ~111m box near Hyderabad (approx 0.001 deg)
        # 0.001 deg lon at lat 17.38 deg ~ 106.1m; 0.001 deg lat ~ 110.6m -> area ~ 11,735 m²
        lon, lat = 78.4867, 17.3850
        d_deg = 0.001
        poly = Polygon(
            [
                (lon, lat),
                (lon + d_deg, lat),
                (lon + d_deg, lat + d_deg),
                (lon, lat + d_deg),
                (lon, lat),
            ]
        )
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="Polygon",
            geometry=poly,
            source_crs="EPSG:4326",
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.calculation_crs == "EPSG:32644"  # 78.48°E is in UTM Zone 44N
        assert res.unit == MeasurementUnit.SQUARE_METERS
        # Area should be on the order of 11,000 - 12,500 m² (NOT 0.000001 deg²)
        assert 10000.0 < res.value < 13000.0

    def test_geographic_linestring_reprojects_to_utm_and_measures_meters(self):
        """Geographic LineString in EPSG:4326 must measure in linear meters, not degrees."""
        # 0.01 deg north along meridian ~ 1,106 meters
        lon, lat = 78.4867, 17.3850
        line = LineString([(lon, lat), (lon, lat + 0.01)])
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="LineString",
            geometry=line,
            source_crs="EPSG:4326",
        )
        handler = LineStringMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.SUCCESS
        assert res.calculation_crs == "EPSG:32644"
        assert res.unit == MeasurementUnit.METERS
        assert 1000.0 < res.value < 1200.0

    def test_missing_crs_fails_explicitly_with_isolation(self):
        """Feature with missing CRS returns FAILED status with clear error explanation."""
        poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
        feature = ParsedFeature(
            feature_index=0,
            geometry_type="Polygon",
            geometry=poly,
            source_crs=None,  # Missing CRS
        )
        handler = PolygonMeasurementHandler()
        res = handler.measure(feature)

        assert res.status == FeatureStatus.FAILED
        assert res.value is None
        assert "Source CRS is missing" in (res.warning or "")


class TestMeasurementEngineAndSummary:
    """Test full dataset measurement dispatcher, failure isolation, and summary aggregation."""

    def test_measure_mixed_dataset(self):
        """Dataset containing polygons, linestrings, points, and invalid features processes cleanly."""
        features = [
            # 1. Valid Polygon (100 m²)
            ParsedFeature(
                feature_index=0,
                geometry_type="Polygon",
                geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
                source_crs="EPSG:32643",
            ),
            # 2. Valid LineString (300 m)
            ParsedFeature(
                feature_index=1,
                geometry_type="LineString",
                geometry=LineString([(0, 0), (300, 0)]),
                source_crs="EPSG:32643",
            ),
            # 3. Point (Skipped)
            ParsedFeature(
                feature_index=2,
                geometry_type="Point",
                geometry=Point(50, 50),
                source_crs="EPSG:32643",
            ),
            # 4. Another Polygon (400 m²)
            ParsedFeature(
                feature_index=3,
                geometry_type="Polygon",
                geometry=Polygon([(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]),
                source_crs="EPSG:32643",
            ),
            # 5. Invalid Polygon (Bow-tie)
            ParsedFeature(
                feature_index=4,
                geometry_type="Polygon",
                geometry=Polygon([(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)]),
                source_crs="EPSG:32643",
            ),
            # 6. Unsupported Geometry type
            ParsedFeature(
                feature_index=5,
                geometry_type="PolyhedralSurface",
                geometry=Point(0, 0),  # dummy
                source_crs="EPSG:32643",
            ),
        ]

        engine = MeasurementEngine()
        results, summary = engine.measure_dataset(features)

        assert len(results) == 6
        assert summary.total_features == 6
        assert summary.measured_features == 3  # 2 polygons + 1 linestring
        assert summary.skipped_features == 1  # 1 point
        assert summary.invalid_features == 1  # 1 bow-tie polygon
        assert summary.unsupported_features == 1  # 1 polyhedralsurface

        # Verify separate aggregations
        assert summary.total_area_m2 == pytest.approx(500.0, rel=1e-5)  # 100 + 400
        assert summary.total_length_m == pytest.approx(300.0, rel=1e-5)  # 300

        # Verify geometry breakdown
        assert summary.geometry_counts["Polygon"] == 3
        assert summary.geometry_counts["LineString"] == 1
        assert summary.geometry_counts["Point"] == 1
        assert summary.geometry_counts["PolyhedralSurface"] == 1

    def test_measure_empty_dataset(self):
        """Empty dataset returns empty results and zero summary."""
        engine = MeasurementEngine()
        results, summary = engine.measure_dataset([])

        assert results == []
        assert summary.total_features == 0
        assert summary.measured_features == 0
        assert summary.total_area_m2 == 0.0
        assert summary.total_length_m == 0.0

    def test_unsupported_handler_direct(self):
        """Direct call to UnsupportedGeometryHandler returns UNSUPPORTED status."""
        feat = ParsedFeature(
            feature_index=0,
            geometry_type="CustomTIN",
            geometry=Point(0, 0),
        )
        handler = UnsupportedGeometryHandler()
        assert handler.can_handle("CustomTIN")
        res = handler.measure(feat)
        assert res.status == FeatureStatus.UNSUPPORTED
        assert res.value is None
