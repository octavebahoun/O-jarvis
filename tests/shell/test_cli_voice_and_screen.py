"""Le terminal, avec la voix et l'écran : la dictée, le clic montré sur la capture, le partage."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ojarvis.shell import cli
from ojarvis.shell.display import Style
from tests.shell.fake_engine import DONE, START, FakeEngine, chats, plan, session, sse


class VoiceEngine(FakeEngine):
    """Le moteur, avec la voix : une phrase entendue, une réponse dite."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path not in ("/v1/voice/dictate", "/v1/voice/say"):
            return super().__call__(request)
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/voice/dictate":
            return httpx.Response(200, json={"text": "Liste les fichiers du dossier."})
        return httpx.Response(200, json={"spoken": True})


def test_a_voice_turned_off_is_said_in_the_terminal() -> None:
    def no_voice(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/voice/dictate"
        message = "La voix est éteinte : allume-la dans Réglages (Voix)."
        return httpx.Response(409, json={"error": {"message": message}})

    client, out = session(FakeEngine([]), [])
    client.http = httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(no_voice))
    assert client.dictate() is None
    assert "La voix est éteinte : allume-la dans Réglages (Voix)." in out.getvalue()


def test_a_spoken_request_is_shown_then_handled_like_a_written_one() -> None:
    reply = ("delta", {"text": "Il y a `a.txt` et **b.txt**."})
    engine = VoiceEngine([sse(START, reply, DONE)])
    client, out = session(engine, [])
    heard = client.dictate()
    assert heard == "Liste les fichiers du dossier."
    client.send(heard, spoken=True)
    assert chats(engine)[0]["text"] == "Liste les fichiers du dossier."  # comme écrite
    said = [body for _method, path, body in engine.requests if path == "/v1/voice/say"]
    assert said == [{"text": "Il y a a.txt et b.txt."}]  # sans les marques du terminal
    assert "Tu as dit : « Liste les fichiers du dossier. »" in out.getvalue()


def test_a_click_plan_shows_where_jarvis_will_click(tmp_path: Path) -> None:
    """#48 au terminal : la capture annotée, et la fenêtre à remettre devant."""
    (tmp_path / "rab12cd.jpg").write_bytes(b"\xff\xd8capture")
    click = plan(2)
    click["steps"] = [
        {
            "position": 1,
            "description": "Cliquer sur le bouton Exporter en PDF (repère rab12cd)",
            "level": 2,
            "tool": "cliquer_ecran",
            "repere": "rab12cd",
        }
    ]
    engine = FakeEngine([sse(START, ("plan", click), DONE)])
    client, out = session(engine, ["n"])
    client.marks_dir = tmp_path
    client.send("Clique sur Exporter en PDF.")
    shown = out.getvalue()
    assert f"où Jarvis va cliquer : {(tmp_path / 'rab12cd.jpg').as_uri()}" in shown
    assert "mets la fenêtre visée devant" in shown


class ShareEngine(FakeEngine):
    """Le moteur, avec le partage d'écran : démarré puis arrêté."""

    def __init__(self, *, pc_control: bool = True) -> None:
        super().__init__([])
        self.active = False
        self.pc_control = pc_control

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith("/v1/desktop/share"):
            return super().__call__(request)
        self.requests.append((request.method, path, {}))
        if path.endswith("/start"):
            if not self.pc_control:
                message = "Le partage d'écran sert au Contrôle du PC : active-le d'abord."
                return httpx.Response(409, json={"error": {"message": message}})
            self.active = True
        elif path.endswith("/stop"):
            self.active = False
        return httpx.Response(200, json={"active": self.active, "frames": 0, "since": None})


def test_the_screen_is_shared_and_unshared_from_the_terminal() -> None:
    engine = ShareEngine()
    client, out = session(engine, [])
    assert cli._command(client, Style(False), "/partage")
    assert "Écran partagé en direct" in out.getvalue()
    cli._command(client, Style(False), "/partage")
    assert "Partage de l'écran arrêté." in out.getvalue()
    starts = [path for _m, path, _b in engine.requests if path.endswith(("/start", "/stop"))]
    assert starts == ["/v1/desktop/share/start", "/v1/desktop/share/stop"]
    refused = ShareEngine(pc_control=False)
    client, out = session(refused, [])
    cli._command(client, Style(False), "/partage")
    assert "active-le d'abord" in out.getvalue()
