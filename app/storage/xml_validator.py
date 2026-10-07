"""Safe XML and KML security validator defending against XXE and entity expansion."""

import logging

from defusedxml import DefusedXmlException, ElementTree

from app.core.exceptions import XMLSecurityError

logger = logging.getLogger(__name__)


def validate_kml_xml_safety(xml_content: bytes | str) -> None:
    """Validate XML/KML content safety using defusedxml.

    Defends against:
      - XML External Entity (XXE) attacks
      - Billion Laughs / Entity Expansion attacks
      - Quadratic blowup attacks
      - Malformed XML syntax

    Args:
        xml_content: Raw XML bytes or string content.

    Raises:
        XMLSecurityError: If XML violates security rules or contains invalid syntax.
    """
    if not xml_content:
        raise XMLSecurityError("KML file is empty (0 bytes)")

    try:
        if isinstance(xml_content, str):
            xml_bytes = xml_content.encode("utf-8")
        else:
            xml_bytes = xml_content

        # defusedxml.ElementTree.fromstring parses XML safely with external entities disabled
        root = ElementTree.fromstring(xml_bytes)
        if root is None:
            raise XMLSecurityError("KML contains no root element")

    except DefusedXmlException as err:
        logger.warning("XML security violation detected: %s", err)
        raise XMLSecurityError(
            f"XML security violation: {err}",
            details={"error": str(err)},
        ) from err
    except ElementTree.ParseError as err:
        logger.warning("Malformed XML syntax in KML: %s", err)
        raise XMLSecurityError(
            f"Malformed XML syntax: {err}",
            details={"error": str(err)},
        ) from err
    except Exception as err:
        logger.warning("Unexpected error during XML validation: %s", err)
        raise XMLSecurityError(
            f"Failed to parse XML safely: {err}",
            details={"error": str(err)},
        ) from err
