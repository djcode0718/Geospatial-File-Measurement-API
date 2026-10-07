"""Tests for ShapefileReader, KMLReader, and feature normalization."""

from pathlib import Path

import fiona
import pytest
from shapely.geometry import LineString, Point, Polygon

from app.core.exceptions import GeospatialParseError
from app.geospatial.models import ParsedFeature
from app.geospatial.readers.kml import KMLReader
from app.geospatial.readers.shapefile import ShapefileReader

# ==============================================================================
# 1. Helper Fixture Generators
# ==============================================================================


@pytest.fixture
def shapefile_polygons_wgs84(tmp_path: Path) -> Path:
    """Generate a valid ESRI Shapefile with Polygons in EPSG:4326."""
    shp_path = tmp_path / "polygons_wgs84.shp"
    schema = {
        "geometry": "Polygon",
        "properties": {"name": "str", "area_code": "int"},
    }
    with fiona.open(
        str(shp_path), "w", driver="ESRI Shapefile", schema=schema, crs="EPSG:4326"
    ) as dst:
        dst.write(
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [77.59, 12.97],
                            [77.60, 12.97],
                            [77.60, 12.98],
                            [77.59, 12.98],
                            [77.59, 12.97],
                        ]
                    ],
                },
                "properties": {"name": "Sector 1", "area_code": 101},
            }
        )
        dst.write(
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [77.61, 12.97],
                            [77.62, 12.97],
                            [77.62, 12.98],
                            [77.61, 12.98],
                            [77.61, 12.97],
                        ]
                    ],
                },
                "properties": {"name": "Sector 2", "area_code": 102},
            }
        )
    return shp_path


@pytest.fixture
def shapefile_no_crs(tmp_path: Path) -> Path:
    """Generate a valid ESRI Shapefile without .prj / CRS metadata."""
    shp_path = tmp_path / "no_crs.shp"
    schema = {"geometry": "LineString", "properties": {"road": "str"}}
    # Writing without CRS creates .shp, .shx, .dbf without .prj
    with fiona.open(str(shp_path), "w", driver="ESRI Shapefile", schema=schema, crs=None) as dst:
        dst.write(
            {
                "geometry": {"type": "LineString", "coordinates": [[10.0, 20.0], [10.5, 20.5]]},
                "properties": {"road": "Highway 1"},
            }
        )
    return shp_path


@pytest.fixture
def shapefile_empty(tmp_path: Path) -> Path:
    """Generate a valid ESRI Shapefile with zero features."""
    shp_path = tmp_path / "empty.shp"
    schema = {"geometry": "Point", "properties": {"tag": "str"}}
    with fiona.open(
        str(shp_path), "w", driver="ESRI Shapefile", schema=schema, crs="EPSG:4326"
    ) as _:
        pass
    return shp_path


# ==============================================================================
# 2. Shapefile Reader Tests
# ==============================================================================


def test_shapefile_reader_polygons_wgs84(shapefile_polygons_wgs84: Path) -> None:
    """Verify ShapefileReader parses polygon features, attributes, and WGS84 CRS."""
    reader = ShapefileReader()
    features, metadata = reader.read_dataset(shapefile_polygons_wgs84)

    assert len(features) == 2
    assert metadata.total_features == 2
    assert metadata.source_crs == "EPSG:4326"
    assert metadata.geometry_counts == {"Polygon": 2}

    # Verify first feature
    f0 = features[0]
    assert isinstance(f0, ParsedFeature)
    assert f0.feature_index == 0
    assert f0.geometry_type == "Polygon"
    assert isinstance(f0.geometry, Polygon)
    assert f0.is_valid_geometry is True
    assert f0.properties["name"] == "Sector 1"
    assert f0.properties["area_code"] == 101
    assert f0.source_crs == "EPSG:4326"

    # Verify second feature
    f1 = features[1]
    assert f1.feature_index == 1
    assert f1.properties["name"] == "Sector 2"


def test_shapefile_reader_missing_crs(shapefile_no_crs: Path) -> None:
    """Verify Shapefile without .prj retains source_crs = None without assuming EPSG:4326."""
    reader = ShapefileReader()
    features, metadata = reader.read_dataset(shapefile_no_crs)

    assert len(features) == 1
    assert metadata.source_crs is None
    assert features[0].source_crs is None
    assert features[0].geometry_type == "LineString"
    assert isinstance(features[0].geometry, LineString)


def test_shapefile_reader_empty_dataset(shapefile_empty: Path) -> None:
    """Verify Shapefile with 0 features returns empty collection without errors."""
    reader = ShapefileReader()
    features, metadata = reader.read_dataset(shapefile_empty)

    assert len(features) == 0
    assert metadata.total_features == 0
    assert metadata.source_crs == "EPSG:4326"


def test_shapefile_reader_corrupt_file(tmp_path: Path) -> None:
    """Verify unreadable/corrupt Shapefile raises GeospatialParseError."""
    corrupt_shp = tmp_path / "corrupt.shp"
    corrupt_shp.write_bytes(b"NOT_A_VALID_SHP_FILE_HEADER")

    reader = ShapefileReader()
    with pytest.raises(GeospatialParseError):
        list(reader.read_features(corrupt_shp))


# ==============================================================================
# 3. KML Reader Tests
# ==============================================================================


def test_kml_reader_mixed_placemarks(tmp_path: Path) -> None:
    """Verify KMLReader parses Polygon, LineString, and Point placemarks with attributes."""
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark id="pm_0">
          <name>Central Park</name>
          <description>Urban park</description>
          <ExtendedData>
            <Data name="zone"><value>Recreation</value></Data>
          </ExtendedData>
          <Polygon>
            <outerBoundaryIs>
              <LinearRing>
                <coordinates>
                  -73.97,40.78,0 -73.95,40.78,0 -73.95,40.80,0 -73.97,40.80,0 -73.97,40.78,0
                </coordinates>
              </LinearRing>
            </outerBoundaryIs>
          </Polygon>
        </Placemark>
        <Placemark id="pm_1">
          <name>Main Avenue</name>
          <LineString>
            <coordinates>-73.97,40.78,0 -73.96,40.79,0 -73.95,40.80,0</coordinates>
          </LineString>
        </Placemark>
        <Placemark id="pm_2">
          <name>Observation Tower</name>
          <Point>
            <coordinates>-73.96,40.79,50</coordinates>
          </Point>
        </Placemark>
      </Document>
    </kml>"""

    kml_file = tmp_path / "survey.kml"
    kml_file.write_text(kml_content, encoding="utf-8")

    reader = KMLReader()
    features, metadata = reader.read_dataset(kml_file)

    assert len(features) == 3
    assert metadata.total_features == 3
    assert metadata.source_crs == "EPSG:4326"
    assert metadata.geometry_counts == {"Polygon": 1, "LineString": 1, "Point": 1}

    # 1. Check Polygon feature
    f0 = features[0]
    assert f0.feature_index == 0
    assert f0.geometry_type == "Polygon"
    assert isinstance(f0.geometry, Polygon)
    assert f0.properties["name"] == "Central Park"
    assert f0.properties["description"] == "Urban park"
    assert f0.properties["zone"] == "Recreation"

    # 2. Check LineString feature
    f1 = features[1]
    assert f1.feature_index == 1
    assert f1.geometry_type == "LineString"
    assert isinstance(f1.geometry, LineString)
    assert f1.properties["name"] == "Main Avenue"

    # 3. Check Point feature
    f2 = features[2]
    assert f2.feature_index == 2
    assert f2.geometry_type == "Point"
    assert isinstance(f2.geometry, Point)
    assert f2.properties["name"] == "Observation Tower"


def test_kml_reader_polygon_with_hole(tmp_path: Path) -> None:
    """Verify KML Polygon with innerBoundaryIs (hole) is preserved in Shapely representation."""
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Placemark>
        <name>Donut Polygon</name>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>0,0 10,0 10,10 0,10 0,0</coordinates>
            </LinearRing>
          </outerBoundaryIs>
          <innerBoundaryIs>
            <LinearRing>
              <coordinates>2,2 8,2 8,8 2,8 2,2</coordinates>
            </LinearRing>
          </innerBoundaryIs>
        </Polygon>
      </Placemark>
    </kml>"""

    kml_file = tmp_path / "donut.kml"
    kml_file.write_text(kml_content, encoding="utf-8")

    reader = KMLReader()
    features, _ = reader.read_dataset(kml_file)

    assert len(features) == 1
    poly: Polygon = features[0].geometry  # type: ignore[assignment]
    assert isinstance(poly, Polygon)
    assert len(poly.interiors) == 1  # 1 hole interior ring preserved


def test_kml_reader_placemark_without_geometry(tmp_path: Path) -> None:
    """Verify Placemarks without geometries are captured with geometry=None and status warning."""
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Placemark>
        <name>Metadata Only Placemark</name>
        <description>No coordinates attached</description>
      </Placemark>
    </kml>"""

    kml_file = tmp_path / "no_geom.kml"
    kml_file.write_text(kml_content, encoding="utf-8")

    reader = KMLReader()
    features, metadata = reader.read_dataset(kml_file)

    assert len(features) == 1
    f0 = features[0]
    assert f0.geometry is None
    assert f0.geometry_type == "None"
    assert f0.is_valid_geometry is False
    assert "no geometry" in (f0.warning_message or "")


# ==============================================================================
# 4. Feature Normalization & Error Isolation Tests
# ==============================================================================


def test_feature_normalization_consistency(shapefile_polygons_wgs84: Path, tmp_path: Path) -> None:
    """Verify Shapefile and KML produce identical normalized ParsedFeature structure."""
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Placemark>
        <name>Sector 1</name>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>77.59,12.97 77.60,12.97 77.60,12.98 77.59,12.98 77.59,12.97</coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
      </Placemark>
    </kml>"""
    kml_file = tmp_path / "poly.kml"
    kml_file.write_text(kml_content, encoding="utf-8")

    shp_features, _ = ShapefileReader().read_dataset(shapefile_polygons_wgs84)
    kml_features, _ = KMLReader().read_dataset(kml_file)

    f_shp = shp_features[0]
    f_kml = kml_features[0]

    # Both must match the same schema fields and types
    assert f_shp.geometry_type == f_kml.geometry_type == "Polygon"
    assert f_shp.source_crs == f_kml.source_crs == "EPSG:4326"
    assert isinstance(f_shp.geometry, Polygon)
    assert isinstance(f_kml.geometry, Polygon)
    assert f_shp.feature_index == f_kml.feature_index == 0
    assert "name" in f_shp.properties and "name" in f_kml.properties


def test_kml_feature_level_error_isolation(tmp_path: Path) -> None:
    """Verify one corrupt or invalid Placemark does not crash other independent valid features."""
    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark id="f0">
          <name>Valid Feature 0</name>
          <Point><coordinates>77.59,12.97,0</coordinates></Point>
        </Placemark>
        <Placemark id="f1">
          <name>Malformed Feature 1</name>
          <LineString><coordinates>corrupt_string_not_numbers</coordinates></LineString>
        </Placemark>
        <Placemark id="f2">
          <name>Valid Feature 2</name>
          <Point><coordinates>77.60,12.98,0</coordinates></Point>
        </Placemark>
      </Document>
    </kml>"""

    kml_file = tmp_path / "isolated_errors.kml"
    kml_file.write_text(kml_content, encoding="utf-8")

    reader = KMLReader()
    features, metadata = reader.read_dataset(kml_file)

    assert len(features) == 3
    assert metadata.total_features == 3

    # Feature 0: Valid Point
    assert features[0].feature_index == 0
    assert features[0].geometry_type == "Point"
    assert isinstance(features[0].geometry, Point)
    assert features[0].is_valid_geometry is True

    # Feature 1: Isolated failure (captured without crashing)
    assert features[1].feature_index == 1
    assert features[1].geometry is None
    assert features[1].is_valid_geometry is False
    assert features[1].warning_message is not None

    # Feature 2: Valid Point
    assert features[2].feature_index == 2
    assert features[2].geometry_type == "Point"
    assert isinstance(features[2].geometry, Point)
    assert features[2].is_valid_geometry is True
