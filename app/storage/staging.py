"""Staging area and temporary workspace lifecycle manager."""

import logging
import shutil
import uuid
from pathlib import Path
from types import TracebackType

from app.core.config import get_settings
from app.core.exceptions import StorageError
from app.storage.sanitizer import sanitize_filename, validate_file_size
from app.storage.zip_handler import inspect_and_extract_zip

logger = logging.getLogger(__name__)


class StagingArea:
    """Manages an isolated temporary staging directory for an upload operation."""

    def __init__(self, operation_id: str | None = None, auto_cleanup: bool = True) -> None:
        self.operation_id = operation_id or str(uuid.uuid4())
        self.auto_cleanup = auto_cleanup

        settings = get_settings()
        self.base_dir = Path(settings.UPLOAD_DIR).resolve()
        self.staging_dir = self.base_dir / self.operation_id
        self.input_dir = self.staging_dir / "input"
        self.extracted_dir = self.staging_dir / "extracted"

        self._create_directories()

    def _create_directories(self) -> None:
        """Create isolated staging, input, and extracted directory structure."""
        try:
            self.input_dir.mkdir(parents=True, exist_ok=True)
            self.extracted_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            raise StorageError(
                f"Failed to create staging directory at '{self.staging_dir}': {err}",
                details={"staging_dir": str(self.staging_dir), "error": str(err)},
            ) from err

    def save_upload(self, raw_bytes: bytes, filename: str) -> Path:
        """Atomically write uploaded bytes to input directory using sanitized filename.

        Args:
            raw_bytes: Binary file payload.
            filename: Raw original filename.

        Returns:
            Path to the saved input file.

        Raises:
            FileValidationError: If size or filename is invalid.
            StorageError: If disk write fails.
        """
        validate_file_size(len(raw_bytes))
        safe_name = sanitize_filename(filename)
        final_path = self.input_dir / safe_name
        temp_path = self.input_dir / f"{safe_name}.part.{uuid.uuid4().hex[:8]}"

        try:
            with open(temp_path, "wb") as f:
                f.write(raw_bytes)
                f.flush()

            # Atomic rename into final staging path
            temp_path.replace(final_path)
            return final_path

        except OSError as err:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise StorageError(
                f"Failed to write uploaded file to staging: {err}",
                details={"final_path": str(final_path), "error": str(err)},
            ) from err

    def extract_archive(self, archive_path: Path) -> list[Path]:
        """Safely extract ZIP archive into the extracted directory.

        Args:
            archive_path: Path to .zip file in input directory.

        Returns:
            List of extracted file Path objects.
        """
        return inspect_and_extract_zip(
            zip_path=archive_path,
            extract_to=self.extracted_dir,
        )

    def cleanup(self) -> None:
        """Recursively remove staging directory and all contained files."""
        if self.staging_dir.exists():
            try:
                shutil.rmtree(self.staging_dir, ignore_errors=True)
                logger.debug("Cleaned up staging directory for operation %s", self.operation_id)
            except Exception as err:
                logger.warning("Failed to remove staging directory %s: %s", self.staging_dir, err)

    def __enter__(self) -> "StagingArea":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.auto_cleanup:
            self.cleanup()
