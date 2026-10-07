"""Tests for service health check and OpenAPI documentation endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient) -> None:
    """Verify GET /health returns 200 OK and expected JSON schema."""
    response = await async_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["app"] == "Geospatial File Measurement API"
    assert data["version"] == "0.1.0"
    assert data["environment"] == "development"


@pytest.mark.asyncio
async def test_openapi_docs_endpoints(async_client: AsyncClient) -> None:
    """Verify Swagger UI and OpenAPI schema endpoints are reachable."""
    docs_response = await async_client.get("/docs")
    assert docs_response.status_code == 200

    openapi_response = await async_client.get("/openapi.json")
    assert openapi_response.status_code == 200
    openapi_data = openapi_response.json()
    assert openapi_data["info"]["title"] == "Geospatial File Measurement API"
    assert openapi_data["info"]["version"] == "0.1.0"
    assert "/api/files/{id}/measurements/" in openapi_data["paths"]
    measurements_path = openapi_data["paths"]["/api/files/{id}/measurements/"]["get"]
    param_names = [p["name"] for p in measurements_path["parameters"]]
    assert "id" in param_names
    assert "limit" in param_names
    assert "offset" in param_names
