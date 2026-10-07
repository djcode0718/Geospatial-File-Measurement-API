"""Geospatial models, readers, CRS resolvers, and measurement engines."""

from app.geospatial.crs import (
    BoundingBox,
    CRSResolution,
    CRSResolver,
    CRSStrategy,
    GeometryTransformer,
    MeasurementPurpose,
    normalize_crs_string,
    transform_geometry,
    validate_crs,
)
from app.geospatial.models import DatasetMetadata, ParsedFeature
from app.geospatial.readers import BaseVectorReader, KMLReader, ShapefileReader

__all__ = [
    "ParsedFeature",
    "DatasetMetadata",
    "BaseVectorReader",
    "ShapefileReader",
    "KMLReader",
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
