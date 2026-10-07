"""Security and storage tests verifying Zip Slip defense, bomb detection, XML validation, and staging."""

import io
import zipfile
from pathlib import Path

import pytest

from app.core.exceptions import (
    ArchiveLimitExceededError,
    ArchiveSecurityError,
    CorruptArchiveError,
    FileSizeLimitExceededError,
    FileValidationError,
    InvalidFilenameError,
    InvalidShapefileArchiveError,
    UnsupportedFileTypeError,
    XMLSecurityError,
    ZipBombError,
    ZipSlipError,
)
from app.storage.sanitizer import sanitize_filename, validate_file_extension, validate_file_size
from app.storage.staging import StagingArea
from app.storage.xml_validator import validate_kml_xml_safety
from app.storage.zip_handler import inspect_and_extract_zip, validate_shapefile_components

# ==============================================================================
# 1. Filename Sanitization & Validation Tests
# ==============================================================================


def test_sanitize_normal_filename() -> None:
    """Verify normal alphanumeric filenames remain clean."""
    assert sanitize_filename("bangalore_survey.zip") == "bangalore_survey.zip"
    assert sanitize_filename("road-network-2026.kml") == "road-network-2026.kml"


def test_sanitize_path_traversal() -> None:
    """Verify path traversal sequences (../, ../..) are stripped to the basename."""
    assert sanitize_filename("../../etc/passwd.zip") == "passwd.zip"
    assert sanitize_filename("foo/bar/../../test.kml") == "test.kml"


def test_sanitize_absolute_and_windows_paths() -> None:
    """Verify absolute Unix paths and Windows drive paths are stripped to safe basenames."""
    assert sanitize_filename("/var/log/data.shp.zip") == "data.shp.zip"
    assert sanitize_filename(r"C:\Users\Admin\Desktop\survey.kml") == "survey.kml"


def test_sanitize_control_chars_and_null_bytes() -> None:
    """Verify control characters and null bytes are removed."""
    assert sanitize_filename("my\x00file\x1fname.kml") == "myfilename.kml"


def test_sanitize_unicode_characters() -> None:
    """Verify Unicode characters are normalized to safe ASCII representation."""
    result = sanitize_filename("münchen_pläne.kml")
    assert result == "munchen_plane.kml"


def test_sanitize_invalid_empty_filename() -> None:
    """Verify empty or completely illegal filenames raise InvalidFilenameError."""
    with pytest.raises(InvalidFilenameError):
        sanitize_filename("")

    with pytest.raises(InvalidFilenameError):
        sanitize_filename("...///\\\\")


def test_validate_file_extension() -> None:
    """Verify supported extensions pass and unsupported extensions fail."""
    assert validate_file_extension("survey.zip") == ".zip"
    assert validate_file_extension("data.KML") == ".kml"

    with pytest.raises(UnsupportedFileTypeError):
        validate_file_extension("executable.exe")

    with pytest.raises(UnsupportedFileTypeError):
        validate_file_extension("document.pdf")


def test_validate_file_size() -> None:
    """Verify file size bounds validation."""
    validate_file_size(1024)  # 1 KB valid

    with pytest.raises(FileValidationError):
        validate_file_size(0)  # 0 bytes invalid

    with pytest.raises(FileSizeLimitExceededError):
        validate_file_size(60 * 1024 * 1024, max_bytes=50 * 1024 * 1024)


# ==============================================================================
# 2. ZIP Archive Security Tests (Zip Slip, Bomb, Symlinks, Limits)
# ==============================================================================


def test_extract_valid_zip(tmp_path: Path) -> None:
    """Verify a valid ZIP archive extracts cleanly within destination directory."""
    zip_path = tmp_path / "valid.zip"
    extract_dir = tmp_path / "extracted"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("test.txt", "sample content")
        zf.writestr("folder/sub.txt", "nested content")

    extracted = inspect_and_extract_zip(zip_path, extract_dir)
    assert len(extracted) == 2
    for p in extracted:
        assert p.is_relative_to(extract_dir)
        assert p.exists()


def test_zip_slip_path_traversal_detection(tmp_path: Path) -> None:
    """Verify archive entries targeting parent directories (Zip Slip) are blocked."""
    zip_path = tmp_path / "zip_slip.zip"
    extract_dir = tmp_path / "extracted"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../evil.txt", "malicious payload")

    with pytest.raises(ZipSlipError):
        inspect_and_extract_zip(zip_path, extract_dir)


def test_zip_slip_absolute_path_detection(tmp_path: Path) -> None:
    """Verify archive entries starting with absolute root slashes are blocked."""
    zip_path = tmp_path / "absolute_entry.zip"
    extract_dir = tmp_path / "extracted"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("/etc/passwd", "malicious payload")

    with pytest.raises(ZipSlipError):
        inspect_and_extract_zip(zip_path, extract_dir)


def test_zip_bomb_compression_ratio_detection(tmp_path: Path) -> None:
    """Verify archive entries with extreme compression ratios are rejected."""
    zip_path = tmp_path / "zip_bomb.zip"
    extract_dir = tmp_path / "extracted"

    # 1 MB of repeated zeros compresses to a few hundred bytes (>1000:1 ratio)
    bomb_payload = b"0" * (1024 * 1024)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.txt", bomb_payload)

    with pytest.raises(ZipBombError):
        inspect_and_extract_zip(zip_path, extract_dir, max_ratio=20.0)


def test_archive_entry_count_limit(tmp_path: Path) -> None:
    """Verify archives exceeding maximum allowed entry count are rejected."""
    zip_path = tmp_path / "many_entries.zip"
    extract_dir = tmp_path / "extracted"

    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(15):
            zf.writestr(f"file_{i}.txt", "data")

    with pytest.raises(ArchiveLimitExceededError):
        inspect_and_extract_zip(zip_path, extract_dir, max_entries=10)


def test_nested_archive_detection(tmp_path: Path) -> None:
    """Verify nested .zip files within an archive are rejected."""
    zip_path = tmp_path / "outer.zip"
    extract_dir = tmp_path / "extracted"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("nested.zip", b"fake zip content")

    with pytest.raises(ArchiveSecurityError):
        inspect_and_extract_zip(zip_path, extract_dir)


def test_corrupt_zip_file(tmp_path: Path) -> None:
    """Verify corrupted / truncated ZIP files raise CorruptArchiveError."""
    corrupt_path = tmp_path / "corrupt.zip"
    corrupt_path.write_bytes(b"PK\x03\x04truncated_garbage_bytes")

    with pytest.raises(CorruptArchiveError):
        inspect_and_extract_zip(corrupt_path, tmp_path / "extracted")


# ==============================================================================
# 3. Shapefile Component Structure Tests
# ==============================================================================


def test_validate_complete_shapefile(tmp_path: Path) -> None:
    """Verify complete Shapefile (.shp, .shx, .dbf, optional .prj) validates successfully."""
    files = [
        tmp_path / "parcels.shp",
        tmp_path / "parcels.shx",
        tmp_path / "parcels.dbf",
        tmp_path / "parcels.prj",
    ]
    for f in files:
        f.touch()

    primary_shp = validate_shapefile_components(files)
    assert primary_shp == tmp_path / "parcels.shp"


def test_validate_shapefile_missing_shp(tmp_path: Path) -> None:
    """Verify archive with missing .shp file raises InvalidShapefileArchiveError."""
    files = [tmp_path / "data.dbf", tmp_path / "data.shx"]
    with pytest.raises(InvalidShapefileArchiveError):
        validate_shapefile_components(files)


def test_validate_shapefile_multiple_shp(tmp_path: Path) -> None:
    """Verify archive with multiple .shp files raises InvalidShapefileArchiveError."""
    files = [
        tmp_path / "layer1.shp",
        tmp_path / "layer1.shx",
        tmp_path / "layer1.dbf",
        tmp_path / "layer2.shp",
        tmp_path / "layer2.shx",
        tmp_path / "layer2.dbf",
    ]
    with pytest.raises(InvalidShapefileArchiveError):
        validate_shapefile_components(files)


def test_validate_shapefile_missing_companions(tmp_path: Path) -> None:
    """Verify archive missing .dbf or .shx companion files is rejected."""
    files = [tmp_path / "roads.shp", tmp_path / "roads.shx"]  # Missing .dbf
    with pytest.raises(InvalidShapefileArchiveError):
        validate_shapefile_components(files)


# ==============================================================================
# 4. XML / KML Security Tests (XXE, Entity Expansion, Malformed)
# ==============================================================================


def test_kml_valid_xml() -> None:
    """Verify clean, valid KML XML content passes validation."""
    valid_kml = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
        <Placemark>
            <name>Test Point</name>
            <Point><coordinates>77.59,12.97,0</coordinates></Point>
        </Placemark>
    </kml>"""
    validate_kml_xml_safety(valid_kml)


def test_kml_empty_xml() -> None:
    """Verify empty XML content raises XMLSecurityError."""
    with pytest.raises(XMLSecurityError):
        validate_kml_xml_safety(b"")


def test_kml_malformed_xml() -> None:
    """Verify malformed XML syntax raises XMLSecurityError."""
    with pytest.raises(XMLSecurityError):
        validate_kml_xml_safety(b"<kml><Placemark><unclosed></kml>")


def test_kml_xxe_external_entity_defense() -> None:
    """Verify XML External Entity (XXE) injection is blocked by defusedxml."""
    xxe_payload = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE foo [
        <!ELEMENT foo ANY >
        <!ENTITY xxe SYSTEM "file:///etc/passwd" >]>
    <kml>
        <Placemark><name>&xxe;</name></Placemark>
    </kml>"""
    with pytest.raises(XMLSecurityError):
        validate_kml_xml_safety(xxe_payload)


def test_kml_entity_expansion_billion_laughs_defense() -> None:
    """Verify exponential entity expansion (Billion Laughs) is blocked."""
    bomb_xml = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
     <!ENTITY lol "lol">
     <!ELEMENT lolz (#PCDATA)>
     <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
     <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
    ]>
    <kml><Placemark><name>&lol2;</name></Placemark></kml>"""
    with pytest.raises(XMLSecurityError):
        validate_kml_xml_safety(bomb_xml)


# ==============================================================================
# 5. StagingArea Temporary Workspace Lifecycle Tests
# ==============================================================================


def test_staging_area_lifecycle() -> None:
    """Verify StagingArea creates isolated workspace, saves uploads, and cleans up on exit."""
    with StagingArea() as staging:
        assert staging.staging_dir.exists()
        assert staging.input_dir.exists()
        assert staging.extracted_dir.exists()

        # Save an upload safely
        upload_path = staging.save_upload(b"test file content", "sample.kml")
        assert upload_path.exists()
        assert upload_path.name == "sample.kml"
        assert upload_path.read_bytes() == b"test file content"

        staging_dir_ref = staging.staging_dir

    # Verify auto-cleanup removed the directory after context exit
    assert not staging_dir_ref.exists()


def test_staging_area_archive_extraction() -> None:
    """Verify StagingArea extracts ZIP into isolated extracted/ subfolder."""
    # Create an in-memory zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("test.shp", "shp data")
        zf.writestr("test.shx", "shx data")
        zf.writestr("test.dbf", "dbf data")
    zip_bytes = zip_buffer.getvalue()

    with StagingArea() as staging:
        archive_path = staging.save_upload(zip_bytes, "survey.zip")
        extracted_files = staging.extract_archive(archive_path)

        assert len(extracted_files) == 3
        for f in extracted_files:
            assert f.parent == staging.extracted_dir
            assert f.exists()
