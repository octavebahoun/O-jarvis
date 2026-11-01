# Changements

Format inspiré de « Keep a Changelog ». Le détail de chaque évolution est dans [`docs/journal/`](./docs/journal/README.md).

## Non publié

## 1.2.1 — 1er novembre 2026

### Modifié
- Nouveau design du mode shell : un accueil (dossier et branche git, modèles Meta, commandes), l'invite « dossier branche ❯ », « ◆ Jarvis » et son modèle en tête de chaque demande, la réponse en retrait coupée aux mots (gras, code et blocs de code rendus), les plans en cartes avec un badge de niveau, la durée et le coût de chaque demande.

### Sécurité
- Le classement des commandes du mode shell ne prend plus pour des lectures des écritures ou du code lancé : `find -exec`, `ip netns exec`, `sort -o`, `uniq` ou `xxd` vers un fichier, les lanceurs (`setsid`, `flock`, `taskset`, `watch`, `script`…), les programmes du dossier courant, `git switch -f`, `git rm`, `git branch -D` ; une recherche récursive trop profonde pour être vérifiée demande ta validation.
- « Toujours pour cette session » ne s'applique plus à une installation de paquets (`npm install`, `pip install`, `cargo install`…).
- Depuis Telegram ou une automatisation, Jarvis ne lit plus tes onglets de Chrome et n'y agit plus.

## 1.2.0 — 1er novembre 2026

Le travail du Meta Global AI Developer Hackathon (détail dans [`HACKATHON.md`](./HACKATHON.md)).

### Ajouté
- Profil « Tout Meta », par défaut : rien que des modèles de Meta (Muse Spark, Muse Spark Open, Muse Wonder, Muse Voice Transcribe, voix MMS, SAM 3.1), secours compris.
- Mode shell : Jarvis dans le terminal (`o-jarvis-shell`), avec la voix (`/voix`), les plans validés, les mesures avant/après et le partage d'écran (`/partage`).
- Voir Jarvis agir : le navigateur de Jarvis filmé dans l'app, bouton « Arrêter ».
- Clics dans les applications du bureau : SAM 3.1 encadre, Muse Spark choisit et vérifie, le portail de GNOME clique.
- Partage d'écran en direct et extraits filmés, regardés par Muse Spark.
- Jarvis dans Chrome : panneau latéral, lecture de l'onglet, actions validées ou « sans demander » par site, secours visuel avec SAM 3.1, extension branchée en arrière-plan pour le terminal, l'app et la voix.
- Jarvis répond dans la langue de la question ; voix MMS anglaise de Meta.
- Avatar « masque cybernétique » et écran de démarrage qui attend le moteur (merci Dxne-Dev).

### Corrigé
- Un message sans mot (« ok ») ne fait plus échouer la recherche par le sens : Jarvis ne répondait plus du tout.
- L'app, le terminal et le panneau de Chrome se partagent un seul moteur, et rejoignent celui qui tourne quand le leur s'arrête.

## 1.1.0 — 24 octobre 2026

### Ajouté
- Suite de 44 scénarios par niveaux (`engine/tools/scenarios.py`), joués avec le vrai modèle ; résultats dans `docs/scenarios.md`.
- Avec le contrôle du PC, Jarvis ouvre une adresse web dans ton navigateur (une pull request, par exemple), après ta validation. Seules les adresses http et https passent.
- Réglages › Modèles locaux : pour une connexion faible, chaque modèle se télécharge à la main dans le navigateur puis s'importe ; Jarvis reconnaît chaque fichier à son empreinte, même renommé (#43).
- En ligne, l'écran est lu par la vision de DeepSeek (`deepseek-v4-flash-vision-exp`, environ 1 s), avant OpenRouter ; la lecture locale reste le choix par défaut.

### Corrigé
- Un téléchargement de modèle coupé reprend là où il s'était arrêté, avec plusieurs essais, au lieu de repartir de zéro (#43).
- Sur Ubuntu 24.04, la voix démarre : si PortAudio ne voit pas le micro anti-écho ou le micro choisi, Jarvis prend le micro par défaut au lieu de planter. L'erreur de la voix dit aussi sa vraie cause.
- Jarvis ne demande plus « tu confirmes ? » avant de proposer un plan : le plan est la confirmation.
- Un plan annoncé mais jamais proposé est maintenant proposé pour de vrai.
- Les chemins que Jarvis lit commencent par `~/` : plus de confusion, et ton nom d'utilisateur ne part plus vers le modèle.
- Le compte rendu d'un plan est dans la conversation dès que le plan est fini.
- L'identifiant d'une automatisation est donné dès sa création.
- Lister un dossier qui n'existe pas encore n'arrête plus un plan.
- Le compte rendu d'un plan ne met plus en doute ce que Jarvis vient de faire, et il ne s'affiche plus deux fois.
- À chaque tour, Jarvis lit l'état réel des plans de la conversation : il ne nie plus une action faite.
- Lancé depuis le dépôt, le moteur lit le `.env` du dépôt : une clé ajoutée après coup (Groq) arrive au trousseau.
- Sur Telegram, un vocal sans clé Groq dit où l'ajouter ; un refus de capture d'écran dit qu'il vient de GNOME.
- Un outil d'intégration au nom inventé (GitHub, Notion…) est refusé tout de suite, avec la liste des vrais noms, au lieu d'entrer dans un plan qui échoue.

## 1.0.0 — 16 octobre 2026

Première version complète.

### Parler à Jarvis
- Fenêtre avec la sphère animée (6 états), conversation en flux, historique cherchable, markdown.
- Voix : « Hey Jarvis » reconnu en local, transcription Groq (français imposé) avec relais, voix Cartesia ou Piper, interruption en parlant, relance sans mot de réveil, sous-titres ; réponse parlée en moins de 2 s (médiane mesurée).
- Mémoire : faits, préférences et profil visibles et modifiables ; recherche par le sens et par les mots ; moteur de contexte avec budget de jetons.
- Routeur de modèles : DeepSeek, OpenRouter, Ollama, OpenAI, Anthropic ; bascule signalée ; modèle imposé par tâche ; données sensibles en local.

### Jarvis agit
- Outils à schéma strict et trois niveaux de permission appliqués par une seule porte ; plans visibles, validés (double confirmation au niveau 3), exécutés étape par étape.
- Journal des actions immuable, chaîné par SHA-256, vérifié au démarrage.
- Navigateur de Jarvis (Playwright) derrière un proxy de garde ; sessions par domaine, chiffrées, révocables.
- Défenses contre l'injection : contenu extérieur encadré comme donnée, plans signalés, adresses fabriquées validées.

### Jarvis s'organise
- Automatisations (heure locale, changements d'heure testés, ou événement) ; workflows persistés qui reprennent après un arrêt.
- Telegram (texte et vocal), intégrations MCP officielles (GitHub, Notion, Google Agenda), briefing du matin, notifications.

### Jarvis contrôle le PC
- Désactivé par défaut : ouvrir et fermer des applications, ranger des fichiers, « regarde mon écran » (portail Wayland, lu en local par défaut).

### Jarvis s'étend
- Skills écrits par Jarvis, testés en bac à sable, activés après double confirmation.
- Registre MCP officiel : chercher, essayer isolé, installer, désactiver.
- Agents dev, recherche et coach, délégués par un orchestrateur ; mémoire graphe ; suggestions (demandes répétées, projets délaissés) avec score de confiance.

### Release
- Paquets `.deb` et AppImage, moteur Python embarqué (aucune installation de Python nécessaire).
- Démarrage automatique à l'ouverture de session (désactivable), discret dans la barre système.
- Chromium du navigateur de Jarvis téléchargé au premier usage.
- Un démarrage lent (trousseau à déverrouiller, machine chargée) n'est plus un échec : l'app se connecte dès que le moteur est prêt.
- Les bulles d'information disparaissent après 8 s et s'affichent en haut, loin de la zone de saisie.
