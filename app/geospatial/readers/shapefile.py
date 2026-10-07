"""Shapefile vector format reader using Fiona and Shapely."""

import logging
from collections.abc import Iterator
from pathlib import Path

import fiona
import pyproj
from shapely.geometry import shape

from app.core.exceptions import GeospatialParseError
from app.geospatial.models import ParsedFeature
from app.geospatial.readers.base import BaseVectorReader

logger = logging.getLogger(__name__)


class ShapefileReader(BaseVectorReader):
    """Reader for ESRI Shapefiles (.shp) extracted from validated staging areas."""

    def get_source_crs(self, file_path: Path) -> str | None:
        """Inspect Shapefile header/metadata and return detected CRS string or None."""
        if not file_path.exists():
            return None
        try:
            with fiona.open(str(file_path), "r") as src:
                return self._extract_crs_string(src)
        except Exception:
            return None

    def read_features(self, file_path: Path) -> Iterator[ParsedFeature]:
        """Read and yield normalized ParsedFeature objects from a Shapefile.

        Args:
            file_path: Path to validated .shp file.

        Yields:
            ParsedFeature instances.

        Raises:
            GeospatialParseError: If file cannot be opened or is fundamentally corrupt.
        """
        if not file_path.exists():
            raise GeospatialParseError(f"Shapefile does not exist at '{file_path}'")

        try:
            with fiona.open(str(file_path), "r") as src:
                source_crs = self._extract_crs_string(src)

                for idx, feat in enumerate(src):
                    try:
                        feature_id = feat.id if hasattr(feat, "id") else idx
                        props = dict(feat.properties) if feat.properties else {}

                        if feat.geometry is None:
                            yield ParsedFeature(
                                feature_index=idx,
                                feature_id=feature_id,
                                geometry=None,
                                geometry_type="None",
                                properties=props,
                                source_crs=source_crs,
                                is_valid_geometry=False,
                                warning_message="Feature contains null or empty geometry",
                            )
                            continue

                        # Convert GeoJSON-like dict to Shapely geometry
                        geom = shape(feat.geometry)
                        geom_type = geom.geom_type
                        is_valid = geom.is_valid
                        warning = (
                            None
                            if is_valid
                            else "Geometry topology is invalid (e.g., self-intersection)"
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
                            "Error parsing feature index %d in %s: %s", idx, file_path.name, err
                        )
                        yield ParsedFeature(
                            feature_index=idx,
                            feature_id=idx,
                            geometry=None,
                            geometry_type="Invalid",
                            properties=dict(feat.properties)
                            if hasattr(feat, "properties") and feat.properties
                            else {},
                            source_crs=source_crs,
                            is_valid_geometry=False,
                            warning_message=f"Failed to parse feature geometry: {err}",
                        )

        except Exception as err:
            if isinstance(err, GeospatialParseError):
                raise
            raise GeospatialParseError(
                f"Failed to read Shapefile at '{file_path.name}': {err}",
                details={"file": str(file_path), "error": str(err)},
            ) from err

    @staticmethod
    def _extract_crs_string(src: fiona.Collection) -> str | None:
        """Extract standardized EPSG or WKT string from Fiona collection metadata."""
        if not src.crs:
            return None

        # Try resolving to standard EPSG code using PyProj
        try:
            if src.crs_wkt:
                crs = pyproj.CRS.from_user_input(src.crs_wkt)
                epsg = crs.to_epsg()
                if epsg:
                    return f"EPSG:{epsg}"
                return crs.to_string()
            elif isinstance(src.crs, dict):
                crs = pyproj.CRS.from_user_input(src.crs)
                epsg = crs.to_epsg()
                if epsg:
                    return f"EPSG:{epsg}"
                return crs.to_string()
        except Exception:
            # Fallback to string representation if PyProj CRS resolution fails
            return str(src.crs)

        return None
