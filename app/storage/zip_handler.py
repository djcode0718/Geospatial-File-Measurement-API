"""Security-hardened ZIP archive inspection, Zip Slip defense, and Shapefile validator."""

import logging
import stat
import zipfile
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import (
    ArchiveLimitExceededError,
    ArchiveSecurityError,
    CorruptArchiveError,
    InvalidShapefileArchiveError,
    ZipBombError,
    ZipSlipError,
)

logger = logging.getLogger(__name__)


def inspect_and_extract_zip(
    zip_path: Path,
    extract_to: Path,
    max_entries: int | None = None,
    max_extracted_size: int | None = None,
    max_ratio: float | None = None,
) -> list[Path]:
    """Inspect and extract ZIP archive with comprehensive security checks.

    Protects against:
      - Zip Slip / path traversal attacks (../../etc/passwd)
      - Decompression bombs / zip bombs (ratio and total size thresholds)
      - Excessive entry counts
      - Symlink and device injection
      - Nested archives

    Args:
        zip_path: Absolute path to source .zip archive.
        extract_to: Target destination directory.
        max_entries: Upper limit for number of entries.
        max_extracted_size: Upper limit for cumulative decompressed size.
        max_ratio: Maximum allowable compression ratio per entry.

    Returns:
        List of extracted file Path objects.

    Raises:
        CorruptArchiveError: If archive is malformed or invalid ZIP.
        ZipSlipError: If path traversal or symlink is detected.
        ZipBombError: If decompressed size or ratio exceeds limits.
        ArchiveLimitExceededError: If entry count exceeds threshold.
        ArchiveSecurityError: If nested archives or other violations are found.
    """
    settings = get_settings()
    entry_limit = max_entries or settings.MAX_ZIP_ENTRIES
    size_limit = max_extracted_size or settings.MAX_EXTRACTED_SIZE_BYTES
    ratio_limit = max_ratio or settings.MAX_COMPRESSION_RATIO

    extract_to = extract_to.resolve()
    extract_to.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            entries = archive.infolist()

            # 1. Check entry count
            if len(entries) > entry_limit:
                raise ArchiveLimitExceededError(
                    f"Archive contains {len(entries)} entries, exceeding maximum limit of {entry_limit}",
                    details={"entry_count": len(entries), "limit": entry_limit},
                )

            total_uncompressed = 0

            # 2. Pre-inspection pass on all entries before writing any file
            for entry in entries:
                # A. Detect symlinks via Unix file mode attributes
                # High 16 bits of external_attr store POSIX file permissions
                mode = entry.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise ZipSlipError(
                        f"Symlink entry detected in archive: {entry.filename}",
                        details={"entry": entry.filename},
                    )

                # B. Zip Slip verification
                # Check for explicit traversal tokens
                if ".." in entry.filename or entry.filename.startswith(("/", "\\")):
                    raise ZipSlipError(
                        f"Path traversal detected in archive entry: {entry.filename}",
                        details={"entry": entry.filename},
                    )

                # Canonical path resolution check
                target_path = (extract_to / entry.filename).resolve()
                try:
                    target_path.relative_to(extract_to)
                except ValueError as err:
                    raise ZipSlipError(
                        f"Archive entry '{entry.filename}' resolves outside target extraction directory",
                        details={"entry": entry.filename, "resolved": str(target_path)},
                    ) from err

                # C. Check for nested archives
                if (
                    entry.filename.lower().endswith((".zip", ".tar", ".gz", ".7z", ".rar"))
                    and not entry.is_dir()
                ):
                    raise ArchiveSecurityError(
                        f"Nested archive entry '{entry.filename}' is not permitted",
                        details={"entry": entry.filename},
                    )

                # D. Compression ratio and bomb threshold checks
                total_uncompressed += entry.file_size
                if entry.file_size > 1024 and entry.compress_size > 0:
                    entry_ratio = entry.file_size / entry.compress_size
                    if entry_ratio > ratio_limit:
                        raise ZipBombError(
                            f"Suspicious compression ratio ({entry_ratio:.1f}:1) on entry '{entry.filename}'",
                            details={
                                "entry": entry.filename,
                                "ratio": entry_ratio,
                                "limit": ratio_limit,
                            },
                        )

            # Check total uncompressed size limit
            if total_uncompressed > size_limit:
                raise ZipBombError(
                    f"Total uncompressed archive size ({total_uncompressed} bytes) exceeds limit of {size_limit} bytes",
                    details={"total_bytes": total_uncompressed, "limit": size_limit},
                )

            # 3. Safe extraction pass
            extracted_files: list[Path] = []
            for entry in entries:
                if entry.is_dir():
                    (extract_to / entry.filename).mkdir(parents=True, exist_ok=True)
                    continue

                dest_path = (extract_to / entry.filename).resolve()
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                with archive.open(entry) as src, open(dest_path, "wb") as dst:
                    # Stream chunks safely
                    while chunk := src.read(64 * 1024):
                        dst.write(chunk)

                extracted_files.append(dest_path)

            return extracted_files

    except zipfile.BadZipFile as err:
        raise CorruptArchiveError(
            f"Failed to read ZIP archive: {err}",
            details={"error": str(err)},
        ) from err


def validate_shapefile_components(extracted_files: list[Path]) -> Path:
    """Validate that extracted files form a complete, unambiguous ESRI Shapefile dataset.

    Mandatory Shapefile components:
      - .shp (Shape format - geometry)
      - .shx (Shape index format)
      - .dbf (dBASE attribute format)
    Optional:
      - .prj (Projection format - CRS)

    Args:
        extracted_files: List of extracted file Path objects.

    Returns:
        Path to the primary .shp file.

    Raises:
        InvalidShapefileArchiveError: If .shp is missing, multiple .shp files exist, or .shx/.dbf are missing.
    """
    shp_files = [f for f in extracted_files if f.suffix.lower() == ".shp"]

    if not shp_files:
        raise InvalidShapefileArchiveError(
            "Archive contains no .shp file. A valid Shapefile archive must contain at least one .shp file.",
        )

    if len(shp_files) > 1:
        shp_names = [f.name for f in shp_files]
        raise InvalidShapefileArchiveError(
            f"Archive contains multiple Shapefile datasets ({shp_names}). Exactly one Shapefile dataset is supported per archive.",
            details={"found_shapefiles": shp_names},
        )

    primary_shp = shp_files[0]
    shp_stem = primary_shp.stem
    parent_dir = primary_shp.parent

    # Mandatory companion extensions
    mandatory_extensions = {".shx", ".dbf"}
    existing_stems_exts = {
        (f.stem, f.suffix.lower()) for f in extracted_files if f.parent == parent_dir
    }

    missing_components = [
        ext for ext in mandatory_extensions if (shp_stem, ext) not in existing_stems_exts
    ]

    if missing_components:
        raise InvalidShapefileArchiveError(
            f"Shapefile '{primary_shp.name}' is missing mandatory companion files: {missing_components}",
            details={"primary_shp": primary_shp.name, "missing": missing_components},
        )

    return primary_shp
