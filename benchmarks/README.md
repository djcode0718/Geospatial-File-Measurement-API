# Geospatial File Measurement API — Performance Benchmark Suite

This directory contains a reproducible benchmark harness designed to measure the end-to-end performance and resource consumption of the complete geospatial processing pipeline and HTTP endpoints.

## 1. Overview

The benchmark suite evaluates:
- **Pipeline Stage Breakdown**: Staging/Extraction, Parsing (Fiona/XML), CRS Resolution & Metric Area Measurement, and Database Persistence (`SQLite WAL`).
- **Memory Scaling**: Peak memory consumption across dataset sizes using Python's `tracemalloc`.
- **HTTP API Latencies**:
  - `POST /api/files/` (Synchronous HTTP latency)
  - `POST /api/files/?async_mode=true` (Background task submission latency)
  - `GET /api/files/{id}/` (O(1) metadata retrieval latency)
  - `GET /api/files/{id}/measurements/?limit={10,100,1000}` (Paginated measurements retrieval latency)

---

## 2. Directory Structure

```text
benchmarks/
├── README.md                  # Benchmark instructions and documentation
├── generate_fixtures.py       # Deterministic fixture generator (Fixed seed: 42)
├── benchmark_processing.py    # Multi-run benchmark harness with statistical aggregation
└── fixtures/                  # Generated synthetic Shapefile .zip and KML datasets
    ├── polygons_100.zip / .kml
    ├── polygons_1000.zip / .kml
    ├── polygons_5000.zip / .kml
    └── polygons_10000.zip / .kml
```

---

## 3. How to Run the Benchmarks

### Step 1: Generate Deterministic Fixtures
Run the fixture generator to create synthetic polygon datasets (100, 1,000, 5,000, and 10,000 features in EPSG:4326):

```bash
python benchmarks/generate_fixtures.py
```

### Step 2: Execute the Benchmark Harness
Run the benchmark harness. It executes 1 warm-up run followed by 4 measured runs per dataset size, reporting the median timings for each processing stage and endpoint:

```bash
python benchmarks/benchmark_processing.py
```

---

## 4. Methodology & Metrics

1. **Warm-up Strategy**:
   - For each dataset tier, 1 cold-start execution runs first to warm up Python imports, PROJ CRS database connections, and SQLite schema caches.
   - The subsequent 4 iterations are measured.
2. **Aggregation**:
   - The **median** value across measured runs is reported to eliminate outlier variance from OS context switches.
3. **Memory Profile**:
   - Peak allocated memory during dataset processing is captured using `tracemalloc.get_traced_memory()`.
4. **Correctness Verification**:
   - The benchmark harness asserts that every feature status is `COMPLETED`, calculation CRS is resolved to `EPSG:32643` (UTM Zone 43N), and no features are dropped or corrupted.
