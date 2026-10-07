"""Domain and infrastructure exception hierarchy."""

from typing import Any


class AppError(Exception):
    """Base class for all application-level domain and infrastructure exceptions."""

    def __init__(
        self, message: str, code: str = "APP_ERROR", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class FileValidationError(AppError):
    """Raised when an uploaded file fails format, type, or integrity validation."""

    def __init__(
        self,
        message: str,
        code: str = "FILE_VALIDATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class UnsupportedFileTypeError(FileValidationError):
    """Raised when file extension or format is unsupported."""

    def __init__(
        self, message: str = "Unsupported file type", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="UNSUPPORTED_FILE_TYPE", details=details)


class FileSizeLimitExceededError(FileValidationError):
    """Raised when upload size exceeds maximum allowed bytes."""

    def __init__(
        self, message: str = "File size exceeds limit", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="FILE_SIZE_LIMIT_EXCEEDED", details=details)


class InvalidFilenameError(FileValidationError):
    """Raised when uploaded filename cannot be sanitized or contains dangerous patterns."""

    def __init__(
        self, message: str = "Invalid or dangerous filename", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="INVALID_FILENAME", details=details)


class ArchiveSecurityError(AppError):
    """Base class for archive security and extraction failures."""

    def __init__(
        self,
        message: str,
        code: str = "ARCHIVE_SECURITY_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class ZipSlipError(ArchiveSecurityError):
    """Raised when a ZIP archive entry attempts path traversal outside target directory."""

    def __init__(
        self,
        message: str = "Path traversal detected in archive entry",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ZIP_SLIP_DETECTED", details=details)


class ZipBombError(ArchiveSecurityError):
    """Raised when uncompressed archive size or compression ratio exceeds safety threshold."""

    def __init__(
        self,
        message: str = "Suspicious compression ratio or decompressed size",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ZIP_BOMB_DETECTED", details=details)


class ArchiveLimitExceededError(ArchiveSecurityError):
    """Raised when archive entry count exceeds maximum allowed."""

    def __init__(
        self,
        message: str = "Archive contains too many entries",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ARCHIVE_LIMIT_EXCEEDED", details=details)


class CorruptArchiveError(ArchiveSecurityError):
    """Raised when archive is corrupted or not a valid ZIP file."""

    def __init__(
        self,
        message: str = "Archive is corrupted or unreadable",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="CORRUPT_ARCHIVE", details=details)


class InvalidShapefileArchiveError(FileValidationError):
    """Raised when Shapefile archive is missing mandatory components or has ambiguity."""

    def __init__(
        self,
        message: str = "Invalid Shapefile archive structure",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="INVALID_SHAPEFILE_ARCHIVE", details=details)


class XMLSecurityError(AppError):
    """Raised when XML/KML content violates security constraints (XXE, entity expansion)."""

    def __init__(
        self,
        message: str = "XML security violation detected",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="XML_SECURITY_ERROR", details=details)


class StorageError(AppError):
    """Raised when filesystem staging, storage, or cleanup encounters an error."""

    def __init__(
        self, message: str = "Storage operation failed", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="STORAGE_ERROR", details=details)


class GeospatialError(AppError):
    """Base class for all geospatial processing exceptions."""

    def __init__(
        self,
        message: str,
        code: str = "GEOSPATIAL_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class GeospatialParseError(GeospatialError):
    """Raised when a vector layer or dataset cannot be parsed."""

    def __init__(
        self,
        message: str = "Failed to parse geospatial data",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="GEOSPATIAL_PARSE_ERROR", details=details)


class UnsupportedGeometryError(GeospatialError):
    """Raised when an unsupported geometry type is encountered."""

    def __init__(
        self, message: str = "Unsupported geometry type", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="UNSUPPORTED_GEOMETRY", details=details)


class InvalidGeometryError(GeospatialError):
    """Raised when a geometry is topologically corrupt or cannot be constructed."""

    def __init__(
        self, message: str = "Invalid geometry structure", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="INVALID_GEOMETRY", details=details)


class CRSError(GeospatialError):
    """Base class for Coordinate Reference System exceptions."""

    def __init__(
        self, message: str, code: str = "CRS_ERROR", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code=code, details=details)


class MissingCRSError(CRSError):
    """Raised when source CRS metadata is missing or unknown."""

    def __init__(
        self,
        message: str = "Source CRS is missing or unknown",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="MISSING_CRS", details=details)


class InvalidCRSError(CRSError):
    """Raised when source or target CRS string/WKT cannot be parsed or validated."""

    def __init__(
        self,
        message: str = "Invalid or unparseable CRS definition",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="INVALID_CRS", details=details)


class CRSResolutionError(CRSError):
    """Raised when an appropriate projected calculation CRS cannot be determined."""

    def __init__(
        self,
        message: str = "Unable to determine calculation CRS",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="CRS_RESOLUTION_ERROR", details=details)


class CRSTransformationError(CRSError):
    """Raised when coordinate reprojection from source to target CRS fails."""

    def __init__(
        self,
        message: str = "Coordinate reprojection failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="CRS_TRANSFORMATION_ERROR", details=details)
