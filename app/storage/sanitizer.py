"""Filename sanitization and file upload validation utilities."""

import os
import re
import unicodedata
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import (
    FileSizeLimitExceededError,
    FileValidationError,
    InvalidFilenameError,
    UnsupportedFileTypeError,
)

# Regex pattern allowing alphanumeric, dashes, underscores, and dots
SAFE_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]")
MAX_FILENAME_LENGTH = 200


def sanitize_filename(filename: str) -> str:
    """Sanitize untrusted filename stripping directory traversals, control chars, and illegal symbols.

    Args:
        filename: Raw user-provided filename string.

    Returns:
        Safe, sanitized basename string.

    Raises:
        InvalidFilenameError: If filename is empty or results in an empty/invalid name.
    """
    if not filename or not isinstance(filename, str):
        raise InvalidFilenameError("Filename cannot be empty")

    # 1. Strip null bytes and control characters
    cleaned = "".join(ch for ch in filename if ord(ch) >= 32 and ch != "\x7f")

    # 2. Normalize unicode (NFKD form) and remove non-ASCII accents
    normalized = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")

    # 3. Handle Windows backslashes and Unix forward slashes to isolate the basename
    # Replace backslashes with forward slashes, then extract basename
    base = os.path.basename(normalized.replace("\\", "/"))

    # 4. Remove leading/trailing dots and whitespace (prevents hidden files like .env or ..)
    base = base.strip(". \t\r\n")

    # 5. Substitute any remaining illegal/unsafe characters with underscore
    safe_name = SAFE_FILENAME_CHARS.sub("_", base)

    # 6. Collapse multiple consecutive underscores
    safe_name = re.sub(r"_+", "_", safe_name)

    # 7. Ensure valid extension and length limits
    if not safe_name:
        raise InvalidFilenameError("Filename contains only invalid or illegal characters")

    path_obj = Path(safe_name)
    stem = path_obj.stem[:MAX_FILENAME_LENGTH]
    suffix = path_obj.suffix.lower()

    if not stem:
        raise InvalidFilenameError("Filename stem cannot be empty")

    return f"{stem}{suffix}"


def validate_file_extension(filename: str, allowed_extensions: set[str] | None = None) -> str:
    """Validate file extension against permitted set.

    Args:
        filename: File path or name string.
        allowed_extensions: Optional set of allowed extensions (defaults to Settings.ALLOWED_EXTENSIONS).

    Returns:
        Normalized lowercase extension string (e.g., '.zip', '.kml').

    Raises:
        UnsupportedFileTypeError: If extension is missing or not allowed.
    """
    settings = get_settings()
    allowed = allowed_extensions or settings.ALLOWED_EXTENSIONS

    ext = Path(filename).suffix.lower()
    if not ext or ext not in allowed:
        raise UnsupportedFileTypeError(
            f"File extension '{ext}' is not supported. Allowed formats: {sorted(allowed)}",
            details={"extension": ext, "allowed": sorted(allowed)},
        )
    return ext


def validate_file_size(size_bytes: int, max_bytes: int | None = None) -> None:
    """Validate that file size is non-empty and within configured bounds.

    Args:
        size_bytes: Size in bytes.
        max_bytes: Optional upper limit in bytes (defaults to Settings.MAX_UPLOAD_SIZE_BYTES).

    Raises:
        FileValidationError: If file size is 0 or negative.
        FileSizeLimitExceededError: If file size exceeds maximum limit.
    """
    settings = get_settings()
    max_limit = max_bytes or settings.MAX_UPLOAD_SIZE_BYTES

    if size_bytes <= 0:
        raise FileValidationError(
            "Uploaded file is empty (0 bytes)",
            details={"size_bytes": size_bytes},
        )

    if size_bytes > max_limit:
        raise FileSizeLimitExceededError(
            f"File size ({size_bytes} bytes) exceeds maximum limit of {max_limit} bytes ({max_limit // (1024 * 1024)} MB)",
            details={"size_bytes": size_bytes, "max_bytes": max_limit},
        )
