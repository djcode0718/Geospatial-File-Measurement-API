"""CRS validation and normalization utilities using PyProj."""

import pyproj

from app.core.exceptions import InvalidCRSError, MissingCRSError


def validate_crs(crs_input: str | None) -> pyproj.CRS:
    """Validate and parse a CRS definition using PyProj.

    Args:
        crs_input: CRS identifier string (e.g. 'EPSG:4326', WKT, or PROJ string).

    Returns:
        Validated pyproj.CRS object.

    Raises:
        MissingCRSError: If crs_input is None or empty.
        InvalidCRSError: If crs_input is unparseable or unrecognized by PROJ.
    """
    if not crs_input or not isinstance(crs_input, str) or not crs_input.strip():
        raise MissingCRSError("Source CRS is missing or undefined")

    cleaned = crs_input.strip()
    try:
        return pyproj.CRS.from_user_input(cleaned)
    except Exception as err:
        raise InvalidCRSError(
            f"Failed to parse CRS '{cleaned}': {err}",
            details={"crs_input": cleaned, "error": str(err)},
        ) from err


def normalize_crs_string(crs_obj: pyproj.CRS) -> str:
    """Extract a standardized string identifier from a PyProj CRS object.

    Returns 'EPSG:XXXX' where available, otherwise returns the official name or PROJ string.
    """
    epsg_code = crs_obj.to_epsg()
    if epsg_code:
        return f"EPSG:{epsg_code}"

    # Try authority name and code
    auth_name = crs_obj.to_authority()
    if auth_name:
        return f"{auth_name[0]}:{auth_name[1]}"

    return crs_obj.to_string()
