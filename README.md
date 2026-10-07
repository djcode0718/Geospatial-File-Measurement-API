# Geospatial File Measurement API

A robust, production-grade backend service built with **FastAPI**, **SQLAlchemy**, and the **Python Geospatial Stack (Fiona, Shapely, PyProj, defusedxml)**. The API ingests vector geospatial datasets (`.zip` Shapefiles and `.kml` files), extracts normalized vector geometries and metadata, dynamically detects and resolves appropriate projected Coordinate Reference Systems (CRS), calculates metric area ($m^2$) and length ($m$), and provides high-performance paginated REST endpoints.

---

## 1. Features

- **Multi-Format Ingestion**: Ingests compressed ESRI Shapefiles (`.zip` containing `.shp`, `.shx`, `.dbf`, `.prj`) and Keyhole Markup Language (`.kml`) datasets.
- **Geospatial Feature Normalization**: Standardizes diverse geometry types into a uniform `ParsedFeature` model with deterministic zero-indexed ordering, geometry GeoJSON extraction, and sanitized property key-value dictionaries.
- **CRS-Aware Geometric Measurement**:
  - Automatically transforms geographic coordinates (e.g., `EPSG:4326` in degrees) to local metric projected Coordinate Reference Systems (e.g., UTM Zone projections) prior to spatial calculations.
  - Preserves already-projected source systems and rejects missing CRS metadata without speculative guessing.
  - Supports local UTM projections, southern hemisphere UTM, polar projections (UPS), and multi-zone equal-area projections.
- **Accurate Metric Calculations**:
  - **Polygon & MultiPolygon**: Surface area in square meters ($m^2$), correctly subtracting interior rings/holes.
  - **LineString & MultiLineString**: Geodesic length in meters ($m$).
  - **Point & MultiPoint**: Explicitly marked as skipped/not applicable without generating misleading zero measurements.
  - **Invalid / Unsupported Geometries**: Isolated with descriptive diagnostic warnings without halting batch execution or corrupting dataset metrics.
- **Dual Execution Modes**: Supports immediate synchronous processing (`POST /api/files/`) and decoupled background task processing (`POST /api/files/?async_mode=true`) using a unified processing engine.
- **Persistence & Fast Retrieval**: Relational schema (`FileRecord`, `FeatureRecord`, `MeasurementRecord`) backed by SQLite WAL mode (and PostgreSQL-ready), indexed on `(file_id, feature_index)` for constant-time paginated reads.
- **Security & Resource Hardening**: Defense-in-depth protections against Zip Slip path traversal, ZIP decompression bombs (100:1 ratio limit), XXE injection, XML billion-laughs entity expansion, payload memory bloat, and oversized files.
- **RFC 7807 Error Standard**: Uniform problem-details responses for client validation errors, missing resources, payload limit violations, and system faults.

---

## 2. Architecture & Design

The application follows a layered, modular architecture with clear separation of concerns across HTTP transport, job execution, file parsing, coordinate transformation, geometric measurement, and persistence.

```
                    +--------------------------------+
                    |          HTTP Client           |
                    +--------------------------------+
                                   |
                                   v
                    +--------------------------------+
                    |      FastAPI Router / API      |
                    |        (app/api/files.py)      |
                    +--------------------------------+
                                   |
                    +--------------------------------+
                    |      Processing Executor       |
                    |    (Sync or Background Task)   |
                    +--------------------------------+
                                   |
                    +--------------------------------+
                    |    File Processing Service     |
                    | (app/services/file_processing) |
                    +--------------------------------+
                      /            |            \
                     v             v             v
             +---------------+ +--------------+ +-------------------+
             | Secure Staging| | Format Reader| |    CRS Resolver   |
             | & Sanitization| |(Fiona / XML) | | & Transformation  |
             +---------------+ +--------------+ +-------------------+
                                       |                 |
                                       +--------+--------+
                                                |
                                                v
                                     +---------------------+
                                     | Measurement Engine  |
                                     | (app/geospatial/m..) |
                                     +---------------------+
                                                |
                                                v
                                     +---------------------+
                                     | SQLAlchemy ORM / DB |
                                     |  (SQLite WAL Mode)  |
                                     +---------------------+
```

### Key Architectural Boundaries
1. **Execution Decoupling**: The `ProcessingExecutor` abstraction allows synchronous HTTP execution and FastAPI `BackgroundTasks` to invoke the exact same core pipeline (`FileProcessingService.process_staged_dataset`) without code duplication.
2. **Streaming & Memory Isolation**: Large datasets are processed in-stream. Staged archives and temporary directories are managed via `StagingArea` context managers that guarantee atomic cleanup on both success and failure paths.
3. **Stateless Coordinate Transformation**: A thread-safe, LRU-cached transformer layer (`get_cached_pyproj_transformer`) avoids redundant PROJ C-context allocations across features.

---

## 3. Processing Lifecycle

```
[ Upload Payload ]
       |
       v
1. Filename & Extension Sanitization (Path traversal & control character stripping)
       |
       v
2. Secure Staging (Isolated UUID operation directory under /tmp/geomeasure_staging)
       |
       v
3. Archive / XML Security Validation (Zip Slip, zip bomb ratio check, XXE defusedxml)
       |
       v
4. Format Reading & Normalization (Fiona Shapefile / FastKML feature stream)
       |
       v
5. CRS Resolution & Validation (Inspect source CRS; detect bounds & auto-select projected CRS)
       |
       v
6. Coordinate Reprojection (PyProj always_xy=True axis normalization to metric projection)
       |
       v
7. Metric Geometry Measurement (Shapely planar area/length calculation)
       |
       v
8. Atomic Database Transaction (Batch insert FileRecord, FeatureRecords, and MeasurementRecords)
       |
       v
9. Staging Area Cleanup (Guaranteed deletion of temporary unpacked files)
       |
       v
[ Status: COMPLETED / COMPLETED_WITH_WARNINGS / FAILED ]
```

---

## 4. CRS Resolution & Transformation Strategy

### Why Geographic Coordinates (Degrees) Cannot Measure Area/Length
Geographic coordinates (`EPSG:4326` / WGS 84) represent spherical angular degrees (latitude and longitude). Because degrees of longitude shrink from the Equator toward the poles, computing Euclidean distance or polygon area directly on degree coordinates produces mathematically meaningless numbers ($degrees^2$). Accurate metric measurements require transforming coordinates into a conformal or equal-area projected coordinate reference system with meters as base units.

### Implemented CRS Decision Logic
- **Local Geographic Datasets ($< 6^\circ$ longitude span)**: Automatically computes dataset centroid longitude and selects the optimal Universal Transverse Mercator (UTM) zone (e.g., `EPSG:32643` for UTM Zone 43N in India, `EPSG:32736` for UTM Zone 36S in the southern hemisphere).
- **Polar Extents ($> 84^\circ N$ or $< 80^\circ S$)**: Automatically selects Universal Polar Stereographic North (`EPSG:32661`) or South (`EPSG:32662`).
- **Multi-Zone Regional Datasets ($\ge 6^\circ$ longitude span)**:
  - **Polygons (Area)**: Transforms to a global Equal-Area projection (`EPSG:6933` Cylindrical Equal-Area) to preserve metric surface area without distortion.
  - **LineStrings (Length)**: Projects to centroid UTM with a non-fatal warning explaining zone approximation.
- **Existing Projected CRS**: If source dataset is already in a valid projected CRS (e.g., State Plane or UTM), the system preserves the source projection.
- **Missing / Unknown CRS**: The system **rejects** the dataset explicitly (`MissingCRSError`) and never guesses an arbitrary coordinate system.
- **Coordinate Axis Normalization**: All transformations enforce `always_xy=True` in `pyproj.Transformer` to ensure `(x, y)` / `(longitude, latitude)` coordinate order regardless of authority axis definitions.

---

## 5. Geometry Measurement Behavior

| Geometry Type | Measurement | Unit | Status | Processing Behavior |
|---|---|---|---|---|
| **Polygon** | Area | $m^2$ (`square_meters`) | `SUCCESS` | Planar area of exterior ring minus all interior hole rings |
| **MultiPolygon** | Area | $m^2$ (`square_meters`) | `SUCCESS` | Sum of all constituent polygon part areas |
| **LineString** | Length | $m$ (`meters`) | `SUCCESS` | Continuous Euclidean length across all vertices |
| **MultiLineString** | Length | $m$ (`meters`) | `SUCCESS` | Sum of all constituent line part lengths |
| **Point** | — | `None` | `SKIPPED_NOT_APPLICABLE` | Skipped explicitly without producing fake zero values |
| **MultiPoint** | — | `None` | `SKIPPED_NOT_APPLICABLE` | Skipped explicitly without producing fake zero values |
| **GeometryCollection** | Area / Length | $m^2$ / $m$ | `SUCCESS` | Measured if parts are homogeneous; rejected if mixed |
| **Invalid Geometry** | — | `None` | `INVALID` | Captured with diagnostic message; does not halt dataset |
| **Unsupported Geometry** | — | `None` | `UNSUPPORTED` | Flagged cleanly without silent data loss |

---

## 6. API Reference & Examples

### Interactive Documentation
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI Schema**: `http://localhost:8000/openapi.json`

---

### 1. Health Check
```http
GET /health
```

#### Response (`200 OK`)
```json
{
  "status": "healthy",
  "app": "Geospatial File Measurement API",
  "version": "0.1.0",
  "environment": "development"
}
```

---

### 2. Upload File (Synchronous)
```http
POST /api/files/
Content-Type: multipart/form-data
```

#### cURL Example
```bash
curl -X POST \
  -F "file=@sample_polygons.zip" \
  http://localhost:8000/api/files/
```

#### Response (`201 Created`)
```json
{
  "id": "38e069e5-1354-4eb7-b0af-38af0a7b88ec",
  "filename": "sample_polygons.zip",
  "file_type": "SHAPEFILE_ZIP",
  "feature_count": 2,
  "source_crs": "EPSG:4326",
  "calculation_crs": "EPSG:32643",
  "status": "COMPLETED",
  "created_at": "2026-10-07T10:15:30.123456Z",
  "summary": {
    "total_features": 2,
    "measured_features": 2,
    "skipped_features": 0,
    "invalid_features": 0,
    "unsupported_features": 0,
    "failed_features": 0,
    "polygon_count": 2,
    "linestring_count": 0,
    "point_count": 0,
    "unsupported_count": 0,
    "total_area_m2": 24033.94,
    "total_length_m": 0.0
  },
  "error_message": null
}
```

---

### 3. Upload File (Background / Asynchronous)
```http
POST /api/files/?async_mode=true
Content-Type: multipart/form-data
```

#### cURL Example
```bash
curl -X POST \
  -F "file=@large_dataset.kml" \
  "http://localhost:8000/api/files/?async_mode=true"
```

#### Response (`202 Accepted`)
```json
{
  "id": "9fd20edf-116f-43ca-9468-1def9a199191",
  "filename": "large_dataset.kml",
  "file_type": "KML",
  "feature_count": 0,
  "source_crs": null,
  "calculation_crs": null,
  "status": "PROCESSING",
  "created_at": "2026-10-07T10:16:00.000000Z",
  "summary": null,
  "error_message": null
}
```

---

### 4. Get File Status & Metadata
```http
GET /api/files/{id}/
```

#### cURL Example
```bash
curl http://localhost:8000/api/files/38e069e5-1354-4eb7-b0af-38af0a7b88ec/
```

#### Response (`200 OK`)
```json
{
  "id": "38e069e5-1354-4eb7-b0af-38af0a7b88ec",
  "filename": "sample_polygons.zip",
  "file_type": "SHAPEFILE_ZIP",
  "file_size_bytes": 1048576,
  "status": "COMPLETED",
  "feature_count": 2,
  "source_crs": "EPSG:4326",
  "calculation_crs": "EPSG:32643",
  "summary": {
    "total_features": 2,
    "measured_features": 2,
    "skipped_features": 0,
    "invalid_features": 0,
    "unsupported_features": 0,
    "failed_features": 0,
    "polygon_count": 2,
    "linestring_count": 0,
    "point_count": 0,
    "unsupported_count": 0,
    "total_area_m2": 24033.94,
    "total_length_m": 0.0
  },
  "error_message": null,
  "created_at": "2026-10-07T10:15:30.123456Z",
  "updated_at": "2026-10-07T10:15:32.456789Z"
}
```

---

### 5. Get Paginated Feature Measurements
```http
GET /api/files/{id}/measurements/?limit=100&offset=0
```

#### Parameters
| Parameter | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `id` | UUID | required | Valid UUID | Unique identifier of the ingested dataset |
| `limit` | Integer | `100` | $1 \le limit \le 1000$ | Maximum number of feature items to return per page |
| `offset` | Integer | `0` | $offset \ge 0$ | Number of features to skip from the beginning |

#### cURL Example
```bash
curl "http://localhost:8000/api/files/38e069e5-1354-4eb7-b0af-38af0a7b88ec/measurements/?limit=2&offset=0"
```

#### Response (`200 OK`)
```json
{
  "file_id": "38e069e5-1354-4eb7-b0af-38af0a7b88ec",
  "limit": 2,
  "offset": 0,
  "total": 2,
  "items": [
    {
      "feature_id": "4b6b668f-a9cb-4654-8e1a-8cb9641aa0c1",
      "feature_index": 0,
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [[77.590, 12.970], [77.591, 12.970], [77.591, 12.971], [77.590, 12.971], [77.590, 12.970]]
        ]
      },
      "properties": {
        "name": "Parcel A",
        "parcel_id": 101
      },
      "status": "SUCCESS",
      "warning_message": null,
      "measurement": {
        "type": "area",
        "value": 12016.97,
        "unit": "square_meters",
        "calculation_crs": "EPSG:32643"
      }
    },
    {
      "feature_id": "7fa12028-3e4b-4b11-9a99-5efdb5cb9833",
      "feature_index": 1,
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [[77.592, 12.970], [77.593, 12.970], [77.593, 12.971], [77.592, 12.971], [77.592, 12.970]]
        ]
      },
      "properties": {
        "name": "Parcel B",
        "parcel_id": 102
      },
      "status": "SUCCESS",
      "warning_message": null,
      "measurement": {
        "type": "area",
        "value": 12016.97,
        "unit": "square_meters",
        "calculation_crs": "EPSG:32643"
      }
    }
  ]
}
```

---

### 6. RFC 7807 Problem Details Error Responses
All errors follow standard RFC 7807 JSON schemas:

```json
{
  "type": "https://errors.geomeasure.internal/unsupported-file-type",
  "title": "Unsupported Media Type",
  "status": 415,
  "detail": "File extension '.geojson' is not supported. Allowed formats: ['.kml', '.zip']",
  "instance": "/api/files/",
  "timestamp": "2026-10-07T10:20:00.000000Z"
}
```

| HTTP Status | Error Type | Condition |
|---|---|---|
| `400 Bad Request` | `validation-error`, `crs-error`, `malformed-archive` | Invalid parameter syntax, missing `.prj` companion, corrupted ZIP |
| `404 Not Found` | `file-not-found` | Given file UUID does not exist |
| `413 Payload Too Large` | `file-size-limit-exceeded` | Upload exceeds 50 MB limit |
| `415 Unsupported Media Type` | `unsupported-file-type` | Uploaded extension not in `['.zip', '.kml']` |
| `500 Internal Server Error` | `storage-error`, `internal-server-error` | Storage I/O failure or unhandled exception |

---

## 7. Setup & Execution Guide

### Prerequisites
- **Python**: Version `3.12+`
- **C/C++ Geospatial Libraries**: GDAL, GEOS, and PROJ (handled automatically via standard wheels on macOS/Linux).

### Quickstart Installation

```bash
# 1. Clone repository
git clone https://github.com/djcode0718/Geospatial-File-Measurement-API.git
cd "Geospatial File Measurement API"

# 2. Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install runtime dependencies
pip install -r requirements.txt

# 4. Install development & test dependencies
pip install -r requirements-dev.txt

# 5. Configure environment variables
cp .env.example .env

# 6. Apply database migrations
alembic upgrade head

# 7. Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 8. Database Schema & Persistence

The persistence layer uses **SQLAlchemy 2.0** ORM mapped to **SQLite 3** with Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and foreign key constraints enabled on every connection.

```
+-------------------------------------------------------+
|                      files                            |
+-------------------------------------------------------+
| id (PK, String(36))                                  |
| filename (String(255))                               |
| file_type (String(32))                               |
| file_size_bytes (Integer)                            |
| status (String(32), indexed)                         |
| feature_count (Integer)                              |
| source_crs (String(64))                              |
| calculation_crs (String(64))                         |
| error_message (Text, nullable)                       |
| summary_metrics (JSON, nullable)                     |
| created_at, updated_at (DateTime(UTC))               |
+-------------------------------------------------------+
                           | 1
                           |
                           | cascade="all, delete-orphan"
                           v N
+-------------------------------------------------------+
|                     features                          |
+-------------------------------------------------------+
| id (PK, String(36))                                  |
| file_id (FK -> files.id, ON DELETE CASCADE, indexed) |
| feature_index (Integer)                              |
| geometry_type (String(64))                           |
| geometry_geojson (JSON, nullable)                    |
| properties (JSON, nullable)                          |
| status (String(32))                                  |
| warning_message (Text, nullable)                     |
| created_at (DateTime(UTC))                           |
| INDEX: (file_id, feature_index)                      |
+-------------------------------------------------------+
                           | 1
                           |
                           | cascade="all, delete-orphan"
                           v 1
+-------------------------------------------------------+
|                   measurements                        |
+-------------------------------------------------------+
| id (PK, String(36))                                  |
| feature_id (FK -> features.id, ON DELETE CASCADE)    |
| measurement_type (String(32))                        |
| measurement_value (Float)                            |
| unit (String(32))                                    |
| calculation_crs (String(64))                         |
| created_at (DateTime(UTC))                           |
+-------------------------------------------------------+
```

### Persistence Features:
- **Composite Index**: `ix_features_file_id_feature_index (file_id, feature_index)` ensures index scans for deterministic pagination (`ORDER BY feature_index LIMIT ? OFFSET ?`).
- **Joined Load Strategy**: `joinedload(FeatureRecord.measurement)` fetches features and their measurement values in a single SQL query, preventing N+1 queries.
- **Idempotent Reprocessing**: Reprocessing an existing `file_id` wipes prior features in a single atomic transaction before writing updated records.

---

## 9. Security & Resource Hardening

| Protection Layer | Config Parameter | Default Value | Mechanism |
|---|---|---|---|
| **Max Upload Size** | `MAX_UPLOAD_SIZE_BYTES` | `50 MB` (52,428,800 B) | Content-Length check & streaming byte count limit |
| **Max Extracted Size** | `MAX_EXTRACTED_SIZE_BYTES` | `200 MB` (209,715,200 B) | Cumulative extraction size counter |
| **Max Archive Entries** | `MAX_ZIP_ENTRIES` | `100` files | Rejects archives with excessive file count |
| **Decompression Ratio** | `MAX_COMPRESSION_RATIO` | `100:1` | Defense against ZIP bombs (tiny zip expanding to gigabytes) |
| **Zip Slip Defense** | — | Strictly enforced | Resolves target paths and validates containment inside staging root |
| **XXE & Entity Expansion** | — | Strictly enforced | `defusedxml` parses KML with DTD and external entity resolution disabled |
| **Max Features / File** | `MAX_FEATURES_PER_FILE` | `50,000` | Prevents CPU exhaustion on massive datasets |
| **Max Coords / Geometry** | `MAX_COORDINATES_PER_GEOMETRY` | `500,000` | Prevents geometric memory exhaustion |
| **Property Payload Bound**| `MAX_PROPERTY_PAYLOAD_BYTES` | `64 KB` | Truncates bloated attribute strings to protect heap |

---

## 10. Performance & Benchmarking

Benchmarks were collected on an **Apple Silicon (ARM64) system with Python 3.12.15 and SQLite WAL mode**. Measurements reflect median values over 4 runs following a cold-start warm-up run.

### Pipeline Stage Breakdown (Shapefile `.zip`)
| Feature Count | Staging & Extract | Parsing (Fiona) | CRS + Measure | DB Persistence | Total Pipeline | Peak Memory |
|---:|---:|---:|---:|---:|---:|---:|
| **100** | 3.07 ms | 10.62 ms | 18.00 ms | 68.20 ms | **100.18 ms** | 811.3 KB |
| **1,000** | 3.60 ms | 98.03 ms | 172.81 ms | 614.63 ms | **886.91 ms** | 8.7 MB |
| **5,000** | 4.37 ms | 489.28 ms | 860.96 ms | 3,222.37 ms | **4,576.95 ms** | 45.4 MB |
| **10,000** | 6.30 ms | 995.89 ms | 1,891.46 ms | 6,519.52 ms | **9,413.12 ms** | 90.5 MB |

### Pipeline Stage Breakdown (KML `.kml`)
| Feature Count | Staging & XML Check | Parsing (defusedxml) | CRS + Measure | DB Persistence | Total Pipeline | Peak Memory |
|---:|---:|---:|---:|---:|---:|---:|
| **100** | 0.73 ms | 13.12 ms | 17.14 ms | 66.89 ms | **98.40 ms** | 850.3 KB |
| **1,000** | 1.25 ms | 148.42 ms | 182.19 ms | 705.07 ms | **1,036.94 ms** | 9.4 MB |
| **5,000** | 1.66 ms | 765.99 ms | 967.60 ms | 3,642.82 ms | **5,372.57 ms** | 47.5 MB |
| **10,000** | 3.86 ms | 1,513.02 ms | 1,957.06 ms | 7,298.48 ms | **10,793.33 ms** | 94.7 MB |

### HTTP API Read Scalability
| Dataset Size | Metadata `GET /api/files/{id}/` | Measurements (`limit=10`) | Measurements (`limit=100`) | Measurements (`limit=1000`) |
|---:|---:|---:|---:|---:|
| **100** | 1.54 ms | 3.52 ms | 3.05 ms | 3.19 ms |
| **1,000** | 1.43 ms | 2.88 ms | 3.25 ms | 28.53 ms |
| **5,000** | 1.47 ms | 3.16 ms | 3.56 ms | 15.04 ms |
| **10,000** | 1.51 ms | 3.15 ms | 3.35 ms | 15.27 ms |

### Key Performance Findings
1. **Linear Scaling**: Observed processing runtime scaled approximately linearly with feature count across both formats (~0.94 ms per feature for Shapefile; ~1.08 ms per feature for KML).
2. **Memory Efficiency**: Heap allocation scales linearly at approximately **~9 KB per feature**, with zero memory leaks across consecutive pipeline executions.
3. **Optimizations Applied**:
   - **Transformer LRU Caching**: Reduced CRS reprojection runtime by **~25% to 30%**.
   - **Persistence Batching (`db.add_all`)**: Reduced database insertion runtime by **~11% to 14%**.

---

## 11. Testing & Code Quality

### Running the Test Suite

```bash
# Run all tests with pytest
pytest

# Run tests with coverage report
pytest --cov=app --cov-report=term-missing

# Run code style & linting checks
ruff check .

# Run formatting checks
ruff format --check .

# Validate bytecode compilation
python -m compileall app
```

### Verified Test Results
- **Pytest Suite**: **132 passed** in 0.70s.
- **Code Coverage**: **89% overall statement coverage** across `app/`.
- **Ruff Linter**: `All checks passed!`
- **Ruff Formatter**: `62 files already formatted`.

### Test Suite Structure
- `tests/test_upload_api.py`: Upload validation, synchronous processing, malformed archives, missing companion files.
- `tests/test_async_api.py`: Background execution mode, 202 Accepted lifecycle, error isolation.
- `tests/test_metadata_api.py`: Metadata retrieval by UUID, 404 handling, summary metrics verification.
- `tests/test_measurements_api.py`: Paginated retrieval, boundary parameters, empty datasets, read-only behavior.
- `tests/test_measurements.py`: Unit tests for Polygon (holes, MultiPolygon), LineString, Point skipping, GeometryCollection, and CRS reprojection.
- `tests/test_crs.py`: CRS detection, UTM zone calculations, hemisphere resolution, polar projections, multi-zone strategies.
- `tests/test_geospatial_readers.py`: Shapefile and KML feature streaming and geometry normalization.
- `tests/test_storage_security.py`: Zip Slip detection, zip bomb ratio limits, XXE defenses, path sanitization.
- `tests/test_security_hardening.py`: Feature count limits, non-finite coordinates, memory bounding, internal path leakage checks.
- `tests/test_persistence.py`: SQLite WAL configuration, foreign key cascades, transaction rollbacks.
- `tests/test_failure_and_observability.py`: Reprocessing idempotency, lifecycle structured logging, and parity between sync and async execution.

---

## 12. Design Decisions & Trade-Offs

| Decision | Rationale | Trade-off / Boundary |
|---|---|---|
| **FastAPI + Pydantic v2** | High-performance asynchronous HTTP server with automatic OpenAPI generation and type validation. | Python runtime overhead compared to Go/Rust, but optimal for Python geospatial ecosystem bindings. |
| **Fiona + Shapely + PyProj** | Battle-tested industry standard C-extensions (GDAL/GEOS/PROJ) providing mathematically accurate spatial calculations. | Requires C binary wheel dependencies. |
| **In-Process `BackgroundTasks`** | Provides asynchronous request decoupling (immediate 202 response) without requiring external brokers. | Tasks are process-local; process crashes lose running jobs (for production, Celery/Redis would be used). |
| **Dynamic UTM Projection** | Essential for geographic datasets (e.g., EPSG:4326) to calculate true metric areas and lengths. | Datasets spanning $>6^\circ$ longitude require global equal-area approximations. |
| **Strict CRS Enforcement** | Rejects missing CRS Shapefiles (.prj) rather than guessing. | Prevents silent measurement corruption at the expense of requiring complete companion files. |
| **SQLite WAL Mode** | Zero-configuration single-file relational database with concurrent reader support. | Single-writer bottleneck under heavy write loads (easily switched to PostgreSQL via `DATABASE_URL`). |

---

## 13. Limitations & Future Roadmap

- **Durable Distributed Queue**: Current async execution uses FastAPI `BackgroundTasks` (process-local). A high-volume enterprise deployment would benefit from Celery / ARQ backed by Redis or RabbitMQ.
- **High-Concurrency Writes**: SQLite WAL supports multiple readers and one writer. For multi-node distributed deployments, point `DATABASE_URL` to PostgreSQL with PostGIS.
- **Ultra-Large Datasets ($>100,000$ features)**: Datasets exceeding 50,000 features are rejected by safety limits. Processing multi-gigabyte rasters or massive vector layers would require tiled chunk processing.
- **Wide Multi-Zone Line Lengths**: Multi-zone LineStrings reproject using dataset centroid UTM, which is an approximation and carries a diagnostic warning.

---

## 14. Project Structure

```text
.
├── app/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app entry point & lifespan
│   ├── api/
│   │   ├── __init__.py
│   │   ├── errors.py                    # RFC 7807 problem details exception handlers
│   │   ├── files.py                     # /api/files endpoints (Upload, Metadata, Measurements)
│   │   ├── health.py                    # /health endpoint
│   │   ├── routes.py                    # API router aggregator
│   │   └── schemas.py                   # Pydantic request/response schemas
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                    # Pydantic Settings & environment variables
│   │   ├── exceptions.py                # Domain exception hierarchy
│   │   └── logging.py                   # Structured JSON logging configuration
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py                      # SQLAlchemy declarative Base
│   │   ├── models.py                    # FileRecord, FeatureRecord, MeasurementRecord
│   │   └── session.py                   # Database engine, WAL listeners, session generators
│   ├── geospatial/
│   │   ├── __init__.py
│   │   ├── models.py                    # ParsedFeature and DatasetMetadata schemas
│   │   ├── crs/
│   │   │   ├── __init__.py
│   │   │   ├── models.py                # CRSInfo, ProjectedCRS, CRSResolutionResult
│   │   │   ├── resolver.py              # Dynamic UTM / UPS / Equal-Area CRS resolver
│   │   │   ├── transformer.py           # Thread-safe LRU-cached GeometryTransformer
│   │   │   └── validator.py             # PyProj CRS syntax and authority validator
│   │   ├── measurement/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py                # MeasurementEngine & DatasetMeasurementSummary
│   │   │   ├── handlers.py              # Polygon, LineString, Point, Collection handlers
│   │   │   └── models.py                # MeasurementResult, FeatureStatus, MeasurementType
│   │   └── readers/
│   │       ├── __init__.py
│   │       ├── base.py                  # Abstract BaseGeospatialReader
│   │       ├── kml.py                   # FastKML & defusedxml parser
│   │       └── shapefile.py             # Fiona ESRI Shapefile reader
│   ├── services/
│   │   ├── __init__.py
│   │   ├── executor.py                  # ProcessingExecutor (Sync & Background execution)
│   │   └── file_processing.py           # Pipeline orchestration & database persistence
│   └── storage/
│       ├── __init__.py
│       ├── sanitizer.py                 # Filename, size, and property sanitizers
│       ├── staging.py                   # Temporary upload directory lifecycle manager
│       ├── xml_validator.py             # defusedxml XXE inspection
│       └── zip_handler.py               # Zip Slip & zip bomb archive unpacker
├── benchmarks/
│   ├── README.md                        # Benchmark instructions and methodology
│   ├── generate_fixtures.py             # Synthetic dataset generator (100 to 10K features)
│   ├── benchmark_processing.py          # Processing pipeline benchmark harness
│   └── fixtures/                        # Generated benchmark datasets
├── migrations/                          # Alembic database migration scripts
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py        # Baseline schema with composite indexes
├── tests/                               # Comprehensive pytest test suite (132 tests)
├── .env.example                         # Environment variable configuration template
├── alembic.ini                          # Alembic migration configuration
├── ARCHITECTURE.md                      # Detailed technical architecture specification
├── PERFORMANCE.md                       # Complete benchmarking report and scaling data
├── PROJECT_SPEC.md                      # Milestone specification and phase tracking
├── pyproject.toml                       # Python package configuration
├── requirements.txt                     # Production runtime dependencies
├── requirements-dev.txt                 # Development and testing dependencies
└── README.md                            # Main project documentation
```

---

## 15. Assignment Requirements Traceability

| Requirement | Implementation Component | Verification Test |
|---|---|---|
| **Shapefile (`.zip`) Upload** | `app.storage.zip_handler` + `app.geospatial.readers.shapefile` | `tests/test_upload_api.py::test_upload_shapefile_zip_success` |
| **KML Upload** | `app.storage.xml_validator` + `app.geospatial.readers.kml` | `tests/test_upload_api.py::test_upload_kml_success` |
| **Feature Extraction** | `app.geospatial.models.ParsedFeature` | `tests/test_geospatial_readers.py` |
| **Polygon Area ($m^2$)** | `app.geospatial.measurement.handlers.PolygonMeasurementHandler` | `tests/test_measurements.py::TestPolygonMeasurementHandler` |
| **LineString Length ($m$)** | `app.geospatial.measurement.handlers.LineStringMeasurementHandler` | `tests/test_measurements.py::TestLineStringMeasurementHandler` |
| **Point Skipping** | `app.geospatial.measurement.handlers.PointMeasurementHandler` | `tests/test_measurements.py::TestPointMeasurementHandler` |
| **CRS Transformation** | `app.geospatial.crs.resolver` + `app.geospatial.crs.transformer` | `tests/test_crs.py` |
| **Status / Metadata API** | `GET /api/files/{id}/` in `app.api.files` | `tests/test_metadata_api.py` |
| **Measurements API** | `GET /api/files/{id}/measurements/` in `app.api.files` | `tests/test_measurements_api.py` |
| **Pagination** | `limit` & `offset` query parameters with deterministic indexing | `tests/test_measurements_api.py::test_get_measurements_pagination_pages` |
| **Error Handling** | RFC 7807 problem details in `app.api.errors` | `tests/test_security_hardening.py` |
| **Relational Storage** | SQLAlchemy models in `app.db.models` + SQLite WAL | `tests/test_persistence.py` |
| **Asynchronous Mode** | `POST /api/files/?async_mode=true` via `ProcessingExecutor` | `tests/test_async_api.py` |
| **Security Controls** | Zip Slip, zip bomb, XXE, payload memory bounding | `tests/test_storage_security.py` |
