"""Vitesse de Laya affiné sur ce PC : chargement, mémoire vive, temps par message.

Usage : python laya/reserve/vitesse.py <dossier du modèle> <questions.json> <fils> [cpu|cuda]

Mesuré le 10/11/2026 sur un PC sans carte graphique (2 fils) : 6,8 s par message pour les
11 questions, 1,5 Go de mémoire vive, 7 à 16 s de chargement.
"""

import json
import statistics
import sys
import time
from pathlib import Path

import torch

DOSSIER, QUESTIONS, FILS = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
APPAREIL = sys.argv[4] if len(sys.argv) > 4 else "cpu"
torch.set_num_threads(FILS)

import laya  # noqa: E402  (après le réglage des fils)


def rss_mo() -> float:
    for ligne in Path("/proc/self/status").read_text().splitlines():
        if ligne.startswith("VmRSS:"):
            return int(ligne.split()[1]) / 1024
    return 0.0


base = rss_mo()
t0 = time.perf_counter()
agent = laya.load(str(DOSSIER), device=APPAREIL)
chargement = time.perf_counter() - t0
toutes = json.loads(QUESTIONS.read_text())
onze = {k: v for k, v in toutes.items() if k != "mise_a_jour"}
maj = {"mise_a_jour": toutes["mise_a_jour"]}

COURT = "jsuis passé sur fedora, tu peux m'aider à installer docker ?"
LONG = (
    "Bon alors voilà, j'ai un souci avec mon projet de site e-commerce : depuis que j'ai mis "
    "à jour les dépendances, le paiement Stripe plante au moment de la validation du panier, "
    "et je ne comprends pas pourquoi, parce que les tests passent en local. Je préfère qu'on "
    "regarde ça ensemble pas à pas, avec des explications courtes, je suis un peu fatigué ce "
    "soir. Au passage, j'ai changé de PC la semaine dernière, j'ai maintenant 16 Go de RAM."
)
# Pour la mise à jour, Laya lit le souvenir le plus proche puis le message, dans ce format.
PAIRE = "Souvenir : L'utilisateur est sous Ubuntu.\nMessage : jsuis passé sur fedora"


def chrono(etat: str, questions: dict, n: int = 8) -> tuple[float, dict]:
    for _ in range(2):
        agent.predict(etat, questions)
    temps = []
    for _ in range(n):
        t = time.perf_counter()
        sortie = agent.predict(etat, questions)
        temps.append((time.perf_counter() - t) * 1000)
    return statistics.median(temps), sortie


resultats: dict[str, object] = {
    "fils": FILS,
    "appareil": APPAREIL,
    "chargement_s": round(chargement, 1),
}
for nom, etat, questions in (
    ("11 questions, message court", COURT, onze),
    ("11 questions, message long", LONG, onze),
    ("mise à jour (1 question)", PAIRE, maj),
):
    ms, sortie = chrono(etat, questions)
    resultats[nom] = round(ms)
    reponses = {q: r.get("choice") for q, r in sortie["answers"].items()}
    print(f"{nom} : {ms:.0f} ms → {reponses}", flush=True)
resultats["ram_mo"] = round(rss_mo() - base)
print(json.dumps(resultats, ensure_ascii=False))
