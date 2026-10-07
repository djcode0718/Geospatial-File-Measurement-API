"""Coordinate Reference System resolution, validation, and geometry reprojection."""

from app.geospatial.crs.models import (
    BoundingBox,
    CRSResolution,
    CRSStrategy,
    MeasurementPurpose,
)
from app.geospatial.crs.resolver import CRSResolver
from app.geospatial.crs.transformer import GeometryTransformer, transform_geometry
from app.geospatial.crs.validator import normalize_crs_string, validate_crs

__all__ = [
    "BoundingBox",
    "CRSResolution",
    "CRSStrategy",
    "MeasurementPurpose",
    "CRSResolver",
    "GeometryTransformer",
    "transform_geometry",
    "validate_crs",
    "normalize_crs_string",
]
