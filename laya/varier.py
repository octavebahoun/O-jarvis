#!/usr/bin/env python3
"""Multiplie les phrases de Laya. Pour chaque phrase écrite à la main (une « graine »), un
grand modèle écrit des variantes qui gardent la même réponse.

Les variantes ne s'écrivent qu'une fois, et le script reprend là où il s'est arrêté.

  python3 laya/varier.py                        toutes les décisions
  python3 laya/varier.py apprendre --limite 3   un essai sur 3 graines
  python3 laya/varier.py --voir apprendre       la consigne de la 1re graine, sans appel
  python3 laya/varier.py --bilan                le compte par réponse

La clé MODEL_API_KEY (Meta) vient de l'environnement, sinon du .env du dépôt.
Le script n'utilise que la bibliothèque standard de Python.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

LAYA = Path(__file__).resolve().parent
GRAINES = LAYA / "phrases"
SORTIE = LAYA / "variantes"
API = "https://api.meta.ai/v1/chat/completions"
MODELE = "muse-wonder-1.0"
#: Le nombre de phrases visé par réponse, graines comprises.
CIBLE = 200
#: Le plus de variantes demandées pour une seule graine.
PLAFOND = 12

#: Les mêmes motifs que le garde-fou du dépôt : une variante qui ressemble à une vraie clé
#: est écartée.
SECRETS = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|sk_car_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}|ntn_[A-Za-z0-9]{30,}|tvly-[A-Za-z0-9-]{20,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b\d{8,12}:AA[A-Za-z0-9_-]{30,}\b"
)

# Chaque décision : sa question, puis chaque réponse et ce qu'elle veut dire.
DECISIONS: dict[str, tuple[str, dict[str, str]]] = {
    "apprendre": (
        "Faut-il retenir quelque chose de ce message sur l'utilisateur ?",
        {
            "rien": "rien de durable sur lui : question, tâche, bavardage, épisode passé, consigne "
            "valable pour ce seul message, texte à traduire ou corriger, hypothèse, sarcasme",
            "preference": "un goût ou une façon de faire durable (« toujours », « je préfère », "
            "« j'aime pas »), même glissé dans une demande",
            "fait": "un fait durable sur lui ou sa vie : identité, métier, lieu, proches, "
            "matériel, projets, santé, rendez-vous à venir",
            "correction": "il corrige ou met à jour ce qu'il avait dit (« en fait », « plus », "
            "« maintenant », « pas X mais Y »)",
        },
    ),
    "sensible": (
        "Quel est le degré de sensibilité de ce que contient le message ?",
        {
            "ordinaire": "rien de sensible, même s'il parle de mots de passe ou de banque sans en "
            "donner",
            "personnel": "santé, argent, religion, politique, vie amoureuse, adresse, téléphone, "
            "date de naissance, numéro de sécu, passeport, permis ou fiscal",
            "secret": "ce qui ouvre un accès ou fait payer, donné en clair : mot de passe, PIN, "
            "CVV, numéro de carte, IBAN, numéro de compte, clé API, jeton, code reçu par SMS, "
            "phrase de récupération, code de porte",
        },
    ),
    "bibliotheque": (
        "Faut-il chercher dans les anciennes conversations avec l'assistant ?",
        {
            "oui": "la demande renvoie à un échange passé avec l'assistant (« tu m'avais dit », "
            "« reprends le script d'hier », « on en était où »)",
            "non": "demande nouvelle, suite de la conversation en cours, souvenir de sa vie (« la "
            "dernière fois à Paris »), ou question sur un fait de lui (« où j'habite ? »)",
        },
    ),
    "modele_fort": (
        "Faut-il proposer un modèle plus puissant pour cette demande ?",
        {
            "proposer": "tâche lourde : long raisonnement, architecture, preuve, bug rare, long "
            "document à analyser, plan sur des semaines, gros programme",
            "non": "tâche simple ou courte, même si le texte fourni est long (trier, convertir, "
            "traduire une phrase)",
        },
    ),
    "mise_a_jour": (
        "Comparé au souvenir le plus proche, que faire du message ?",
        {
            "ajouter": "le message apporte un fait ou un goût durable sur l'utilisateur, mais "
            "sur un autre sujet que le souvenir",
            "remplacer": "le message rend le souvenir faux (changement, correction)",
            "garder_les_deux": "même sujet, les deux restent vrais (« aussi », « au travail », "
            "« le dimanche »)",
            "demander": "impossible de savoir si le souvenir reste vrai (un nouvel emploi, sans "
            "dire s'il a quitté l'ancien)",
        },
    ),
    "oublier": (
        "Le message demande-t-il d'effacer quelque chose de la mémoire ?",
        {
            "supprimer": "demande d'oubli claire : on sait quoi effacer",
            "demander": "demande d'oubli floue (« oublie ça », « efface les trucs perso »)",
            "garder": "pas une demande d'oubli de la mémoire (« j'ai oublié mes clés », « n'oublie "
            "pas », supprimer un fichier)",
        },
    ),
    "incertain": (
        "L'information que donne l'utilisateur est-elle sûre ?",
        {
            "accepter": "affirmation nette",
            "verifier": "affirmation douteuse que l'assistant peut vérifier lui-même (fait "
            "général, web, ordinateur)",
            "demander": "doute que seul l'utilisateur peut lever (projet hésitant, « je crois "
            "que je suis allergique »)",
        },
    ),
    "recuperer": (
        "Faut-il rappeler ce que l'assistant sait de lui pour bien répondre ?",
        {
            "oui": "la bonne réponse dépend de lui : ville, goûts, allergies, matériel, proches, "
            "langage préféré",
            "non": "la réponse est la même pour tout le monde",
        },
    ),
    "resumer": (
        "Faut-il résumer la conversation jusqu'ici ?",
        {
            "oui": "changement de sujet, demande de récapitulatif, fin de séance",
            "non": "suite directe de ce qui précède",
        },
    ),
    "duree": (
        "Combien de temps garder cette information ?",
        {
            "toujours": "identité, goûts, proches, santé durable",
            "projet": "le temps d'un projet, d'études, d'une recherche, d'un entraînement",
            "quelques_jours": "état passager, séjour, rendez-vous daté",
        },
    ),
    "action": (
        "Quelle action sur le monde de l'utilisateur ce message demande-t-il ?",
        {
            "aucune": "juste répondre dans le chat (écrire un texte, expliquer comment faire)",
            "lire": "lire un fichier, un mail, l'agenda, une page, chercher sur le web",
            "creer_modifier": "créer, écrire dans un fichier, envoyer, déplacer, renommer, "
            "ajouter un événement",
            "supprimer": "effacer un fichier, un mail, un événement, désinstaller",
            "clarification": "une action est demandée, mais impossible sans précision "
            "(« supprime-le », « envoie le mail »)",
        },
    ),
    "verifier": (
        "Après l'action demandée, faut-il vérifier qu'elle a marché ?",
        {
            "oui": "action qui a un effet : envoyer, installer, supprimer, déplacer, lancer, "
            "déployer, programmer",
            "non": "simple réponse ou lecture",
        },
    ),
}

SYSTEME = """Tu écris des données d'entraînement pour Laya, un petit modèle qui classe les \
messages qu'un utilisateur envoie à son assistant personnel, Jarvis. Tout est inventé : jamais \
de vraie personne, de vraie adresse, de vrai numéro ni de vrai secret.

Règles :
- Chaque phrase est un message plausible d'un utilisateur à Jarvis, et sa réponse est \
exactement celle de la graine : aucune autre réponse ne doit pouvoir s'appliquer.
- Varie le fond (autres sujets, objets, prénoms, lieux), la longueur et le ton : poli, \
familier, sec, style SMS avec fautes et abréviations (jsuis, stp, tt, pk, mtn).
- Garde surtout la langue de la graine ; environ une phrase sur huit dans une autre langue \
(anglais, espagnol, portugais, allemand, italien…).
- Aucune phrase trop proche de la graine ou d'une autre phrase.
- Téléphones : numéros de fiction (01 99 …). Secrets inventés et courts : mot de passe simple, \
clé ou jeton tronqué (12 caractères au plus après le préfixe, comme sk-proj-4Fh2Kq).
"""
FORMAT_PHRASES = 'Réponds en JSON seul : {"phrases": ["…", "…"]}.'
FORMAT_PAIRES = (
    "Le message apporte toujours une information durable sur l'utilisateur, jamais une simple "
    "demande ni une question. Le souvenir est à la troisième personne (« L'utilisateur… », "
    "« The user… »), dans la langue du message. "
    'Réponds en JSON seul : {"paires": [{"souvenir": "…", "texte": "…"}]}.'
)


def cle() -> str:
    valeur = os.environ.get("MODEL_API_KEY", "").strip()
    env = LAYA.parent / ".env"
    if not valeur and env.exists():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            if ligne.startswith("MODEL_API_KEY="):
                valeur = ligne.split("=", 1)[1].strip().strip('"').strip("'")
    if not valeur:
        sys.exit("MODEL_API_KEY absente : ni dans l'environnement, ni dans le .env du dépôt.")
    return valeur


def lire(chemin: Path) -> list[dict[str, Any]]:
    if not chemin.exists():
        return []
    return [json.loads(ligne) for ligne in chemin.read_text(encoding="utf-8").splitlines() if ligne]


def empreinte(texte: str) -> str:
    """Deux phrases qui ne diffèrent que par la casse, les espaces ou la ponctuation finale."""
    return re.sub(r"[\s.!?…]+$", "", re.sub(r"\s+", " ", texte.casefold()).strip())


def combien(graines: list[dict[str, Any]], decision: str) -> dict[str, int]:
    """Les variantes à demander par graine, selon le nombre de graines de chaque réponse."""
    compte: dict[str, int] = {}
    for g in graines:
        compte[g[decision]] = compte.get(g[decision], 0) + 1
    return {r: max(2, min(PLAFOND, math.ceil((CIBLE - n) / n))) for r, n in compte.items()}


def consigne(decision: str, graine: dict[str, Any], n: int) -> list[dict[str, str]]:
    question, reponses = DECISIONS[decision]
    liste = "\n".join(f"- {r} : {sens}" for r, sens in reponses.items())
    reponse = graine[decision]
    paire = decision == "mise_a_jour"
    lignes = [f"Décision : {question}", f"Réponses possibles :\n{liste}", ""]
    if paire:
        lignes.append(f"Graine : souvenir « {graine['souvenir']} », message « {graine['texte']} »")
    else:
        lignes.append(f"Graine : « {graine['texte']} »")
    lignes += [
        f"Réponse de la graine : {reponse}",
        "",
        f"Écris {n} nouvelles {'paires' if paire else 'phrases'} dont la réponse est aussi "
        f"« {reponse} ».",
        FORMAT_PAIRES if paire else FORMAT_PHRASES,
    ]
    return [{"role": "system", "content": SYSTEME}, {"role": "user", "content": "\n".join(lignes)}]


def appeler(messages: list[dict[str, str]], n: int, args: argparse.Namespace, jeton: str) -> Any:
    corps = {
        "model": args.modele,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "reasoning_effort": args.effort,
        "temperature": 1.0,
        # La réflexion cachée de Meta compte dans la limite : 2 048 jetons de marge.
        "max_tokens": 2048 + 150 * n,
    }
    requete = urllib.request.Request(
        API,
        data=json.dumps(corps).encode(),
        headers={"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"},
    )
    for essai in range(4):
        try:
            with urllib.request.urlopen(requete, timeout=120) as reponse:  # noqa: S310 — https fixe
                contenu = json.load(reponse)["choices"][0]["message"]["content"]
            contenu = re.sub(r"^```(?:json)?\s*|\s*```$", "", contenu.strip())
            return json.loads(contenu)
        except urllib.error.HTTPError as erreur:
            if erreur.code not in (408, 429, 500, 502, 503, 504) or essai == 3:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            if essai == 3:
                raise
        time.sleep(2 ** (essai + 1))
    raise RuntimeError("inaccessible")


def nettoyer(
    brut: Any, decision: str, graine: dict[str, Any], i: int, vues: set[str]
) -> list[dict[str, Any]]:
    """Garde les variantes valables, avec la réponse de la graine.

    Écarte les doublons et ce qui ressemble à une vraie clé.
    """
    paire = decision == "mise_a_jour"
    elements = brut.get("paires" if paire else "phrases", []) if isinstance(brut, dict) else []
    sorties = []
    for e in elements:
        if paire:
            if not isinstance(e, dict):
                continue
            souvenir, texte = str(e.get("souvenir", "")).strip(), str(e.get("texte", "")).strip()
            cle_vue = empreinte(souvenir) + "|" + empreinte(texte)
            valable = bool(souvenir) and 2 <= len(texte) <= 400 and len(souvenir) <= 300
            ligne = {"souvenir": souvenir, "texte": texte}
        else:
            texte = str(e).strip()
            cle_vue = empreinte(texte)
            valable = 2 <= len(texte) <= 400
            ligne = {"texte": texte}
        if not valable or cle_vue in vues or SECRETS.search(json.dumps(ligne, ensure_ascii=False)):
            continue
        vues.add(cle_vue)
        sorties.append({**ligne, decision: graine[decision], "graine": i})
    return sorties


def varier(decision: str, args: argparse.Namespace, jeton: str) -> tuple[int, int]:
    graines = lire(GRAINES / f"{decision}.jsonl")
    chemin = SORTIE / f"{decision}.jsonl"
    #: Les graines déjà passées, même celles dont aucune variante n'a été gardée.
    suivi = SORTIE / f"{decision}.faites"
    deja = lire(chemin)
    faites = {int(x) for x in suivi.read_text().split()} if suivi.exists() else set()
    paire = decision == "mise_a_jour"

    def cle_de(ligne: dict[str, Any]) -> str:
        texte = empreinte(ligne["texte"])
        return empreinte(ligne["souvenir"]) + "|" + texte if paire else texte

    vues = {cle_de(x) for x in graines + deja}
    parts = combien(graines, decision)
    a_faire = [(i, g) for i, g in enumerate(graines) if i not in faites][: args.limite or None]
    ecrites = echecs = 0
    SORTIE.mkdir(exist_ok=True)
    with (
        ThreadPoolExecutor(args.fils) as pool,
        chemin.open("a", encoding="utf-8") as fichier,
        suivi.open("a", encoding="utf-8") as passees,
    ):
        travaux = {
            pool.submit(
                appeler, consigne(decision, g, parts[g[decision]]), parts[g[decision]], args, jeton
            ): (i, g)
            for i, g in a_faire
        }
        for travail in as_completed(travaux):
            i, g = travaux[travail]
            try:
                lignes = nettoyer(travail.result(), decision, g, i, vues)
            except Exception as erreur:  # une graine ratée sera reprise à la relance
                echecs += 1
                print(f"  {decision} #{i} : échec ({erreur})", file=sys.stderr)
                continue
            fichier.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in lignes)
            fichier.flush()
            passees.write(f"{i}\n")
            passees.flush()
            ecrites += len(lignes)
    return ecrites, echecs


def bilan() -> None:
    for decision in DECISIONS:
        graines = lire(GRAINES / f"{decision}.jsonl")
        variantes = lire(SORTIE / f"{decision}.jsonl")
        print(decision)
        for reponse in DECISIONS[decision][1]:
            n_g = sum(1 for g in graines if g[decision] == reponse)
            n_v = sum(1 for v in variantes if v[decision] == reponse)
            print(f"  {reponse:<16} {n_g:>4} graines  {n_v:>5} variantes  = {n_g + n_v}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("decisions", nargs="*", help="par défaut : les 12")
    parser.add_argument("--limite", type=int, default=0, help="graines par décision (essai)")
    parser.add_argument("--fils", type=int, default=4, help="appels en même temps")
    parser.add_argument("--modele", default=MODELE)
    parser.add_argument("--effort", default="minimal", help="réflexion de Meta : minimal ou high")
    parser.add_argument("--voir", metavar="DECISION", help="affiche une consigne, sans appel")
    parser.add_argument("--bilan", action="store_true")
    args = parser.parse_args()

    if args.bilan:
        bilan()
        return
    if args.voir:
        graines = lire(GRAINES / f"{args.voir}.jsonl")
        n = combien(graines, args.voir)[graines[0][args.voir]]
        for m in consigne(args.voir, graines[0], n):
            print(f"--- {m['role']}\n{m['content']}")
        return
    inconnues = [d for d in args.decisions if d not in DECISIONS]
    if inconnues:
        sys.exit(f"Décisions inconnues : {', '.join(inconnues)}")
    jeton = cle()
    for decision in args.decisions or list(DECISIONS):
        ecrites, echecs = varier(decision, args, jeton)
        fin = f", {echecs} graines ratées (relancer pour les reprendre)" if echecs else ""
        print(f"{decision} : {ecrites} variantes écrites{fin}")


if __name__ == "__main__":
    main()
