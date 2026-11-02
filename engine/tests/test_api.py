from __future__ import annotations

import json
from pathlib import Path

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backends import null

from ojarvis import __version__
from ojarvis.app import create_app
from ojarvis.config import EngineConfig
from ojarvis.security.redaction import REDACTOR
from ojarvis.security.secrets import MemoryBackend
from ojarvis.services import Services
from ojarvis.testing.fixtures import DEV_ORIGIN, TOKEN, Router


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("header", ["", "Bearer mauvais", "Basic " + TOKEN, TOKEN])
def test_routes_require_the_token(client: TestClient, header: str) -> None:
    response = client.get("/v1/status", headers={"Authorization": header})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["correlation_id"]


def test_unknown_host_is_refused(client: TestClient) -> None:
    response = client.get("/health", headers={"Host": "attaquant.example"})
    assert response.status_code == 400


def test_cors_preflight(client: TestClient) -> None:
    allowed = client.options(
        "/v1/status",
        headers={"Origin": DEV_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == DEV_ORIGIN
    denied = client.options(
        "/v1/status",
        headers={
            "Origin": "https://site-malveillant.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in denied.headers


def test_correlation_id_round_trip(client: TestClient) -> None:
    response = client.get("/v1/status", headers={"X-Correlation-ID": "demande-42"})
    assert response.headers["x-correlation-id"] == "demande-42"
    generated = client.get("/v1/status", headers={"X-Correlation-ID": "invalide !"})
    assert generated.headers["x-correlation-id"] != "invalide !"


def test_status(client: TestClient) -> None:
    body = client.get("/v1/status").json()
    assert body["version"] == __version__
    assert body["keyring"] == "memoire"
    assert body["keyring_error"] is None
    assert body["secrets_total"] == 15  # BASETEN_API_KEY, ajoutée le 27/10
    assert body["secrets_configured"] == 0
    assert body["events_published"] >= 1


def test_secret_value_is_never_returned(client: TestClient) -> None:
    secret = "valeur-qui-ne-doit-jamais-revenir"
    response = client.put("/v1/secrets/DEEPSEEK_API_KEY", json={"value": secret})
    assert response.status_code == 204
    assert response.content == b""
    listing = client.get("/v1/secrets")
    assert secret not in listing.text
    row = next(r for r in listing.json() if r["name"] == "DEEPSEEK_API_KEY")
    assert row["configured"] is True
    assert row["source"] == "ui"
    assert set(row) >= {"label", "group", "help", "last_check_ok"}
    events = client.get("/v1/events").text
    assert secret not in events


def test_secret_delete_and_unknown(client: TestClient) -> None:
    client.put("/v1/secrets/NOTION_TOKEN", json={"value": "ntn_valeur"})
    assert client.delete("/v1/secrets/NOTION_TOKEN").status_code == 204
    row = next(r for r in client.get("/v1/secrets").json() if r["name"] == "NOTION_TOKEN")
    assert row["configured"] is False
    assert client.put("/v1/secrets/AWS_KEY", json={"value": "x"}).status_code == 404
    assert client.delete("/v1/secrets/AWS_KEY").status_code == 404


def test_secret_input_validation(client: TestClient) -> None:
    response = client.put("/v1/secrets/DEEPSEEK_API_KEY", json={"value": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    extra = client.put("/v1/secrets/DEEPSEEK_API_KEY", json={"value": "a", "autre": 1})
    assert extra.status_code == 422
    newline = client.put("/v1/secrets/DEEPSEEK_API_KEY", json={"value": "a\nb"})
    assert newline.status_code == 422


def test_check_success_records_trace(client: TestClient, upstream: dict[str, Router]) -> None:
    upstream["api.deepseek.com"] = lambda r: httpx.Response(
        200, json={"data": [{"id": "deepseek-flash"}]}
    )
    client.put("/v1/secrets/DEEPSEEK_API_KEY", json={"value": "cle-de-test-deepseek"})
    body = client.post("/v1/secrets/DEEPSEEK_API_KEY/check").json()
    assert body["ok"] is True
    assert "deepseek-flash" in body["detail"]
    trace = client.get(f"/v1/traces/{body['trace_id']}").json()
    assert trace["status"] == "ok"
    assert trace["kind"] == "check"
    assert trace["spans"][0]["kind"] == "http"
    row = next(r for r in client.get("/v1/secrets").json() if r["name"] == "DEEPSEEK_API_KEY")
    assert row["last_check_ok"] is True
    listing = client.get("/v1/traces").json()
    assert listing[0]["id"] == body["trace_id"]


def test_check_failure_marks_trace_error(client: TestClient, upstream: dict[str, Router]) -> None:
    upstream["api.tavily.com"] = lambda r: httpx.Response(401, json={})
    client.put("/v1/secrets/TAVILY_API_KEY", json={"value": "tvly-mauvaise-cle-12345"})
    body = client.post("/v1/secrets/TAVILY_API_KEY/check").json()
    assert body["ok"] is False
    assert body["detail"] == "Clé refusée par le service."
    assert client.get(f"/v1/traces/{body['trace_id']}").json()["status"] == "error"


def test_check_without_value(client: TestClient) -> None:
    body = client.post("/v1/secrets/OPENAI_API_KEY/check").json()
    assert body["ok"] is False
    assert body["detail"] == "Aucune valeur enregistrée."


def test_unknown_trace_is_normalized_404(client: TestClient) -> None:
    response = client.get("/v1/traces/absent")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_traces_query_validation(client: TestClient) -> None:
    assert client.get("/v1/traces?limit=0").status_code == 422
    assert client.get("/v1/traces?limit=10").status_code == 200


def test_settings_endpoints(client: TestClient) -> None:
    assert client.get("/v1/settings").json()["reduced_motion"] == "system"
    updated = client.patch("/v1/settings", json={"reduced_motion": "reduce"})
    assert updated.json()["reduced_motion"] == "reduce"
    assert client.patch("/v1/settings", json={"inconnu": True}).status_code == 422
    changed = client.get("/v1/events?type_prefix=settings.").json()
    assert changed[0]["payload"]["keys"] == ["reduced_motion"]


def test_perf_report_is_saved(client: TestClient, services: Services) -> None:
    report = {
        "runtime": "tauri-webkitgtk",
        "user_agent": "test",
        "device_pixel_ratio": 1,
        "canvas_px": 440,
        "samples": [
            {
                "state": "idle",
                "duration_s": 5,
                "frames": 300,
                "fps_mean": 60,
                "frame_ms_p50": 16.6,
                "frame_ms_p95": 17.1,
                "frame_ms_p99": 18,
                "long_frames": 0,
            }
        ],
    }
    body = client.post("/v1/diagnostics/perf", json=report).json()
    saved = json.loads(Path(body["saved_as"]).read_text(encoding="utf-8"))
    assert saved["samples"][0]["fps_mean"] == 60
    assert body["saved_as"].startswith(str(services.config.paths.state_dir))
    assert client.post("/v1/diagnostics/perf", json={"runtime": "x"}).status_code == 422


def test_docs_hidden_outside_dev(config: EngineConfig) -> None:
    prod = EngineConfig(paths=config.paths, dev_mode=False, allowed_hosts=config.allowed_hosts)
    app = create_app(prod, token=TOKEN, secret_backend=MemoryBackend(), discover=False)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404


def test_short_token_refused(config: EngineConfig) -> None:
    with pytest.raises(ValueError, match="jeton"):
        create_app(config, token="court")


def test_unexpected_errors_are_normalized(config: EngineConfig) -> None:
    app = create_app(config, token=TOKEN, secret_backend=MemoryBackend(), discover=False)
    with TestClient(
        app, headers={"Authorization": f"Bearer {TOKEN}"}, raise_server_exceptions=False
    ) as raw:

        async def boom() -> None:
            raise RuntimeError("détail interne à ne pas exposer")

        raw.app.state.services.settings.get = boom  # type: ignore[attr-defined]
        response = raw.get("/v1/settings")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "détail interne" not in response.text


def test_memory_keyring_only_in_dev(config: EngineConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OJARVIS_MEMORY_KEYRING", "1")
    # Pas de PYTHON_KEYRING_BACKEND : keyring garderait ce trousseau nul pour les tests suivants.
    unusable = null.Keyring()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(keyring, "get_keyring", lambda: unusable)
    app = create_app(config, token=TOKEN, discover=False)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as dev:
        assert dev.get("/v1/status").json()["keyring"] == "memoire"
    prod = EngineConfig(paths=config.paths, dev_mode=False, allowed_hosts=config.allowed_hosts)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(prod, token=TOKEN, discover=False), headers=headers) as c:
        body = c.get("/v1/status").json()
    assert body["keyring"] == "indisponible"
    assert body["keyring_error"]


def test_session_token_is_redacted(client: TestClient) -> None:
    assert TOKEN not in REDACTOR.redact(f"jeton {TOKEN}")
