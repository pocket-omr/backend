from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


def test_v1_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_v1_db_health_endpoint_ok(client: TestClient) -> None:
    class HealthyDB:
        async def execute(self, _query: object) -> int:
            return 1

    async def override_get_db():
        yield HealthyDB()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/v1/health/db")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "up"}
    finally:
        app.dependency_overrides.clear()


def test_v1_db_health_endpoint_unavailable(client: TestClient) -> None:
    class BrokenDB:
        async def execute(self, _query: object) -> int:
            raise RuntimeError("db down")

    async def override_get_db():
        yield BrokenDB()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/v1/health/db")
        assert response.status_code == 503
        assert response.json() == {"detail": "database unavailable"}
    finally:
        app.dependency_overrides.clear()
