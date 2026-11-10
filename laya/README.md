# Laya : les phrases d'entraînement

Le Système 1 de Jarvis est Laya Multilingual, un petit modèle qui classe chaque message sans générer de texte. On l'affine sur ces phrases, puis on publie ses poids.

- Toutes les phrases sont **inventées** : aucune vraie conversation, puisque les poids seront publics.
- Il y a un fichier par décision, avec une ligne JSON par exemple : `{"texte": "...", "<décision>": "<réponse>"}`.
- Le fichier `mise_a_jour` ajoute un champ `"souvenir"` : c'est le souvenir le plus proche, trouvé par EmbeddingGemma.
- Laya ne fait que trier les messages. Le reste revient au code : les dates, le modèle disponible, les niveaux N1, N2 et N3, les confirmations.
- `variantes/` : environ 200 phrases par réponse, écrites une seule fois par Muse Wonder à partir des graines (`varier.py`, consigne dans `consigne-variantes.md`), puis relues. Les fichiers `.faites` notent les graines déjà traitées.
- `preparer.py` range graines et variantes dans `entrainement/` pour `laya-train` : environ 10 % des graines partent au test, avec toutes leurs variantes. Pour la mise à jour, Laya lit « Souvenir : … » puis « Message : … » : Jarvis devra lui envoyer exactement ce format.
- `kaggle/affinage.py` affine Laya Multilingual sur un GPU de Kaggle, puis le mesure sur le test. On l'envoie avec `kaggle datasets create -p laya/entrainement` puis `kaggle kernels push -p laya/kaggle`.

| Fichier | Décision | Réponses |
|---|---|---|
| `apprendre` | Apprendre ? | rien · preference · fait · correction |
| `sensible` | Sensible ? | ordinaire · personnel · secret |
| `bibliotheque` | Chercher dans la bibliothèque ? | oui · non |
| `modele_fort` | Proposer un modèle plus fort ? | non · proposer |
| `mise_a_jour` | Mettre à jour un souvenir ? | ajouter · remplacer · garder_les_deux · demander |
| `oublier` | Oublier une information ? | supprimer · garder · demander |
| `incertain` | Information incertaine ? | accepter · verifier · demander |
| `recuperer` | Récupérer un souvenir ? | oui · non |
| `resumer` | Résumer la conversation ? | oui · non |
| `duree` | Combien de temps ? | toujours · projet · quelques_jours |
| `action` | Quelle action ? | aucune · lire · creer_modifier · supprimer · clarification |
| `verifier` | Vérifier le résultat ? | oui · non |
