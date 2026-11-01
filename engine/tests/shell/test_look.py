"""La mise en page du terminal : la réponse coupée aux mots, l'accueil, l'invite et l'en-tête."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from ojarvis.shell.banner import banner, prompt
from ojarvis.shell.display import Style
from ojarvis.shell.prose import Prose
from tests.shell.fake_engine import DONE, ROUTE, START, FakeEngine, plan, session, sse


def written(text: str | list[str], *, width: int | None = None, style: bool = False) -> str:
    out: list[str] = []
    prose = Prose(out.append, Style(style), width)
    for chunk in [text] if isinstance(text, str) else text:
        prose.feed(chunk)
    prose.close()
    return "".join(out)


def test_the_reply_is_indented_and_cut_between_words() -> None:
    reply = "Il te reste **58 Go** sur 457 Go : le disque est plein."
    expected = "  Il te reste 58 Go\n  sur 457 Go : le\n  disque est plein."
    assert written(reply, width=20) == expected
    # Le texte arrive par morceaux, coupés n'importe où : le même rendu.
    assert written([reply[i : i + 3] for i in range(0, len(reply), 3)], width=20) == expected


def test_paragraphs_keep_one_blank_line_and_leading_blanks_vanish() -> None:
    assert written("\n\nUn.\n\n\n\nDeux.\nTrois.") == "  Un.\n\n  Deux.\n  Trois."


def test_a_code_block_keeps_its_spaces_under_a_rail() -> None:
    assert written("Lance :\n```bash\nls  -la\n```\nFini.") == "  Lance :\n  │ ls  -la\n  Fini."


def test_bold_code_and_titles_lose_their_marks() -> None:
    text = written("## Bilan\nIl reste **58 Go** dans `~/Vidéos`.", style=True)
    assert "#" not in text
    assert "*" not in text
    assert "`" not in text
    assert "\033[1mBilan\033[0m" in text
    assert "\033[1m58\033[0m \033[1mGo\033[0m" in text
    assert "\033[36m~/Vidéos\033[0m." in text


def test_the_header_comes_once_per_request_even_after_a_plan() -> None:
    engine = FakeEngine(
        [
            sse(START, ROUTE, ("delta", {"text": "Je nettoie."}), ("plan", plan(2)), DONE),
            sse(START, ROUTE, ("delta", {"text": "C'est fait."}), DONE),
        ]
    )
    client, out = session(engine, ["o"])
    client.send("Libère de la place.")
    text = out.getvalue()
    assert text.count("◆ Jarvis") == 1
    assert text.startswith("\n◆ Jarvis   meta / muse-spark-1.3\n  Je nettoie.\n")
    assert "\n  ╰─" not in text  # la question part avec ta réponse, pas dans la sortie
    assert text.endswith("\n\n  C'est fait.\n  0,0 s\n")


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_the_prompt_shows_the_folder_and_its_git_branch(tmp_path: Path) -> None:
    project = tmp_path / "boutique"
    project.mkdir()
    assert prompt(Style(False), project, tmp_path) == "\nboutique ❯ "
    git = shutil.which("git") or "git"
    subprocess.run([git, "init", "-q", "-b", "main", str(project)], check=True)  # noqa: S603
    assert prompt(Style(False), project, tmp_path) == "\nboutique main ❯ "
    assert prompt(Style(False), tmp_path, tmp_path).startswith("\n~ ")


class MetaEngine(FakeEngine):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/settings":
            return httpx.Response(200, json={"model_profile": "meta"})
        return super().__call__(request)


def test_the_welcome_shows_the_folder_the_meta_models_and_the_commands(tmp_path: Path) -> None:
    client, out = session(MetaEngine([]), [])
    banner(client, client.http, Style(False), tmp_path / "x", tmp_path, own_engine=False)
    text = out.getvalue()
    assert " ◉ O-jarvis  Shell  v" in text
    assert "    dossier  ~/x" in text
    assert "    modèles  Tout Meta · Muse Spark 1.3, secours Muse Spark Open" in text
    assert "    moteur   celui de ton app" in text
    assert "/voix parler   /partage écran en direct" in text
