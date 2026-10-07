"""Abstract base vector reader interface."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from app.geospatial.models import DatasetMetadata, ParsedFeature


class BaseVectorReader(ABC):
    """Abstract interface for geospatial vector format readers."""

    @abstractmethod
    def read_features(self, file_path: Path) -> Iterator[ParsedFeature]:
        """Stream normalized ParsedFeature records from source file.

        Args:
            file_path: Path to validated dataset file.

        Yields:
            ParsedFeature instances.
        """
        pass

    @abstractmethod
    def get_source_crs(self, file_path: Path) -> str | None:
        """Inspect and return dataset-level CRS string without iterating all features.

        Args:
            file_path: Path to validated dataset file.

        Returns:
            Normalized CRS string (e.g. 'EPSG:4326') or None if absent.
        """
        pass

    def read_dataset(self, file_path: Path) -> tuple[list[ParsedFeature], DatasetMetadata]:
        """Read all features and compile aggregate dataset summary metadata.

        Args:
            file_path: Path to validated dataset file.

        Returns:
            Tuple of (list of ParsedFeature, DatasetMetadata).
        """
        features: list[ParsedFeature] = []
        geom_counts: dict[str, int] = {}
        detected_crs: str | None = self.get_source_crs(file_path)
        warnings: list[str] = []

        for feat in self.read_features(file_path):
            features.append(feat)
            geom_counts[feat.geometry_type] = geom_counts.get(feat.geometry_type, 0) + 1
            if detected_crs is None and feat.source_crs is not None:
                detected_crs = feat.source_crs
            if feat.warning_message:
                warnings.append(f"Feature {feat.feature_index}: {feat.warning_message}")

        metadata = DatasetMetadata(
            total_features=len(features),
            source_crs=detected_crs,
            geometry_counts=geom_counts,
            warnings=warnings,
        )
        return features, metadata
