#!/usr/bin/env python3
"""Prépare les phrases de Laya pour `laya-train` : un fichier d'entraînement et un de test.

  python3 laya/preparer.py

Le test est mis de côté **par graine** : une graine choisie pour le test y part avec toutes
ses variantes, pour qu'aucune variante d'une phrase de test ne serve à l'entraînement.
On garde environ 10 % des graines de chaque réponse, et au moins deux.

Sorties, dans laya/entrainement/ :
  train.jsonl          l'entraînement (graines et variantes)
  test.jsonl           le test (graines et variantes jamais vues)
  test-graines.jsonl   le test réduit aux phrases écrites à la main
  questions.json       les 12 questions, telles que Jarvis les posera à Laya
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from varier import DECISIONS

LAYA = Path(__file__).resolve().parent
SORTIE = LAYA / "entrainement"
PART_TEST = 0.10
MIN_TEST = 2
GRAINE_ALEATOIRE = 0


def questions() -> dict[str, dict[str, Any]]:
    """Chaque décision devient une question à choix, avec nos définitions pour critères."""
    return {
        decision: {"type": "choice", "instructions": question, "criteria": dict(reponses)}
        for decision, (question, reponses) in DECISIONS.items()
    }


def etat(ligne: dict[str, Any]) -> str:
    """Ce que Laya lit. Pour la mise à jour, il lit le souvenir avec le message."""
    if "souvenir" in ligne:
        return f"Souvenir : {ligne['souvenir']}\nMessage : {ligne['texte']}"
    return str(ligne["texte"])


def lire(chemin: Path) -> list[dict[str, Any]]:
    if not chemin.exists():
        return []
    return [json.loads(x) for x in chemin.read_text(encoding="utf-8").splitlines() if x]


def graines_de_test(
    graines: list[dict[str, Any]], decision: str, hasard: random.Random
) -> set[int]:
    par_reponse: dict[str, list[int]] = {}
    for i, g in enumerate(graines):
        par_reponse.setdefault(g[decision], []).append(i)
    choisies: set[int] = set()
    for indices in par_reponse.values():
        n = max(MIN_TEST, round(len(indices) * PART_TEST))
        choisies.update(hasard.sample(indices, n))
    return choisies


def cas(decision: str, ligne: dict[str, Any], specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "state": etat(ligne),
        "questions": {decision: specs[decision]},
        "expected": {decision: ligne[decision]},
    }


def ecrire(chemin: Path, lignes: list[dict[str, Any]]) -> None:
    chemin.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lignes), encoding="utf-8"
    )


def main() -> None:
    hasard = random.Random(GRAINE_ALEATOIRE)  # noqa: S311 — un tirage reproductible, pas un secret
    specs = questions()
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    test_graines: list[dict[str, Any]] = []
    for decision in DECISIONS:
        graines = lire(LAYA / "phrases" / f"{decision}.jsonl")
        variantes = lire(LAYA / "variantes" / f"{decision}.jsonl")
        reponses = set(DECISIONS[decision][1])
        inconnues = {x[decision] for x in graines + variantes} - reponses
        if inconnues:
            raise SystemExit(f"{decision} : réponses inconnues {sorted(inconnues)}")
        a_tester = graines_de_test(graines, decision, hasard)
        for i, g in enumerate(graines):
            (test if i in a_tester else train).append(cas(decision, g, specs))
            if i in a_tester:
                test_graines.append(cas(decision, g, specs))
        for v in variantes:
            (test if v["graine"] in a_tester else train).append(cas(decision, v, specs))
        n_test = sum(1 for x in test if decision in x["expected"])
        n_train = sum(1 for x in train if decision in x["expected"])
        print(
            f"{decision:<13} {n_train:>5} entraînement  {n_test:>4} test  ({len(a_tester)} graines)"
        )
    hasard.shuffle(train)
    SORTIE.mkdir(exist_ok=True)
    ecrire(SORTIE / "train.jsonl", train)
    ecrire(SORTIE / "test.jsonl", test)
    ecrire(SORTIE / "test-graines.jsonl", test_graines)
    (SORTIE / "questions.json").write_text(
        json.dumps(specs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"total : {len(train)} entraînement, {len(test)} test, {len(test_graines)} graines de test"
    )


if __name__ == "__main__":
    main()
