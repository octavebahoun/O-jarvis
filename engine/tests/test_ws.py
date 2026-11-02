from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ojarvis.testing.fixtures import DEV_ORIGIN, TOKEN


def test_stream_requires_auth_message(client: TestClient) -> None:
    with client.websocket_connect("/v1/events/ws") as ws:
        ws.send_json({"type": "auth", "token": "mauvais"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_stream_rejects_foreign_origin(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect(
            "/v1/events/ws", headers={"Origin": "https://site-malveillant.example"}
        ) as ws,
    ):
        ws.receive_json()
    assert closed.value.code == 4403


def test_stream_receives_events_and_pong(client: TestClient) -> None:
    with client.websocket_connect("/v1/events/ws", headers={"Origin": DEV_ORIGIN}) as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        assert ws.receive_json()["type"] == "hello"
        client.patch("/v1/settings", json={"sphere_quality": "low"})
        received = [ws.receive_json() for _ in range(1)]
        while received[-1].get("event", {}).get("type") != "settings.changed":
            assert len(received) < 20, f"événement absent : {received}"
            received.append(ws.receive_json())
        assert received[-1]["event"]["payload"]["values"] == {"sphere_quality": "low"}
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_stream_rejects_non_json_first_message(client: TestClient) -> None:
    with client.websocket_connect("/v1/events/ws") as ws:
        ws.send_text("pas du json")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_client_leaving_before_auth_is_not_an_error(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with client.websocket_connect("/v1/events/ws") as ws:
        ws.close()
    assert client.get("/health").status_code == 200
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
