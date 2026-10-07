"""Pytest fixtures and test client configuration."""

import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import create_db_engine, get_db
from app.main import app


@pytest.fixture
def test_db_session() -> Generator[Session, None, None]:
    """Provide an isolated, file-backed SQLite session for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        db_path = tmp_file.name

    db_url = f"sqlite:///{db_path}"
    engine = create_db_engine(db_url)
    Base.metadata.create_all(bind=engine)

    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = session_factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        for suffix in ["", "-wal", "-shm", "-journal"]:
            target = Path(f"{db_path}{suffix}")
            if target.exists():
                target.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def async_client(test_db_session: Session) -> AsyncGenerator[AsyncClient, None]:
    """Provide an asynchronous HTTP client bound to the FastAPI application with isolated DB."""

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
