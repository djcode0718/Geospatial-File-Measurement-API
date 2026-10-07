"""Comprehensive, reproducible benchmark harness measuring the Geospatial Processing Pipeline."""

import io
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.session as app_session_mod
from app.db.base import Base
from app.db.models import FileRecord, FileType
from app.geospatial.readers.kml import KMLReader
from app.geospatial.readers.shapefile import ShapefileReader
from app.main import app
from app.services.file_processing import FileProcessingService
from app.storage.staging import StagingArea
from app.storage.zip_handler import validate_shapefile_components

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def get_system_specs() -> dict[str, str]:
    """Gather hardware and environment metadata."""
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "architecture": platform.architecture()[0],
    }


def benchmark_pipeline_stage_breakdown(file_path: Path, file_type: FileType, runs: int = 3) -> dict:
    """Measure exact stage-by-stage execution times and peak memory across multiple runs."""
    stage_timings = {
        "staging_extraction_ms": [],
        "parsing_ms": [],
        "measurement_ms": [],
        "persistence_ms": [],
        "total_ms": [],
        "peak_memory_kb": [],
    }

    # Setup isolated SQLite DB in temp directory
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db_path = temp_db.name
    temp_db.close()

    engine = create_engine(f"sqlite:///{temp_db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_test_cls = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    service = FileProcessingService()

    try:
        for run_idx in range(runs + 1):  # Run 0 is warm-up
            tracemalloc.start()
            total_start = time.perf_counter()

            # 1. Staging & Extraction
            t0 = time.perf_counter()
            staging = StagingArea(auto_cleanup=False)
            staged_path = staging.save_upload(file_path.read_bytes(), file_path.name)

            if file_type == FileType.SHAPEFILE_ZIP:
                extracted_files = staging.extract_archive(staged_path)
                target_path = validate_shapefile_components(extracted_files)
                reader = ShapefileReader()
            else:
                target_path = staged_path
                reader = KMLReader()
            t_staging = (time.perf_counter() - t0) * 1000

            # 2. Parsing
            t0 = time.perf_counter()
            features, metadata = reader.read_dataset(target_path)
            t_parsing = (time.perf_counter() - t0) * 1000

            # 3. Measurement (includes CRS resolution + coordinate reprojection)
            t0 = time.perf_counter()
            results, summary = service.measurement_engine.measure_dataset(features)
            t_measurement = (time.perf_counter() - t0) * 1000

            # 4. Database Persistence
            t0 = time.perf_counter()
            with session_test_cls() as db:
                file_record = FileRecord(
                    id=staging.operation_id,
                    filename=file_path.name,
                    file_type=file_type.value,
                    status="PROCESSING",
                )
                db.add(file_record)
                db.commit()

                # Execute persistence
                service.process_staged_dataset(
                    file_id=file_record.id,
                    staged_path=str(staged_path),
                    filename=file_path.name,
                    file_type=file_type,
                    db=db,
                    staging=staging,
                )
            t_persistence = (time.perf_counter() - t0) * 1000

            total_elapsed = (time.perf_counter() - total_start) * 1000
            current_mem, peak_mem = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            staging.cleanup()

            if run_idx > 0:  # Skip warm-up run
                stage_timings["staging_extraction_ms"].append(t_staging)
                stage_timings["parsing_ms"].append(t_parsing)
                stage_timings["measurement_ms"].append(t_measurement)
                stage_timings["persistence_ms"].append(t_persistence)
                stage_timings["total_ms"].append(total_elapsed)
                stage_timings["peak_memory_kb"].append(peak_mem / 1024)

    finally:
        engine.dispose()
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)

    return {
        "staging_extraction_ms": statistics.median(stage_timings["staging_extraction_ms"]),
        "parsing_ms": statistics.median(stage_timings["parsing_ms"]),
        "measurement_ms": statistics.median(stage_timings["measurement_ms"]),
        "persistence_ms": statistics.median(stage_timings["persistence_ms"]),
        "total_ms": statistics.median(stage_timings["total_ms"]),
        "peak_memory_kb": statistics.median(stage_timings["peak_memory_kb"]),
    }


def benchmark_api_endpoints(file_path: Path) -> dict:
    """Benchmark API endpoints: Sync upload, Async upload, Metadata GET, and Paginated Measurements."""
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db_path = temp_db.name
    temp_db.close()

    engine = create_engine(f"sqlite:///{temp_db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_test_cls = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    orig_session_local = app_session_mod.SessionLocal
    app_session_mod.SessionLocal = session_test_cls

    def override_get_db():
        session = session_test_cls()
        try:
            yield session
        finally:
            session.close()

    from app.db.session import get_db

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    try:
        content = file_path.read_bytes()
        filename = file_path.name
        mime_type = (
            "application/zip"
            if filename.endswith(".zip")
            else "application/vnd.google-earth.kml+xml"
        )

        # 1. Sync Upload
        t0 = time.perf_counter()
        resp_sync = client.post(
            "/api/files/",
            files={"file": (filename, io.BytesIO(content), mime_type)},
        )
        sync_latency_ms = (time.perf_counter() - t0) * 1000
        file_id = resp_sync.json()["id"]

        # 2. Async Upload
        t0 = time.perf_counter()
        client.post(
            "/api/files/?async_mode=true",
            files={"file": (filename, io.BytesIO(content), mime_type)},
        )
        async_http_latency_ms = (time.perf_counter() - t0) * 1000

        # 3. Metadata GET
        t0 = time.perf_counter()
        client.get(f"/api/files/{file_id}/")
        metadata_latency_ms = (time.perf_counter() - t0) * 1000

        # 4. Paginated Measurements GET
        pagination_latencies = {}
        for limit in [10, 100, 1000]:
            t0 = time.perf_counter()
            client.get(f"/api/files/{file_id}/measurements/?limit={limit}&offset=0")
            pagination_latencies[f"page_limit_{limit}_ms"] = (time.perf_counter() - t0) * 1000

        return {
            "sync_upload_latency_ms": sync_latency_ms,
            "async_upload_latency_ms": async_http_latency_ms,
            "metadata_get_latency_ms": metadata_latency_ms,
            **pagination_latencies,
        }

    finally:
        app.dependency_overrides.clear()
        app_session_mod.SessionLocal = orig_session_local
        engine.dispose()
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)


def run_full_benchmark_suite():
    """Execute complete benchmarking across 100, 1,000, 5,000, and 10,000 features."""
    specs = get_system_specs()
    print("=" * 80)
    print("GEOSPATIAL API PERFORMANCE BENCHMARK HARNESS")
    print(f"System: {specs['platform']} ({specs['processor']}, {specs['architecture']})")
    print(f"Python: {specs['python_version']}")
    print("=" * 80)

    sizes = [100, 1000, 5000, 10000]

    print("\n--- 1. SHAPEFILE PROCESSING BENCHMARKS ---")
    shp_results = {}
    for count in sizes:
        shp_file = FIXTURES_DIR / f"polygons_{count}.zip"
        if not shp_file.exists():
            continue
        res = benchmark_pipeline_stage_breakdown(shp_file, FileType.SHAPEFILE_ZIP, runs=3)
        shp_results[count] = res
        print(f"\nShapefile {count} features:")
        print(f"  - Staging/Extract: {res['staging_extraction_ms']:.2f} ms")
        print(f"  - Parsing (Fiona): {res['parsing_ms']:.2f} ms")
        print(f"  - Measurement (CRS+Area): {res['measurement_ms']:.2f} ms")
        print(f"  - DB Persistence:  {res['persistence_ms']:.2f} ms")
        print(f"  - Total Pipeline:  {res['total_ms']:.2f} ms")
        print(f"  - Peak Memory:     {res['peak_memory_kb']:.1f} KB")

    print("\n--- 2. KML PROCESSING BENCHMARKS ---")
    kml_results = {}
    for count in sizes:
        kml_file = FIXTURES_DIR / f"polygons_{count}.kml"
        if not kml_file.exists():
            continue
        res = benchmark_pipeline_stage_breakdown(kml_file, FileType.KML, runs=3)
        kml_results[count] = res
        print(f"\nKML {count} features:")
        print(f"  - Staging/Extract: {res['staging_extraction_ms']:.2f} ms")
        print(f"  - Parsing (XML):   {res['parsing_ms']:.2f} ms")
        print(f"  - Measurement (CRS+Area): {res['measurement_ms']:.2f} ms")
        print(f"  - DB Persistence:  {res['persistence_ms']:.2f} ms")
        print(f"  - Total Pipeline:  {res['total_ms']:.2f} ms")
        print(f"  - Peak Memory:     {res['peak_memory_kb']:.1f} KB")

    print("\n--- 3. HTTP API LATENCY & PAGINATION BENCHMARKS ---")
    for count in [100, 1000, 5000, 10000]:
        shp_file = FIXTURES_DIR / f"polygons_{count}.zip"
        if not shp_file.exists():
            continue
        api_res = benchmark_api_endpoints(shp_file)
        print(f"\nAPI Latency for Shapefile ({count} features):")
        print(
            f"  - POST /api/files/ (Sync HTTP Latency):  {api_res['sync_upload_latency_ms']:.2f} ms"
        )
        print(
            f"  - POST /api/files/?async_mode=true (Async HTTP Latency): {api_res['async_upload_latency_ms']:.2f} ms"
        )
        print(
            f"  - GET /api/files/{{id}}/ (Metadata):      {api_res['metadata_get_latency_ms']:.2f} ms"
        )
        print(
            f"  - GET /api/files/{{id}}/measurements/?limit=10:   {api_res['page_limit_10_ms']:.2f} ms"
        )
        print(
            f"  - GET /api/files/{{id}}/measurements/?limit=100:  {api_res['page_limit_100_ms']:.2f} ms"
        )
        print(
            f"  - GET /api/files/{{id}}/measurements/?limit=1000: {api_res['page_limit_1000_ms']:.2f} ms"
        )


if __name__ == "__main__":
    run_full_benchmark_suite()
