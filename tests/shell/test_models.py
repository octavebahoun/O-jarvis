"""« /modele » : le modèle de Meta choisi pour ce terminal part avec chaque demande."""

from __future__ import annotations

from ojarvis_shell.shell import cli, models
from ojarvis_shell.shell.display import Style
from tests.shell.fake_engine import DONE, START, FakeEngine, chats, session, sse


def test_a_model_is_found_by_its_short_or_full_name() -> None:
    assert models.find("wonder") is models.CHOICES[1]
    assert models.find("won") is models.CHOICES[1]
    assert models.find("Muse Wonder") is models.CHOICES[1]
    assert models.find("open") is models.CHOICES[2]
    assert models.find("gpt") is None
    assert models.find("") is None


def test_the_chosen_model_goes_with_every_request_until_auto() -> None:
    engine = FakeEngine([sse(START, DONE), sse(START, DONE), sse(START, DONE)])
    client, out = session(engine, [])
    style = Style(False)
    client.send("Bonjour.")
    assert cli._command(client, style, "/modele wonder")
    client.send("Écris un site.")
    assert cli._command(client, style, "/modele auto")
    client.send("Merci.")
    sent = [body.get("model") for body in chats(engine)]
    assert sent == [None, "meta/muse-wonder-1.0", None]
    text = out.getvalue()
    assert "Modèle : Muse Wonder 1.0" in text
    assert "Modèle : automatique (Muse Spark, Muse Spark Open en secours)" in text


def test_modele_alone_lists_the_meta_models_and_an_unknown_name_changes_nothing() -> None:
    client, out = session(FakeEngine([]), [])
    style = Style(False)
    assert cli._command(client, style, "/modele")
    assert cli._command(client, style, "/modele gpt")
    text = out.getvalue()
    assert "● spark" not in text  # rien de choisi : automatique
    assert "○ wonder" in text
    assert "/modele spark · wonder · open · auto" in text
    assert "Modèle inconnu : gpt." in text
    assert client.model is None
