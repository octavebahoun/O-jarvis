"""Retouches ciblées (modifier_fichier) et aperçus en diff, montrés avant validation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ojarvis.core.errors import NotFoundError
from ojarvis.tools.base import InvalidToolArgumentsError, Level, ToolContext
from ojarvis.tools.files import EditFile, WriteFile, unified_diff
from ojarvis.tools.guards import BlockedTargetError

CODE = "def moyenne(notes):\n    total = sum(notes)\n    return total / len(notes)\n"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home" / "projet"
    folder.mkdir(parents=True)
    (folder / "stats.py").write_text(CODE)
    return tmp_path / "home"


def context(home: Path) -> ToolContext:
    return ToolContext(http=httpx.AsyncClient(), secret=lambda _name: None, home=home)


def edit(**fields: object) -> dict[str, object]:
    return {"chemin": "~/projet/stats.py", **fields}


def test_the_preview_is_a_unified_diff(home: Path) -> None:
    tool = EditFile()
    args = tool.parse(
        edit(
            ancien="    return total / len(notes)",
            nouveau="    return total / len(notes) if notes else 0",
        )
    )
    assert tool.level == Level.AGIR
    assert tool.describe(args) == "Modifier le fichier ~/projet/stats.py (−1 / +1 lignes)"
    diff = tool.preview(args, home)
    assert diff is not None
    assert diff.splitlines()[:2] == ["--- a/~/projet/stats.py", "+++ b/~/projet/stats.py"]
    assert "-    return total / len(notes)" in diff
    assert "+    return total / len(notes) if notes else 0" in diff
    assert (home / "projet" / "stats.py").read_text() == CODE  # un aperçu ne change rien


async def test_the_edit_replaces_the_passage_and_reports_the_diff(home: Path) -> None:
    tool = EditFile()
    args = tool.parse(edit(ancien="sum(notes)", nouveau="sum(notes, 0.0)"))
    output = await tool.run(args, context(home))
    assert (home / "projet" / "stats.py").read_text() == CODE.replace(
        "sum(notes)", "sum(notes, 0.0)"
    )
    assert output.text.startswith("Fichier modifié : ~/projet/stats.py")
    assert "+    total = sum(notes, 0.0)" in output.text


def test_a_passage_that_is_missing_or_ambiguous_is_sent_back(home: Path) -> None:
    tool = EditFile()
    with pytest.raises(InvalidToolArgumentsError, match="introuvable"):
        tool.preview(tool.parse(edit(ancien="return 42", nouveau="return 0")), home)
    with pytest.raises(InvalidToolArgumentsError, match="3 fois"):
        tool.preview(tool.parse(edit(ancien="notes", nouveau="valeurs")), home)
    every = tool.preview(tool.parse(edit(ancien="notes", nouveau="valeurs", toutes=True)), home)
    assert every is not None
    assert every.count("+") >= 3


def test_secrets_and_missing_files_are_refused(home: Path) -> None:
    tool = EditFile()
    (home / ".ssh").mkdir()
    with pytest.raises(BlockedTargetError):
        tool.preview(tool.parse({"chemin": "~/.ssh/config", "ancien": "a", "nouveau": "b"}), home)
    with pytest.raises(NotFoundError):
        tool.preview(tool.parse(edit(chemin="~/projet/absent.py", ancien="a", nouveau="b")), home)


def test_writing_a_file_previews_its_diff_too(home: Path) -> None:
    tool = WriteFile()
    replaced = tool.preview(
        tool.parse({"chemin": "~/projet/stats.py", "contenu": "print('remplacé')\n"}), home
    )
    assert replaced is not None
    assert "-def moyenne(notes):" in replaced
    assert "+print('remplacé')" in replaced
    created = tool.preview(tool.parse({"chemin": "~/projet/neuf.py", "contenu": "x = 1\n"}), home)
    assert created is not None
    assert "+x = 1" in created


def test_a_long_diff_is_cut() -> None:
    before = "\n".join(f"ligne {n}" for n in range(3000))
    diff = unified_diff(before, "", "gros.txt")
    assert diff.endswith("[… diff coupé]")
    assert unified_diff("pareil", "pareil", "x") == "(aucun changement)"
