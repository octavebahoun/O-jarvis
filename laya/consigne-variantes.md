# Consigne : écrire les variantes de Laya

Tu travailles dans le dépôt O-jarvis, sur la branche `feat/laya-phrases`. Ne change pas de branche.

## Le but

Le dossier `laya/phrases/` contient 798 phrases écrites à la main, qu'on appelle les **graines**. Elles servent à entraîner Laya, un petit modèle qui classe les messages. Le script `laya/varier.py` demande à Muse Wonder des variantes de chaque graine, avec la même réponse, jusqu'à environ 200 phrases par réponse. Lis d'abord `laya/README.md`.

## Les étapes

1. **Essai.** Lance `python3 laya/varier.py apprendre --limite 2`, puis montre à Octave ce qu'il y a dans `laya/variantes/apprendre.jsonl`. **Attends son accord** avant de continuer.
2. **Tout.** Lance `python3 laya/varier.py`. Cela fait environ 800 appels à Muse Wonder. Si des graines échouent, relance la même commande : le script reprend là où il s'est arrêté.
3. **Bilan.** Lance `python3 laya/varier.py --bilan` et donne le résultat à Octave.

## Les règles

- Ne modifie **jamais** `laya/phrases/` : ce sont les graines écrites à la main.
- Ne modifie pas `laya/varier.py`. Si tu trouves un bug, explique-le à Octave et attends sa réponse.
- N'écris pas de variantes toi-même : c'est le script qui les produit.
- Si une variante te semble avoir une mauvaise réponse, ne la corrige pas. Note-la dans `laya/variantes/doutes.md`, avec le fichier, la phrase et la raison.
- N'affiche jamais la valeur de `MODEL_API_KEY`. Le script la lit tout seul, dans l'environnement ou dans le `.env`.
- Ne fais ni commit ni push.
