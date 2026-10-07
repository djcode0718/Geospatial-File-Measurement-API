"""Reproducible fixture generator creating synthetic geospatial datasets for benchmarking."""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import fiona
from shapely.geometry import LineString, Polygon, mapping

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def generate_polygon(index: int, base_lon: float = 77.5, base_lat: float = 12.9) -> Polygon:
    """Generate a valid polygon centered around given coordinates with slight offset."""
    offset_x = (index % 100) * 0.005
    offset_y = (index // 100) * 0.005
    x = base_lon + offset_x
    y = base_lat + offset_y
    delta = 0.002
    return Polygon([(x, y), (x + delta, y), (x + delta, y + delta), (x, y + delta), (x, y)])


def generate_linestring(index: int, base_lon: float = 77.5, base_lat: float = 12.9) -> LineString:
    """Generate a valid linestring with 5 vertices."""
    offset_x = (index % 100) * 0.005
    offset_y = (index // 100) * 0.005
    x = base_lon + offset_x
    y = base_lat + offset_y
    delta = 0.001
    return LineString(
        [(x, y), (x + delta, y + delta), (x + 2 * delta, y), (x + 3 * delta, y + delta)]
    )


def create_shapefile_zip(output_zip: Path, feature_count: int, geom_type: str = "Polygon") -> Path:
    """Create a zipped Shapefile dataset with the requested number of features."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp())

    try:
        shp_path = temp_dir / "dataset.shp"
        schema = {
            "geometry": geom_type,
            "properties": {"id": "int", "name": "str", "category": "str"},
        }
        crs = "EPSG:4326"

        with fiona.open(str(shp_path), "w", driver="ESRI Shapefile", crs=crs, schema=schema) as dst:
            for idx in range(feature_count):
                if geom_type == "Polygon":
                    geom = generate_polygon(idx)
                else:
                    geom = generate_linestring(idx)

                dst.write(
                    {
                        "geometry": mapping(geom),
                        "properties": {
                            "id": idx,
                            "name": f"Feature {idx}",
                            "category": "benchmark",
                        },
                    }
                )

        # Write .prj manually if not automatically written
        prj_path = temp_dir / "dataset.prj"
        if not prj_path.exists():
            prj_path.write_text(
                'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
            )

        # Package into zip
        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for companion in temp_dir.glob("dataset.*"):
                zf.write(companion, arcname=companion.name)

        return output_zip
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def create_kml_dataset(output_kml: Path, feature_count: int) -> Path:
    """Create a KML dataset with the requested number of Placemarks."""
    output_kml.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "  <Document>",
        f"    <name>Benchmark Dataset ({feature_count} features)</name>",
    ]

    for idx in range(feature_count):
        offset_x = (idx % 100) * 0.005
        offset_y = (idx // 100) * 0.005
        x = 77.5 + offset_x
        y = 12.9 + offset_y
        d = 0.002
        coords = f"{x},{y},0 {x + d},{y},0 {x + d},{y + d},0 {x},{y + d},0 {x},{y},0"

        placemark = f"""    <Placemark>
      <name>Polygon_{idx}</name>
      <ExtendedData>
        <Data name="id"><value>{idx}</value></Data>
        <Data name="category"><value>benchmark</value></Data>
      </ExtendedData>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>{coords}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>"""
        lines.append(placemark)

    lines.append("  </Document>")
    lines.append("</kml>")

    output_kml.write_text("\n".join(lines), encoding="utf-8")
    return output_kml


def generate_all_fixtures():
    """Generate benchmark datasets of sizes 100, 1,000, 5,000, and 10,000."""
    sizes = [100, 1000, 5000, 10000]
    print(f"Generating benchmark fixtures in {FIXTURES_DIR}...")

    for count in sizes:
        shp_zip = FIXTURES_DIR / f"polygons_{count}.zip"
        create_shapefile_zip(shp_zip, feature_count=count, geom_type="Polygon")
        print(
            f"Created Shapefile ZIP: {shp_zip.name} ({count} features, {os.path.getsize(shp_zip) / 1024:.1f} KB)"
        )

        kml_file = FIXTURES_DIR / f"polygons_{count}.kml"
        create_kml_dataset(kml_file, feature_count=count)
        print(
            f"Created KML File: {kml_file.name} ({count} features, {os.path.getsize(kml_file) / 1024:.1f} KB)"
        )


if __name__ == "__main__":
    generate_all_fixtures()
