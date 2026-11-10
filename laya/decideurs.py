#!/usr/bin/env python3
"""Les décideurs du Système 1 : une petite régression par décision, sur le vecteur EmbeddingGemma
du message. Une seule lecture du message pour toutes les décisions du chat.

  cd engine && uv run python ../laya/decideurs.py [--prefixe classification|recherche]

« recherche » réutilise le vecteur que Jarvis calcule déjà pour chercher dans la mémoire :
aucune lecture de plus. « classification » est le préfixe prévu par le modèle pour classer.

Pour « Mettre à jour un souvenir ? », le souvenir et le message ont chacun leur vecteur ; le
décideur lit les deux, leur produit et leur écart.

Les vecteurs sont gardés dans ~/.cache/o-jarvis/decideurs/ : relancer ne relit que les phrases
nouvelles. Les scores vont dans le même dossier ; les poids, par défaut aussi, ou dans le moteur
avec `--sortie engine/src/ojarvis/systeme1/decideurs-eg2.npz` (préfixe « recherche » seulement :
c'est le vecteur que le moteur calcule déjà).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from ojarvis.memory.embeddings import GEMMA_FOLDER, PREFIXES, GemmaEmbedder

LAYA = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache" / "o-jarvis" / "decideurs"
MODELE = Path.home() / ".cache" / "o-jarvis" / "models" / GEMMA_FOLDER
#: Les décisions du chat ; « action » et « verifier » attendent les actions.
CHAT = (
    "apprendre", "sensible", "bibliotheque", "modele_fort", "mise_a_jour",
    "oublier", "incertain", "recuperer", "resumer", "duree",
)
PREFIXE = {"classification": "task: classification | query: ", "recherche": PREFIXES["query"]}


def lire(nom: str) -> list[dict]:
    chemin = LAYA / "entrainement" / nom
    return [json.loads(x) for x in chemin.read_text(encoding="utf-8").splitlines() if x]


def morceaux(ligne: dict) -> list[str]:
    """Les textes à vectoriser : le message, ou le souvenir puis le message."""
    etat = ligne["state"]
    if etat.startswith("Souvenir : ") and "\nMessage : " in etat:
        souvenir, message = etat.removeprefix("Souvenir : ").split("\nMessage : ", 1)
        return [souvenir, message]
    return [etat]


class Vecteurs:
    """Les vecteurs déjà calculés, sur disque, par texte."""

    def __init__(self, prefixe: str) -> None:
        self.prefixe = PREFIXE[prefixe]
        self.chemin = CACHE / f"vecteurs-{prefixe}.npz"
        self.table: dict[str, np.ndarray] = {}
        if self.chemin.exists():
            donnees = np.load(self.chemin)
            self.table = dict(zip(donnees["cles"].tolist(), donnees["vecteurs"], strict=True))
        self.modele: GemmaEmbedder | None = None

    @staticmethod
    def cle(texte: str) -> str:
        return hashlib.sha256(texte.encode()).hexdigest()[:24]

    def completer(self, textes: set[str]) -> None:
        manquants = sorted((t for t in textes if self.cle(t) not in self.table), key=len)
        if not manquants:
            return
        if self.modele is None:
            self.modele = GemmaEmbedder(MODELE)
            self.modele._load()
        debut = time.perf_counter()
        # Par longueur : un lot de phrases de même taille perd moins de temps en remplissage.
        for i in range(0, len(manquants), 16):
            lot = manquants[i : i + 16]
            for texte, v in zip(lot, self.modele._run([self.prefixe + t for t in lot]), strict=True):
                self.table[self.cle(texte)] = v
            if i % 800 == 0:
                print(f"  {i}/{len(manquants)} vectorisés", flush=True)
        print(f"{len(manquants)} textes vectorisés en {time.perf_counter() - debut:.0f} s", flush=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        cles = list(self.table)
        np.savez(self.chemin, cles=np.array(cles), vecteurs=np.stack([self.table[c] for c in cles]))

    def traits(self, ligne: dict) -> np.ndarray:
        v = [self.table[self.cle(t)] for t in morceaux(ligne)]
        if len(v) == 1:
            return v[0]
        s, m = v
        return np.concatenate([s, m, s * m, np.abs(s - m)])


def entrainer(x: np.ndarray, y: np.ndarray, k: int, pas: int = 600, l2: float = 1e-3) -> np.ndarray:
    """Régression logistique multiclasse, biais compris (descente de gradient)."""
    x = np.hstack([x, np.ones((len(x), 1), dtype=np.float32)])
    w = np.zeros((x.shape[1], k), dtype=np.float32)
    cible = np.eye(k, dtype=np.float32)[y]
    for _ in range(pas):
        z = x @ w
        p = np.exp(z - z.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        w -= 2.0 * (x.T @ (p - cible) / len(x) + l2 * w)
    return w


def predire(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    return (np.hstack([x, np.ones((len(x), 1), dtype=np.float32)]) @ w).argmax(1)


def par_decision(lignes: list[dict]) -> dict[str, list[dict]]:
    d: dict[str, list[dict]] = defaultdict(list)
    for r in lignes:
        decision = next(iter(r["expected"]))
        if decision in CHAT:
            d[decision].append(r)
    return d


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prefixe", choices=sorted(PREFIXE), default="recherche")
    parser.add_argument("--sortie", type=Path, help="où écrire les poids (.npz)")
    args = parser.parse_args()

    jeux = {n: par_decision(lire(f"{n}.jsonl")) for n in ("train", "test", "test-graines")}
    vecteurs = Vecteurs(args.prefixe)
    vecteurs.completer({t for jeu in jeux.values() for rs in jeu.values() for r in rs for t in morceaux(r)})

    # Le modèle et le préfixe des vecteurs appris : le moteur refuse des poids d'un autre modèle.
    poids = {"_modele": np.array(GemmaEmbedder.model_id), "_prefixe": np.array(PREFIXE[args.prefixe])}
    rapport = {"prefixe": args.prefixe}
    totaux = {"test": [0, 0], "test-graines": [0, 0]}
    for decision in CHAT:
        lignes = jeux["train"][decision]
        reponses = sorted({r["expected"][decision] for r in lignes})
        x = np.stack([vecteurs.traits(r) for r in lignes])
        y = np.array([reponses.index(r["expected"][decision]) for r in lignes])
        w = entrainer(x, y, len(reponses))
        poids[decision] = w.astype(np.float32)
        poids[f"{decision}.reponses"] = np.array(reponses)
        ligne = []
        for nom in ("test", "test-graines"):
            rs = jeux[nom][decision]
            p = predire(w, np.stack([vecteurs.traits(r) for r in rs]))
            justes = sum(reponses[i] == r["expected"][decision] for i, r in zip(p, rs, strict=True))
            totaux[nom][0] += justes
            totaux[nom][1] += len(rs)
            rapport[f"{decision} {nom}"] = f"{justes}/{len(rs)}"
            ligne.append(f"{nom} {justes}/{len(rs)}")
        print(f"  {decision:<13} " + "  ".join(ligne), flush=True)
    for nom, (j, n) in totaux.items():
        rapport[f"ensemble {nom}"] = round(j / n, 3)
        print(f"ensemble {nom} : {j}/{n} = {j / n:.1%}")
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(args.sortie or CACHE / f"decideurs-{args.prefixe}.npz", **poids)
    (CACHE / f"rapport-{args.prefixe}.json").write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
