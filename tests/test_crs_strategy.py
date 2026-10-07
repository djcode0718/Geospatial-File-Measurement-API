"""Tests for CRS validation, dynamic UTM / equal-area resolution, and geometry reprojection."""

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from app.core.exceptions import (
    InvalidCRSError,
    MissingCRSError,
)
from app.geospatial.crs.models import (
    BoundingBox,
    CRSStrategy,
    MeasurementPurpose,
)
from app.geospatial.crs.resolver import CRSResolver
from app.geospatial.crs.transformer import transform_geometry
from app.geospatial.crs.validator import normalize_crs_string, validate_crs

# ==============================================================================
# 1. CRS Validation Tests
# ==============================================================================


def test_validate_crs_valid_formats() -> None:
    """Verify PyProj parses standard EPSG and OGC authority codes."""
    crs_4326 = validate_crs("EPSG:4326")
    assert crs_4326.is_geographic is True
    assert crs_4326.to_epsg() == 4326
    assert normalize_crs_string(crs_4326) == "EPSG:4326"

    crs_utm = validate_crs("EPSG:32643")
    assert crs_utm.is_projected is True
    assert crs_utm.to_epsg() == 32643
    assert normalize_crs_string(crs_utm) == "EPSG:32643"


def test_validate_crs_missing_raises_error() -> None:
    """Verify missing/None/empty CRS raises MissingCRSError."""
    with pytest.raises(MissingCRSError):
        validate_crs(None)

    with pytest.raises(MissingCRSError):
        validate_crs("   ")


def test_validate_crs_invalid_raises_error() -> None:
    """Verify invalid CRS strings raise InvalidCRSError."""
    with pytest.raises(InvalidCRSError):
        validate_crs("NOT_A_VALID_CRS_CODE")

    with pytest.raises(InvalidCRSError):
        validate_crs("EPSG:99999999")


# ==============================================================================
# 2. Dynamic UTM Resolution Tests (Local Geographic Extents)
# ==============================================================================


def test_resolve_local_utm_northern_hemisphere() -> None:
    """Verify local extent in Bangalore/Hyderabad (77.5°E, 12.9°N) resolves to UTM Zone 43N (EPSG:32643)."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=77.50, min_y=12.90, max_x=77.60, max_y=13.00)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.AREA
    )

    assert res.source_crs == "EPSG:4326"
    assert res.calculation_crs == "EPSG:32643"
    assert res.strategy == CRSStrategy.LOCAL_UTM
    assert res.requires_transformation is True
    assert "UTM Zone 43N" in res.reason


def test_resolve_local_utm_southern_hemisphere() -> None:
    """Verify local extent in Sydney (151.2°E, -33.8°S) resolves to UTM Zone 56S (EPSG:32756)."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=151.10, min_y=-33.90, max_x=151.30, max_y=-33.70)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.AREA
    )

    assert res.source_crs == "EPSG:4326"
    assert res.calculation_crs == "EPSG:32756"
    assert res.strategy == CRSStrategy.LOCAL_UTM
    assert res.requires_transformation is True
    assert "UTM Zone 56S" in res.reason


def test_resolve_local_utm_western_hemisphere() -> None:
    """Verify local extent in New York (-74.0°W, 40.7°N) resolves to UTM Zone 18N (EPSG:32618)."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=-74.05, min_y=40.65, max_x=-73.95, max_y=40.75)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.LENGTH
    )

    assert res.calculation_crs == "EPSG:32618"
    assert res.strategy == CRSStrategy.LOCAL_UTM
    assert "UTM Zone 18N" in res.reason


# ==============================================================================
# 3. Multi-Zone and Broad Extent Fallback Tests
# ==============================================================================


def test_resolve_multi_zone_area_purpose_uses_equal_area() -> None:
    """Verify broad extent spanning >6° longitude selects EPSG:6933 (EASE-Grid Equal Area) for AREA purpose."""
    resolver = CRSResolver()
    # Spanning from 5°E to 18°E (13° longitude span across 3 UTM zones)
    extent = BoundingBox(min_x=5.0, min_y=45.0, max_x=18.0, max_y=52.0)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.AREA
    )

    assert res.calculation_crs == "EPSG:6933"
    assert res.strategy == CRSStrategy.GLOBAL_EQUAL_AREA
    assert len(res.warnings) > 0
    assert "equal-area" in res.reason.lower()


def test_resolve_multi_zone_length_purpose_cross_zone_fallback() -> None:
    """Verify broad extent for LENGTH purpose uses centroid UTM with cross-zone warning."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=5.0, min_y=45.0, max_x=18.0, max_y=52.0)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.LENGTH
    )

    assert res.strategy == CRSStrategy.CROSS_ZONE_FALLBACK
    assert "EPSG:326" in res.calculation_crs
    assert len(res.warnings) > 0


# ==============================================================================
# 4. Polar Extent Tests
# ==============================================================================


def test_resolve_arctic_polar_region() -> None:
    """Verify extent at latitude >= 84°N selects Arctic Polar Stereographic (EPSG:3413)."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=-40.0, min_y=85.0, max_x=-30.0, max_y=87.0)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.AREA
    )

    assert res.calculation_crs == "EPSG:3413"
    assert res.strategy == CRSStrategy.POLAR_STEREOGRAPHIC
    assert "Arctic" in res.reason


def test_resolve_antarctic_polar_region() -> None:
    """Verify extent at latitude <= -80°S selects Antarctic Polar Stereographic (EPSG:3031)."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=0.0, min_y=-85.0, max_x=20.0, max_y=-82.0)

    res = resolver.resolve(
        source_crs_input="EPSG:4326", extent=extent, purpose=MeasurementPurpose.AREA
    )

    assert res.calculation_crs == "EPSG:3031"
    assert res.strategy == CRSStrategy.POLAR_STEREOGRAPHIC
    assert "Antarctic" in res.reason


# ==============================================================================
# 5. Already-Projected Source CRS Tests
# ==============================================================================


def test_resolve_already_projected_crs_preserves_projection() -> None:
    """Verify a dataset already in a projected CRS (e.g. EPSG:32643) is preserved without reprojection."""
    resolver = CRSResolver()
    extent = BoundingBox(min_x=780000.0, min_y=1430000.0, max_x=790000.0, max_y=1440000.0)

    res = resolver.resolve(
        source_crs_input="EPSG:32643", extent=extent, purpose=MeasurementPurpose.AREA
    )

    assert res.source_crs == "EPSG:32643"
    assert res.calculation_crs == "EPSG:32643"
    assert res.strategy == CRSStrategy.PRESERVED_SOURCE_PROJECTED
    assert res.requires_transformation is False


# ==============================================================================
# 6. Coordinate Reprojection & Numerical Sanity Tests
# ==============================================================================


def test_transform_point_reprojection() -> None:
    """Verify Point reprojection from EPSG:4326 degrees to UTM Zone 43N meters."""
    pt = Point(77.5946, 12.9716)  # Bangalore coordinates
    transformed = transform_geometry(pt, source_crs="EPSG:4326", target_crs="EPSG:32643")

    assert isinstance(transformed, Point)
    assert transformed is not None
    # UTM coordinates in Zone 43N should be on the order of 10^5 to 10^6 meters
    assert 700_000.0 < transformed.x < 900_000.0
    assert 1_300_000.0 < transformed.y < 1_500_000.0

    # Ensure source point was not mutated
    assert pt.x == 77.5946
    assert pt.y == 12.9716


def test_transform_linestring_reprojection() -> None:
    """Verify LineString coordinate reprojection preserves vertex count and structure."""
    line = LineString([[77.59, 12.97], [77.60, 12.98], [77.61, 12.99]])
    transformed = transform_geometry(line, source_crs="EPSG:4326", target_crs="EPSG:32643")

    assert isinstance(transformed, LineString)
    assert transformed is not None
    assert len(transformed.coords) == 3
    assert transformed.coords[0][0] > 700_000.0


def test_transform_polygon_with_hole_reprojection() -> None:
    """Verify Polygon with hole reprojects cleanly preserving topological validity and holes."""
    outer = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    hole = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8), (0.2, 0.2)]
    poly = Polygon(shell=outer, holes=[hole])

    transformed = transform_geometry(poly, source_crs="EPSG:4326", target_crs="EPSG:32631")

    assert isinstance(transformed, Polygon)
    assert transformed is not None
    assert transformed.is_valid is True
    assert len(transformed.interiors) == 1  # 1 hole interior preserved


def test_transform_multipolygon_reprojection() -> None:
    """Verify MultiPolygon reprojects constituent parts into a valid MultiPolygon."""
    p1 = Polygon([(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5), (0.0, 0.0)])
    p2 = Polygon([(1.0, 1.0), (1.5, 1.0), (1.5, 1.5), (1.0, 1.5), (1.0, 1.0)])
    mp = MultiPolygon([p1, p2])

    transformed = transform_geometry(mp, source_crs="EPSG:4326", target_crs="EPSG:32631")

    assert isinstance(transformed, MultiPolygon)
    assert transformed is not None
    assert len(transformed.geoms) == 2


def test_transform_identity_returns_unchanged() -> None:
    """Verify transforming between identical source and target returns original geometry without overhead."""
    pt = Point(100.0, 200.0)
    transformed = transform_geometry(pt, source_crs="EPSG:32643", target_crs="EPSG:32643")
    assert transformed == pt


def test_transform_invalid_crs_raises_error() -> None:
    """Verify transformation with invalid CRS definition raises CRSTransformationError or InvalidCRSError."""
    pt = Point(10.0, 20.0)
    with pytest.raises(InvalidCRSError):
        transform_geometry(pt, source_crs="EPSG:4326", target_crs="INVALID_CODE")
