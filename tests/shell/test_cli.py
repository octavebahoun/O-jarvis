"""Le terminal du mode shell, face à un faux moteur : afficher, demander, valider, continuer."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest

from ojarvis.agent.measures import MEASURE_AFTER, MEASURE_BEFORE
from ojarvis.api.routes_chat import sse as engine_sse
from ojarvis.chat.service import ChatEvent
from ojarvis.shell import cli
from ojarvis.shell.cli import ShellClient
from ojarvis.shell.display import Style, preview, sse_events
from tests.shell.fake_engine import DONE, ROUTE, START, FakeEngine, chats, plan, session, sse


def test_a_read_shows_the_model_the_command_and_its_output() -> None:
    tool = {
        "id": "t1",
        "name": "executer_commande",
        "description": "Lancer « df -h » dans ~/projet",
        "status": "done",
        "output": "$ df -h\ndossier : ~/projet\ncode de sortie 0 (0 s)\n--- sortie ---\n"
        "/dev/sda2  457G  386G  48G  90% /",
    }
    engine = FakeEngine(
        [
            sse(
                START,
                ROUTE,
                ("tool", {**tool, "status": "running"}),
                ("tool", tool),
                ROUTE,
                ("delta", {"text": "Ton disque est plein à 90 %."}),
                DONE,
            )
        ]
    )
    client, out = session(engine, [])
    client.send("Pourquoi mon disque est plein ?")
    text = out.getvalue()
    assert text.count("· meta / muse-spark-1.3") == 1  # annoncé une fois par tour
    assert "  ✓ Lancer « df -h » dans ~/projet" in text
    assert "/dev/sda2  457G  386G  48G  90% /" in text
    assert "$ df -h" not in text  # l'en-tête est déjà dans la ligne ✓
    assert "Ton disque est plein à 90 %." in text
    assert client.conversation_id == "c1"
    assert chats(engine)[0] == {
        "text": "Pourquoi mon disque est plein ?",
        "channel": "shell",
        "folder": "/home/test/projet",
    }


def test_an_approved_action_runs_then_jarvis_continues_with_its_results() -> None:
    engine = FakeEngine(
        [
            sse(START, ("plan", plan(2)), ("delta", {"text": "Je te propose de nettoyer."}), DONE),
            sse(START, ("delta", {"text": "25,4 Go libérés, vérifié avec df."}), DONE),
        ]
    )
    client, out = session(engine, ["o"])
    client.send("Libère de la place.")
    text = out.getvalue()
    assert "┌ À valider · niveau 2 (action)" in text
    assert "│ 1. Lancer « docker image prune -f »" in text
    assert "  ✓ Lancer « docker image prune -f »" in text
    assert "Total reclaimed space: 25.4GB" in text
    assert "25,4 Go libérés" in text
    approve = next(b for m, p, b in engine.requests if p.endswith("/approve"))
    assert approve == {"double_confirm": False}
    follow = chats(engine)[1]
    assert follow["after_plan"] == ["p1"]
    assert follow["conversation_id"] == "c1"
    assert follow["text"] == cli.CONTINUE


def test_a_refused_plan_runs_nothing_and_stops_there() -> None:
    engine = FakeEngine([sse(START, ("plan", plan(2)), DONE)])
    client, out = session(engine, ["n"])
    client.send("Libère de la place.")
    paths = [p for _m, p, _b in engine.requests]
    assert "/v1/plans/p1/refuse" in paths
    assert "/v1/plans/p1/approve" not in paths
    assert len(chats(engine)) == 1  # pas de suite
    assert "Refusé : rien n'a été lancé." in out.getvalue()


def test_a_destruction_needs_the_whole_word_yes() -> None:
    engine = FakeEngine([sse(START, ("plan", plan(3)), DONE)])
    client, _out = session(engine, ["o"])  # « o » ne suffit pas pour détruire
    client.send("Supprime les images.")
    assert "/v1/plans/p1/approve" not in [p for _m, p, _b in engine.requests]

    engine = FakeEngine([sse(START, ("plan", plan(3)), DONE), sse(START, DONE)])
    client, out = session(engine, ["oui"])
    client.send("Supprime les images.")
    approve = next(b for m, p, b in engine.requests if p.endswith("/approve"))
    assert approve == {"double_confirm": True}
    assert "┌ À valider · niveau 3 (destruction)" in out.getvalue()


def test_a_reading_plan_is_followed_without_a_question() -> None:
    engine = FakeEngine([sse(START, ("plan", plan(1, status="approved")), DONE), sse(START, DONE)])
    client, _out = session(engine, [])  # aucune réponse à donner
    client.send("Regarde l'espace disque.")
    assert chats(engine)[1]["after_plan"] == ["p1"]


def test_an_engine_refusal_is_shown() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": {"message": "Dossier protégé."}})

    out = io.StringIO()
    http = httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(refuse))
    client = ShellClient(http, "~", out=out, ask=input, style=Style(False))
    client.send("Bonjour")
    assert "✗ Dossier protégé." in out.getvalue()


def test_sse_and_preview_helpers() -> None:
    lines = ["event: delta", 'data: {"text": "a"}', "", "event: done", "data: {}", ""]
    assert list(sse_events(lines)) == [("delta", {"text": "a"}), ("done", {})]
    report = "$ seq 20\ndossier : ~\ncode de sortie 0 (0 s)\n--- sortie ---\n" + "\n".join(
        str(n) for n in range(1, 21)
    )
    shown = preview(report, lines=5)
    assert shown.splitlines()[0] == "[… 15 lignes plus haut]"
    assert shown.splitlines()[-1] == "20"
    # Un échec garde son code : c'est l'information utile.
    failed = "$ make\ndossier : ~\ncode de sortie 2 (1 s)\n--- erreurs ---\nmake: *** Erreur 1"
    assert preview(failed) == "code de sortie 2 (1 s)\n--- erreurs ---\nmake: *** Erreur 1"


def test_a_code_plan_shows_its_diff_and_the_review() -> None:
    code_plan = plan(2)
    code_plan["steps"][0]["preview"] = (
        "--- a/~/x.py\n+++ b/~/x.py\n@@ -1 +1 @@\n-ancien\n+nouveau\n"
        + "\n".join(f"+ligne {n}" for n in range(100))
    )
    code_plan["review"] = {"ok": True, "notes": "", "provider": "meta", "model": "muse-wonder-1.0"}
    engine = FakeEngine([sse(START, ("plan", code_plan), DONE)])
    client, out = session(engine, ["n"])
    client.send("Corrige.")
    text = out.getvalue()
    assert "│   -ancien" in text
    assert "│   +nouveau" in text
    assert "│   [… 25 lignes de plus]" in text  # un long diff est coupé à 80 lignes
    assert "│ Relecture · meta / muse-wonder-1.0 : ✓ rien à signaler" in text


def test_closing_the_input_during_a_question_refuses_the_plan() -> None:
    engine = FakeEngine([sse(START, ("plan", plan(2)), DONE)])
    out = io.StringIO()
    http = httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(engine))

    def closed(_question: str) -> str:
        raise EOFError

    client = ShellClient(http, "/home/test/projet", out=out, ask=closed, style=Style(False))
    client.send("Libère de la place.")  # Ctrl+D : pas de plantage
    assert "/v1/plans/p1/refuse" in [p for _m, p, _b in engine.requests]
    assert "Refusé : rien n'a été lancé." in out.getvalue()


def test_the_follow_ups_stop_cleanly_at_the_budget() -> None:
    priced = ("done", {"cost_usd": 0.01})
    engine = FakeEngine(
        [
            sse(START, ("plan", plan(2)), priced),
            sse(START, ("plan", plan(2)), priced),
            sse(START, ("done", {})),
        ]
    )
    client, out = session(engine, ["o", "o"])
    client.budget = cli.Budget(follow_ups=1)
    client.send("Fais tout.")
    text = out.getvalue()
    assert len(chats(engine)) == 2  # la demande, puis une seule suite
    assert "Budget de cette demande atteint (1 suites)" in text
    assert "Écris « continue » pour reprendre." in text
    assert "· coût de cette demande : 0.020 $" in text
    # Les résultats du dernier plan partent avec le message suivant : rien n'est perdu.
    client.send("continue")
    assert chats(engine)[2]["after_plan"] == ["p1"]
    assert client.pending == []


def test_the_cost_budget_stops_before_the_next_follow_up() -> None:
    engine = FakeEngine([sse(START, ("plan", plan(2)), ("done", {"cost_usd": 2.0}))])
    client, out = session(engine, ["o"])
    client.send("Fais tout.")  # budget par défaut : 1 $
    assert len(chats(engine)) == 1
    assert "Budget de cette demande atteint (2.00 $)" in out.getvalue()


def command_plan(
    command: str, *, level: int = 2, web: bool = False, plan_id: str = "p1"
) -> dict[str, Any]:
    return {
        "id": plan_id,
        "title": "Installer",
        "status": "proposed",
        "max_level": level,
        "tainted": True,
        "web": web,
        "steps": [
            {
                "position": 1,
                "description": f"Lancer « {command} » dans ~/projet",
                "level": level,
                "tool": "executer_commande",
                "command": command,
            }
        ],
    }


def test_always_approves_the_same_kind_of_command_for_the_session(tmp_path: Path) -> None:
    engine = FakeEngine(
        [
            sse(START, ("plan", command_plan("npm install react")), DONE),
            sse(START, ("plan", command_plan("npm install vite")), DONE),
            sse(START, DONE),
        ]
    )
    client, out = session(engine, [])
    questions: list[str] = []

    def ask(question: str) -> str:
        questions.append(question)
        return "t"

    client._ask = ask
    client.home = tmp_path
    client.send("Installe React puis Vite.")
    assert len(questions) == 1  # la 2ᵉ installation ne demande rien
    assert "[t]oujours (npm install)" in questions[0]
    assert "└ Validé d'office (toujours pour cette session : npm install)" in out.getvalue()
    assert [p for _m, p, _b in engine.requests].count("/v1/plans/p1/approve") == 2
    assert client.allowed == {"npm install"}
    # Après une lecture du web, une installation demande de nouveau ta validation.
    late = FakeEngine([sse(START, ("plan", command_plan("npm install x", web=True)), DONE)])
    client.http = httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(late))
    client._ask = lambda _q: "n"
    client.send("Installe x.")
    assert "/v1/plans/p1/refuse" in [p for _m, p, _b in late.requests]
    assert "l'idée vient d'un contenu lu sur le web" in out.getvalue()


@pytest.mark.parametrize(
    "plan_data",
    [
        command_plan("curl https://exemple.com"),  # réseau
        command_plan("rm -rf build", level=3),  # destruction
        command_plan("npm install x", web=True),  # après le web
        {
            **plan(2),
            "steps": [{"position": 1, "description": "Modifier", "tool": "modifier_fichier"}],
        },
    ],
)
def test_always_is_not_offered_when_it_should_not(
    plan_data: dict[str, Any], tmp_path: Path
) -> None:
    engine = FakeEngine([sse(START, ("plan", plan_data), DONE)])
    questions: list[str] = []

    def ask(question: str) -> str:
        questions.append(question)
        return "t"

    out = io.StringIO()
    http = httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(engine))
    client = ShellClient(http, str(tmp_path), out=out, ask=ask, style=Style(False), home=tmp_path)
    client.send("Vas-y.")
    assert "[t]oujours" not in questions[0]
    assert "/v1/plans/p1/approve" not in [
        p for _m, p, _b in engine.requests
    ]  # « t » ne vaut pas « o »
    assert not client.allowed


def test_an_event_never_splits_on_unicode_line_breaks() -> None:
    # Revue du 27/10/2026 : httpx coupe aussi les lignes sur U+2028 ; un « cat » d'un fichier
    # qui en contenait faisait planter le terminal.
    text = engine_sse(ChatEvent("delta", {"text": "a\u2028b\u2029c\x85d"}))
    assert list(sse_events(text.splitlines())) == [("delta", {"text": "a\u2028b\u2029c\x85d"})]


SUDO = {"command": "sudo apt install -y ncdu", "level": 2, "why": "Pour mesurer le disque."}


def sudo_tool(proposal: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return (
        "tool",
        {
            "id": "t1",
            "name": "proposer_sudo",
            "description": f"Proposer « {proposal['command']} », que tu lances toi-même",
            "status": "done",
            "output": "Proposée.",
            "sudo": proposal,
        },
    )


def test_a_proposed_sudo_command_runs_in_your_terminal_and_its_output_returns() -> None:
    engine = FakeEngine([sse(START, sudo_tool(SUDO), DONE), sse(START, DONE)])
    client, out = session(engine, ["e"])
    ran: list[tuple[str, str]] = []

    def run(command: str, folder: str) -> tuple[int, str]:
        ran.append((command, folder))
        return 0, "ncdu installé"

    client._run = run
    client.send("Installe ncdu.")
    assert ran == [("sudo apt install -y ncdu", "/home/test/projet")]
    assert chats(engine)[1]["ran"] == {
        "command": "sudo apt install -y ncdu",
        "code": 0,
        "output": "ncdu installé",
    }
    text = out.getvalue()
    assert "Commande d'administrateur : c'est toi qui la lances" in text
    assert "il va directement à sudo, Jarvis ne le voit pas" in text


def test_a_sudo_destruction_needs_oui_and_a_refusal_runs_nothing() -> None:
    destroy = {**SUDO, "command": "sudo rm -rf /var/lib/docker/overlay2/abc", "level": 3}
    engine = FakeEngine([sse(START, sudo_tool(destroy), DONE)])
    client, out = session(engine, ["e"])  # « e » ne suffit pas pour une destruction

    def run(command: str, folder: str) -> tuple[int, str]:
        raise AssertionError(f"lancée sans « oui » : {command} dans {folder}")

    client._run = run
    client.send("Libère de la place.")
    assert "Pas lancée." in out.getvalue()
    assert len(chats(engine)) == 1  # rien ne repart vers Jarvis


def df(used: str, free: str, share: str) -> str:
    return (
        "$ df -h /\ndossier : ~\ncode de sortie 0 (0 s)\n--- sortie ---\n"
        "Sys. de fichiers Taille Utilisé Dispo Uti% Monté sur\n"
        f"/dev/sda2 457G {used} {free} {share} /"
    )


class MeasuredEngine(FakeEngine):
    """Un plan avec sa mesure : avant, l'action, après."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/plans/p1":
            return super().__call__(request)
        before, after = df("392G", "43G", "91%"), df("377G", "58G", "87%")
        steps = [
            {"position": 1, "why": MEASURE_BEFORE, "status": "done", "result": before,
             "description": "Lancer « df -h / » dans ~"},
            {"position": 2, "why": "vider le cache", "status": "done", "result": "nettoyé",
             "description": "Lancer « npm cache clean --force » dans ~"},
            {"position": 3, "why": MEASURE_AFTER, "status": "done", "result": after,
             "description": "Lancer « df -h / » dans ~"},
        ]  # fmt: skip
        return httpx.Response(200, json={"status": "done", "steps": steps})


def test_the_terminal_shows_the_measure_before_and_after() -> None:
    measured = {
        **plan(2),
        "steps": [
            {"position": 1, "description": "Lancer « df -h / » dans ~", "level": 1,
             "tool": "executer_commande", "measure": "avant"},
            {"position": 2, "description": "Lancer « npm cache clean --force » dans ~", "level": 2,
             "tool": "executer_commande", "command": "npm cache clean --force"},
            {"position": 3, "description": "Lancer « df -h / » dans ~", "level": 1,
             "tool": "executer_commande", "measure": "après"},
        ],
    }  # fmt: skip
    engine = MeasuredEngine([sse(START, ("plan", measured), DONE), sse(START, DONE)])
    client, out = session(engine, ["o"])
    client.send("Libère de la place.")
    text = out.getvalue()
    assert "1. (mesure avant) Lancer « df -h / » dans ~" in text
    assert "· Avant : /dev/sda2 457G 392G 43G 91% /" in text
    assert "· Après : /dev/sda2 457G 377G 58G 87% /" in text


def test_colors_highlight_the_brand_the_reply_and_the_model() -> None:
    engine = FakeEngine([sse(START, ROUTE, ("delta", {"text": "Salut."}), DONE)])
    client, out = session(engine, [], style=Style(True))
    cli._banner(client, Style(True), "~", False)
    client.send("Bonjour.")
    text = out.getvalue()
    assert "◉ O-jarvis" in text
    assert "◆ " in text
    assert "Salut." in text
    assert "muse-spark-1.3" in text
    assert "\033[" in text  # codes ANSI émis dans un vrai terminal
