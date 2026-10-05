import time

from fastapi.testclient import TestClient


def test_serial_status_endpoint(client: TestClient) -> None:
    response = client.get("/api/serial/status")

    assert response.status_code == 200
    body = response.json()
    assert body["transport_mode"] == "mock"
    assert body["protocol_mode"] == "json-line"
    assert body["real_serial_enabled"] is False


def test_serial_send_json_safe_command(client: TestClient) -> None:
    response = client.post(
        "/api/serial/send-json",
        json={"message": {"type": "disarm", "seq": 2, "reason": "operator_request"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["status"]["pending_ack_count"] == 1


def test_serial_send_json_risky_command_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/serial/send-json",
        json={"message": {"type": "fire_request", "seq": 9, "reason": "test"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert "Risky serial command is disabled" in body["reason"]


def test_ack_handling(client: TestClient) -> None:
    client.post("/api/serial/send-json", json={"message": {"type": "disarm", "seq": 3, "reason": "test"}})
    response = client.post("/api/serial/simulate-rx", json={"message": {"type": "ack", "seq": 3, "accepted": True}})

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["status"]["pending_ack_count"] == 0
    assert body["log_entry"]["kind"] == "ack"


def test_nack_handling(client: TestClient) -> None:
    client.post("/api/serial/send-json", json={"message": {"type": "disarm", "seq": 4, "reason": "test"}})
    response = client.post("/api/serial/simulate-rx", json={"message": {"type": "nack", "seq": 4, "reason": "ESTOP_ACTIVE"}})

    body = response.json()
    assert body["accepted"] is True
    assert body["status"]["pending_ack_count"] == 0
    assert body["status"]["last_error"] == "NACK:ESTOP_ACTIVE"


def test_timeout_handling(client: TestClient) -> None:
    client.post("/api/serial/send-json", json={"message": {"type": "disarm", "seq": 5, "reason": "test"}})
    client.app.state.runtime.serial.pending[5]["sent_at"] = time.time() - 1

    response = client.get("/api/serial/status")

    body = response.json()
    assert body["connection_state"] == "FAULT"
    assert body["last_error"] == "ACK_TIMEOUT:5"
    assert body["pending_ack_count"] == 0


def test_heartbeat_timeout_updates_error_state(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.serial.last_heartbeat_at = time.time() - 2

    response = client.get("/api/serial/status")

    body = response.json()
    assert body["connection_state"] == "FAULT"
    assert body["last_error"] == "HEARTBEAT_TIMEOUT"


def test_serial_logs_and_clear(client: TestClient) -> None:
    client.post("/api/serial/send-json", json={"message": {"type": "heartbeat", "seq": 1, "timestamp_ms": 123}})

    logs = client.get("/api/serial/logs").json()
    assert len(logs) >= 1

    clear = client.post("/api/serial/clear-logs").json()
    assert clear["accepted"] is True
    assert client.get("/api/serial/logs").json()[0]["kind"] == "status"
