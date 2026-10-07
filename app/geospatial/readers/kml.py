import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import GeospatialParseError
from app.geospatial.models import ParsedFeature
from app.geospatial.readers.base import BaseVectorReader
from app.storage.xml_validator import validate_kml_xml_safety

logger = logging.getLogger(__name__)


class KMLReader(BaseVectorReader):
    """Reader for Keyhole Markup Language (.kml) files with XML security verification."""

    # Default namespace for standard OGC KML 2.2
    KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}

    def get_source_crs(self, file_path: Path) -> str | None:
        """KML 2.2 OGC standard mandates WGS 84 (EPSG:4326) geographic coordinates."""
        return "EPSG:4326"

    def read_features(self, file_path: Path) -> Iterator[ParsedFeature]:
        """Read and yield normalized ParsedFeature objects from a KML file.

        Args:
            file_path: Path to validated .kml file.

        Yields:
            ParsedFeature instances.

        Raises:
            GeospatialParseError: If KML cannot be opened or parsed.
        """
        if not file_path.exists():
            raise GeospatialParseError(f"KML file does not exist at '{file_path}'")

        try:
            raw_bytes = file_path.read_bytes()
            # 1. Enforce XML security validation (blocks XXE, Billion Laughs, etc.)
            validate_kml_xml_safety(raw_bytes)

            root = ElementTree.fromstring(raw_bytes)
        except Exception as err:
            raise GeospatialParseError(
                f"Failed to parse KML at '{file_path.name}': {err}",
                details={"file": str(file_path), "error": str(err)},
            ) from err

        # In standard KML, CRS is defined as WGS 84 (EPSG:4326) longitude/latitude
        source_crs = "EPSG:4326"

        # Find all Placemark elements across any namespace
        placemarks = self._find_all_tags(root, "Placemark")

        for idx, placemark in enumerate(placemarks):
            try:
                feature_id = placemark.get("id") or str(idx)
                props = self._extract_placemark_properties(placemark)

                geom = self._extract_geometry(placemark)

                if geom is None:
                    yield ParsedFeature(
                        feature_index=idx,
                        feature_id=feature_id,
                        geometry=None,
                        geometry_type="None",
                        properties=props,
                        source_crs=source_crs,
                        is_valid_geometry=False,
                        warning_message="Placemark contains no geometry",
                    )
                    continue

                geom_type = geom.geom_type
                is_valid = geom.is_valid
                warning = (
                    None if is_valid else "Geometry topology is invalid (e.g., self-intersection)"
                )

                yield ParsedFeature(
                    feature_index=idx,
                    feature_id=feature_id,
                    geometry=geom,
                    geometry_type=geom_type,
                    properties=props,
                    source_crs=source_crs,
                    is_valid_geometry=is_valid,
                    warning_message=warning,
                )

            except Exception as err:
                logger.warning(
                    "Error parsing Placemark index %d in %s: %s", idx, file_path.name, err
                )
                yield ParsedFeature(
                    feature_index=idx,
                    feature_id=idx,
                    geometry=None,
                    geometry_type="Invalid",
                    properties=self._extract_placemark_properties(placemark)
                    if placemark is not None
                    else {},
                    source_crs=source_crs,
                    is_valid_geometry=False,
                    warning_message=f"Failed to parse Placemark geometry: {err}",
                )

    def _extract_geometry(self, placemark: Element) -> BaseGeometry | None:
        """Extract and construct Shapely geometry from a Placemark element."""
        # Check for Polygon
        polygon_elem = self._find_first_tag(placemark, "Polygon")
        if polygon_elem is not None:
            return self._parse_polygon(polygon_elem)

        # Check for LineString
        linestring_elem = self._find_first_tag(placemark, "LineString")
        if linestring_elem is not None:
            return self._parse_linestring(linestring_elem)

        # Check for Point
        point_elem = self._find_first_tag(placemark, "Point")
        if point_elem is not None:
            return self._parse_point(point_elem)

        # Check for MultiGeometry
        multigeom_elem = self._find_first_tag(placemark, "MultiGeometry")
        if multigeom_elem is not None:
            return self._parse_multi_geometry(multigeom_elem)

        return None

    def _parse_polygon(self, polygon_elem: Element) -> Polygon | None:
        """Parse KML Polygon outer and inner boundary coordinates."""
        outer_elem = self._find_first_tag(polygon_elem, "outerBoundaryIs")
        if outer_elem is None:
            return None

        outer_ring_elem = self._find_first_tag(outer_elem, "LinearRing")
        if outer_ring_elem is None:
            return None

        outer_coords = self._parse_coordinates(outer_ring_elem)
        if len(outer_coords) < 3:
            return None

        # Parse optional inner rings (holes)
        inner_rings: list[list[tuple[float, float]]] = []
        for inner_elem in self._find_all_tags(polygon_elem, "innerBoundaryIs"):
            inner_ring_elem = self._find_first_tag(inner_elem, "LinearRing")
            if inner_ring_elem is not None:
                inner_coords = self._parse_coordinates(inner_ring_elem)
                if len(inner_coords) >= 3:
                    inner_rings.append(inner_coords)

        return Polygon(shell=outer_coords, holes=inner_rings)

    def _parse_linestring(self, linestring_elem: Element) -> LineString | None:
        """Parse KML LineString coordinates."""
        coords = self._parse_coordinates(linestring_elem)
        if len(coords) < 2:
            return None
        return LineString(coords)

    def _parse_point(self, point_elem: Element) -> Point | None:
        """Parse KML Point coordinate."""
        coords = self._parse_coordinates(point_elem)
        if not coords:
            return None
        return Point(coords[0])

    def _parse_multi_geometry(self, multi_elem: Element) -> BaseGeometry | None:
        """Parse KML MultiGeometry containing mixed or homogeneous geometries."""
        geoms: list[BaseGeometry] = []

        for child in multi_elem:
            tag = self._strip_namespace(child.tag)
            if tag == "Polygon":
                p = self._parse_polygon(child)
                if p is not None:
                    geoms.append(p)
            elif tag == "LineString":
                ln = self._parse_linestring(child)
                if ln is not None:
                    geoms.append(ln)
            elif tag == "Point":
                pt = self._parse_point(child)
                if pt is not None:
                    geoms.append(pt)

        if not geoms:
            return None

        # Determine if geometries are homogeneous
        if all(isinstance(g, Polygon) for g in geoms):
            return MultiPolygon(geoms)  # type: ignore[arg-type]
        if all(isinstance(g, LineString) for g in geoms):
            return MultiLineString(geoms)  # type: ignore[arg-type]
        if all(isinstance(g, Point) for g in geoms):
            return MultiPoint(geoms)  # type: ignore[arg-type]

        return GeometryCollection(geoms)

    def _parse_coordinates(self, parent_elem: Element) -> list[tuple[float, float]]:
        """Extract and parse coordinate pairs from a <coordinates> tag.

        KML coordinate tuples are formatted as 'lon,lat[,alt]'.
        """
        coord_elem = self._find_first_tag(parent_elem, "coordinates")
        if coord_elem is None or not coord_elem.text:
            return []

        coords_text = coord_elem.text.strip()
        tuples = re.split(r"[\s\n\r\t]+", coords_text)
        parsed_coords: list[tuple[float, float]] = []

        for t in tuples:
            if not t:
                continue
            parts = t.split(",")
            if len(parts) >= 2:
                try:
                    lon = float(parts[0].strip())
                    lat = float(parts[1].strip())
                    parsed_coords.append((lon, lat))
                except ValueError:
                    continue

        return parsed_coords

    def _extract_placemark_properties(self, placemark: Element) -> dict[str, Any]:
        """Extract name, description, and ExtendedData from a Placemark."""
        props: dict[str, Any] = {}

        name_elem = self._find_first_tag(placemark, "name")
        if name_elem is not None and name_elem.text:
            props["name"] = name_elem.text.strip()

        desc_elem = self._find_first_tag(placemark, "description")
        if desc_elem is not None and desc_elem.text:
            props["description"] = desc_elem.text.strip()

        # Parse ExtendedData
        extended_elem = self._find_first_tag(placemark, "ExtendedData")
        if extended_elem is not None:
            # Data tags: <Data name="key"><value>val</value></Data>
            for data_tag in self._find_all_tags(extended_elem, "Data"):
                name = data_tag.get("name")
                if name:
                    val_elem = self._find_first_tag(data_tag, "value")
                    props[name] = (
                        val_elem.text.strip() if val_elem is not None and val_elem.text else ""
                    )

            # SimpleData tags: <SimpleData name="key">val</SimpleData>
            for sdata_tag in self._find_all_tags(extended_elem, "SimpleData"):
                name = sdata_tag.get("name")
                if name and sdata_tag.text:
                    props[name] = sdata_tag.text.strip()

        return props

    @staticmethod
    def _strip_namespace(tag: str) -> str:
        """Strip XML namespace prefix from tag name."""
        return tag.split("}")[-1] if "}" in tag else tag

    def _find_first_tag(self, root: Element, target_tag: str) -> Element | None:
        """Find the first descendant element matching target_tag regardless of namespace."""
        for elem in root.iter():
            if self._strip_namespace(elem.tag) == target_tag:
                return elem
        return None

    def _find_all_tags(self, root: Element, target_tag: str) -> list[Element]:
        """Find all direct or nested elements matching target_tag regardless of namespace."""
        return [elem for elem in root.iter() if self._strip_namespace(elem.tag) == target_tag]
