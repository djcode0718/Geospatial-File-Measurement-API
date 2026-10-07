"""Coordinate transformation layer for Shapely geometries using PyProj."""

import logging
from functools import lru_cache

import pyproj
import shapely.ops
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import CRSTransformationError, InvalidCRSError, MissingCRSError
from app.geospatial.crs.validator import validate_crs

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def get_cached_pyproj_transformer(
    source_crs: str, target_crs: str
) -> tuple[pyproj.Transformer, bool]:
    """Retrieve or construct a cached PyProj coordinate transformer with axis-order normalization."""
    source_pyproj = validate_crs(source_crs)
    target_pyproj = validate_crs(target_crs)
    is_identity = (source_crs.strip().upper() == target_crs.strip().upper()) or (
        source_pyproj == target_pyproj
    )
    transformer = pyproj.Transformer.from_crs(
        source_pyproj,
        target_pyproj,
        always_xy=True,
    )
    return transformer, is_identity


class GeometryTransformer:
    """Reprojects Shapely geometries between Coordinate Reference Systems."""

    def __init__(self, source_crs: str, target_crs: str) -> None:
        """Initialize PyProj coordinate transformer with axis-order normalization.

        Args:
            source_crs: Source CRS identifier (e.g. 'EPSG:4326').
            target_crs: Target calculation CRS identifier (e.g. 'EPSG:32643').

        Raises:
            InvalidCRSError: If source or target CRS cannot be parsed.
            CRSTransformationError: If transformer cannot be constructed.
        """
        self.source_str = source_crs
        self.target_str = target_crs

        try:
            self._transformer, self._is_identity = get_cached_pyproj_transformer(
                source_crs, target_crs
            )
        except (InvalidCRSError, MissingCRSError):
            raise
        except Exception as err:
            raise CRSTransformationError(
                f"Failed to create coordinate transformer from '{source_crs}' to '{target_crs}': {err}",
                details={"source_crs": source_crs, "target_crs": target_crs, "error": str(err)},
            ) from err

    def transform(self, geometry: BaseGeometry | None) -> BaseGeometry | None:
        """Transform a Shapely geometry into the target coordinate system.

        Preserves geometry type, rings, and coordinates without mutating source geometry.

        Args:
            geometry: Shapely BaseGeometry instance or None.

        Returns:
            New transformed Shapely BaseGeometry, or None if input was None.

        Raises:
            CRSTransformationError: If coordinate reprojection fails.
        """
        if geometry is None:
            return None

        if self._is_identity:
            return geometry

        try:
            transformed = shapely.ops.transform(self._transformer.transform, geometry)
            return transformed
        except Exception as err:
            logger.warning(
                "Coordinate reprojection failed from %s to %s: %s",
                self.source_str,
                self.target_str,
                err,
            )
            raise CRSTransformationError(
                f"Failed to reproject geometry from '{self.source_str}' to '{self.target_str}': {err}",
                details={
                    "source_crs": self.source_str,
                    "target_crs": self.target_str,
                    "error": str(err),
                },
            ) from err


def transform_geometry(
    geometry: BaseGeometry | None,
    source_crs: str,
    target_crs: str,
) -> BaseGeometry | None:
    """Convenience helper to transform a single geometry between two CRS definitions."""
    transformer = GeometryTransformer(source_crs=source_crs, target_crs=target_crs)
    return transformer.transform(geometry)
