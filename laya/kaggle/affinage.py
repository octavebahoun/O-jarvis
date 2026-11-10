"""Affine Laya Multilingual sur les phrases de Jarvis, sur un GPU de Kaggle.

Lit le jeu de données `laya-jarvis-phrases` (fait par laya/preparer.py), entraîne, puis
mesure Laya sur les phrases de test, qu'il n'a jamais vues. Tout ce qui sort est dans
/kaggle/working : le modèle affiné (laya-jarvis/), rapport.json et erreurs.json.
"""

import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "laya==0.4.30"], check=True)

import json  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from pathlib import Path  # noqa: E402

import laya  # noqa: E402
import torch  # noqa: E402
from laya.train import TrainConfig, finetune, resolve_checkpoint_dir  # noqa: E402

DONNEES = next(p.parent for p in Path("/kaggle/input").rglob("train.jsonl"))
SORTIE = Path("/kaggle/working")
MODELE = SORTIE / "laya-jarvis"

print("GPU :", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "aucun")
print("données :", DONNEES)


def lire(nom: str) -> list[dict]:
    return [json.loads(x) for x in (DONNEES / nom).read_text(encoding="utf-8").splitlines() if x]


def mesurer(agent, lignes: list[dict]) -> tuple[dict, list[dict]]:
    """Le score par décision, et chaque erreur avec la phrase, la réponse attendue et donnée."""
    par_decision: dict[str, list[dict]] = defaultdict(list)
    for ligne in lignes:
        par_decision[next(iter(ligne["expected"]))].append(ligne)
    scores, erreurs = {}, []
    for decision, rangs in par_decision.items():
        questions = rangs[0]["questions"]
        sorties = agent.predict_batch([r["state"] for r in rangs], questions, batch_size=32)
        justes = 0
        confusion: Counter = Counter()
        for r, s in zip(rangs, sorties, strict=True):
            attendu = r["expected"][decision]
            donne = s.get("answers", s)[decision]["choice"]
            confusion[f"{attendu} → {donne}"] += 1
            if donne == attendu:
                justes += 1
            else:
                erreurs.append(
                    {"decision": decision, "phrase": r["state"], "attendu": attendu, "donne": donne}
                )
        scores[decision] = {
            "justes": justes,
            "total": len(rangs),
            "score": round(justes / len(rangs), 3),
            "confusion": dict(confusion),
        }
    total = sum(s["justes"] for s in scores.values()), sum(s["total"] for s in scores.values())
    scores["ensemble"] = {
        "justes": total[0],
        "total": total[1],
        "score": round(total[0] / total[1], 3),
    }
    return scores, erreurs


base = resolve_checkpoint_dir("multilingual")
print("base :", base)
debut = time.time()
resume = finetune(
    data=str(DONNEES / "train.jsonl"),
    model_dir=base,
    output_dir=str(MODELE),
    # L'ordre des réponses est mélangé à l'entraînement : Laya ne doit pas apprendre une place.
    config=TrainConfig(eval_data=str(DONNEES / "test.jsonl"), shuffle_options=("choice",)),
    device="cuda" if torch.cuda.is_available() else "cpu",
)
duree = round((time.time() - debut) / 60, 1)
shutil.rmtree(MODELE / "checkpoint_latest", ignore_errors=True)  # une copie du modèle final
print(f"entraînement fini en {duree} min")

agent = laya.load(str(MODELE))
avant = laya.load(base)
rapport = {"duree_min": duree, "resume_laya": resume}
erreurs = {}
for nom in ("test.jsonl", "test-graines.jsonl"):
    lignes = lire(nom)
    rapport[f"avant {nom}"], _ = mesurer(avant, lignes)
    rapport[f"apres {nom}"], erreurs[nom] = mesurer(agent, lignes)
    print(nom, "avant", rapport[f"avant {nom}"]["ensemble"])
    print(nom, "après", rapport[f"apres {nom}"]["ensemble"])

(SORTIE / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=2, default=str))
(SORTIE / "erreurs.json").write_text(json.dumps(erreurs, ensure_ascii=False, indent=2))
print(
    json.dumps(
        {k: v for k, v in rapport.items() if k.startswith("apres")}, ensure_ascii=False, indent=1
    )[:4000]
)
