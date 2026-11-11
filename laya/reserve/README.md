# Laya en réserve

Laya affiné est plus juste que les décideurs EmbeddingGemma (95 % contre 94 %, surtout sur la mise à jour), mais trop lent sans carte graphique : 6,8 s par message, contre 0,1 s. Jarvis utilise donc les décideurs, et Laya attend ici.

- **Idée pour plus tard** : un script d'installation qui propose Laya aux PC puissants (avec carte graphique), et garde les décideurs ailleurs.
- **Les poids** : sortie du notebook Kaggle privé `octavebahoun/laya-jarvis-affinage` (lancé le 10/11/2026, 11 min sur 2×T4). Une copie locale est dans `~/.cache/o-jarvis/models/laya-jarvis-v1/laya-jarvis` (647 Mo, avec `train_report.json`).
- **Les scores** sur les 755 phrases de test : 49 % avant l'affinage, 95 % après. Sur les 83 phrases écrites à la main : 43 %, puis 88 %.
- **La bibliothèque** : `pip install laya==0.4.30` (avec torch).
- **La vitesse** : `python laya/reserve/vitesse.py <dossier du modèle> laya/entrainement/questions.json 2 [cpu|cuda]`. Le dernier argument permet de mesurer sur une carte graphique.
- **Les pistes déjà essayées** : la version int8 n'est pas plus rapide et fait plus d'erreurs (écartée). Des critères de question plus courts rendent Laya 2,4 fois plus rapide, mais il faut le réentraîner.
- **Pour Jarvis** : Laya répond aux 12 questions de `entrainement/questions.json` en une lecture du message. Pour la mise à jour, il lit « Souvenir : … » puis « Message : … », comme dans `vitesse.py`.
