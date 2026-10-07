"""Application configuration settings using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application runtime settings loaded from environment variables or .env."""

    PROJECT_NAME: str = "Geospatial File Measurement API"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = (
        "Production-grade backend service for ingesting geospatial vector files, "
        "calculating metric measurements with accurate CRS transformations, and "
        "exposing RESTful inspection endpoints."
    )
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server binding
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Logging
    LOG_LEVEL: str = "INFO"

    # Uploads, Storage & Security Limits
    UPLOAD_DIR: str = "/tmp/geomeasure_staging"
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB
    MAX_ZIP_ENTRIES: int = 100
    MAX_EXTRACTED_SIZE_BYTES: int = 200 * 1024 * 1024  # 200 MB
    MAX_COMPRESSION_RATIO: float = 100.0  # 100:1 ratio
    ALLOWED_EXTENSIONS: set[str] = {".zip", ".kml"}

    # Database
    DATABASE_URL: str = "sqlite:///./geomeasure.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
