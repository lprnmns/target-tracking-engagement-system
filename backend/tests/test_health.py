from fastapi.testclient import TestClient


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"] == "0.1.0"
    assert body["uptime_s"] >= 0

