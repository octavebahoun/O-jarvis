# Décisions


## Machine (vérifiée le 14/10/2026)

- **Ubuntu 26.04.1 LTS, GNOME 50, Wayland uniquement** — GNOME 50 n'a plus de session X11. Le chemin X11 du code se teste sous Xvfb.
- **i5-4200M (2 cœurs, 4 threads, AVX2), 7,2 Go de RAM, Intel HD 4600 (pas de VRAM dédiée)** — machine modeste : tout ce qui tourne en local doit rester léger (pas de PyTorch, modèles quantifiés, chargement à la demande).
- **Docker 29.1.3, utilisateur dans le groupe `docker`** — sandbox de code avec Docker ; bubblewrap en repli.
- **Fuseau `Europe/Paris`** — stockage en UTC, affichage et planification dans le fuseau du système.
- **`/tmp` est un tmpfs plein à 81 %** (cache laissé par un autre programme) — fichiers temporaires lourds dans `~/.cache/o-jarvis/tmp`.

## Architecture

- **Deux processus : app Tauri v2 (Rust + React/TypeScript/Vite) et moteur Python (FastAPI)** — imposé par le brief. Pas de microservices.
- **Le moteur écoute `127.0.0.1` sur un port libre choisi par lui-même** — il annonce son port sur sa sortie standard ; aucune course sur un port fixe.
- **Jeton aléatoire par lancement, transmis par l'entrée standard** — invisible dans `/proc/<pid>/environ`. Le moteur s'arrête quand l'entrée standard se ferme : si l'app meurt, il meurt aussi.
- **Monorepo** : `app/` (Tauri + React), `engine/` (Python), `docs/`, `scripts/`.
- **Identifiants de code en anglais, interface, docs et commits en français** — convention lisible par tous les outils, produit en français.
- **Le moteur se lance depuis l'environnement Python du dépôt** (`engine/.venv`) en développement et pour le lanceur « un clic » ; un moteur empaqueté (PyInstaller) prendra la priorité en phase 7. Ordre de recherche : empaqueté, `OJARVIS_ENGINE_DIR`, sources.
- **WebSocket authentifiée par son premier message** — un navigateur ne peut pas poser d'en-tête sur une WebSocket ; l'origine est aussi vérifiée.
- **Fermer la fenêtre arrête tout** (le brief) ; la barre système offre « Ouvrir » et « Quitter » ; `Ctrl+Q` quitte.

## Moteur Python

- **Système 1 : une régression logistique par décision sur le vecteur EmbeddingGemma du message** (10/11/2026) — appris sur 7 100 phrases inventées (`laya/`) : 94 % de bonnes réponses sur 590 phrases de test jamais vues, 88 % sur celles écrites à la main, en une milliseconde, sans autre lecture du message que celle de la mémoire. Laya Multilingual (322M) affiné sur Kaggle faisait 95 % et 90 %, mais relisait le message pour chaque question : 6,8 s par message sur un i5 de 2015, 1,5 Go de mémoire ; gardé en réserve. Les poids ne valent que pour EmbeddingGemma 2 : avec un autre modèle (le hachage des tests), le Système 1 se tait et Jarvis fait comme avant.
- **Python 3.12 géré par uv** — faster-whisper et onnxruntime ne sont pas fiables sur le Python 3.14 du système.
- **Qualité** : ruff (lint + format), mypy strict, pytest, couverture ≥ 80 % sur le métier.
- **SQLite unique (mode WAL) avec SQLAlchemy 2 et Alembic** — un seul fichier pour le relationnel, les vecteurs et le graphe. Migrations versionnées dès le premier schéma, lancées une seule fois au démarrage du moteur (leçon de l'ancien : course entre deux processus).
- **Vecteurs : extension `sqlite-vec`** — maintenue, dans la même base, mêmes transactions. Repli automatique en recherche brute NumPy si l'extension ne charge pas.
- **Graphe : tables `entities` et `relations` dans SQLite, requêtes récursives** — Kùzu, la base graphe embarquée la plus connue, est archivée depuis 2025.
- **Recherche plein texte : FTS5** — combinée aux vecteurs pour une recherche hybride.
- **Embeddings locaux : EmbeddingGemma 2, partie texte, en ONNX q4** (10/11/2026, à la place de `multilingual-e5-small`) — banc d'essai en français sur un i5 de 2015 (21 questions, 20 souvenirs inventés) : 18/21 bonnes réponses en tête contre 12/21 pour e5, 110 ms par question, 470 Mo de mémoire vive, 207 Mo à télécharger ; Qwen3-Embedding 0.6B : 17/21 mais 1,3 Go. e5 lisait 256 jetons au plus, notait tout entre 0,7 et 1, et un long texte arrivait en tête de presque toutes les recherches. Utilisé quel que soit le profil, « Tout Meta » compris (le hachage de mots qu'il y utilisait : 6/21). Tourne sur le CPU sans PyTorch. Les seuils qui dépendent du modèle (pertinence, doublon, demande répétée, poids des mots) sont mesurés pour lui et réunis dans `ojarvis.memory.calibration`.
- **Bus d'événements maison, asynchrone, dans le processus** — une centaine de lignes, typé, testable ; chaque événement est aussi écrit en base pour les automatisations et les traces.
- **Workflows : machine à états persistée en base** — une chaîne reprend à la dernière étape terminée après un crash. Pas de file externe.
- **Pas de LangChain** — adaptateurs fins écrits ici avec les SDK officiels `openai` (DeepSeek, OpenRouter, OpenAI, Ollama via leur API compatible) et `anthropic`. Plus simple à lire et à tester.
- **Sorties structurées** — appel d'outils natif, sinon mode JSON ; validation Pydantic ; parse tolérant (`raw_decode`, puis réparation) ; un seul nouvel essai avec l'erreur ; puis échec propre.
- **Un seul appel LLM par tour** — le modèle répond ou appelle des outils (dont « proposer un plan » et « créer une automatisation ») dans le même appel. L'ancien enchaînait intention, plan puis réponse : trop lent pour la voix.
- **Logs structurés (structlog, JSON) avec ID de corrélation** — et un filtre qui masque toute valeur secrète connue.
- **Client HTTP : `httpx`** — `httpx2` (successeur annoncé par le client de test de Starlette) a été écarté : paquet récent au nom proche de `httpx`, risque d'imitation ; l'avertissement de dépréciation est filtré dans les tests (14/10/2026). Il arrive quand même comme dépendance des SDK officiels `anthropic` 1.x et `huggingface-hub` ; notre code n'utilise que `httpx` (15/10/2026).
- **Flux d'événements WebSocket : groupe de tâches anyio** — avec `asyncio.gather`, l'annulation venue de l'extérieur n'était pas toujours reconnue et faisait échouer un test de temps en temps (15/10/2026).
- **Loggers `uvicorn.error` et `websockets` bridés à WARNING** — en DEBUG, ils écrivent le contenu des trames WebSocket, dont le message d'authentification. Le jeton de session est aussi ajouté au filtre des logs.
- **Trousseau en mémoire pour les tests de bout en bout** (`OJARVIS_MEMORY_KEYRING=1`, accepté seulement en mode développement) — aucun test ne touche aux vraies clés, rien n'est écrit en clair.

## LLM

- **DeepSeek en principal** — clé valide ; modèles exposés : `deepseek-flash` et `deepseek-v4-pro`.
- **OpenRouter en offre gratuite** — seuls les modèles `:free` sont utilisables, avec un plafond de requêtes par jour. Suffisant pour la vision en développement.
- **Ollama local : `ministral-3:3b` (3 Go, vision + outils, très bon en français)** — un 7B ferait swapper cette machine. Repli plus léger : `qwen3-vl:2b` (1,9 Go). Chargé à la demande, déchargé vite.
- **OpenAI et Anthropic** — adaptateurs écrits et couverts par des tests de contrat simulés ; ils s'activent seuls quand une clé apparaît.
- **Délai maximal avant le premier morceau de réponse, par tâche** — 5 s pour la voix, 20 s pour le chat, 120 s pour un modèle local (chargement). Le 15/10/2026, DeepSeek acceptait les requêtes sans jamais répondre (incident de leur côté) : sans ce délai, un tour vocal attendait sans fin.
- **Modèle local préchargé quand un fournisseur en ligne tombe** — son premier chargement prend 13 à 50 s ; au plus une fois toutes les 5 minutes.
- **Rien n'est appris d'une transcription douteuse** — log-probabilité moyenne de Whisper sous -0,6 : Jarvis répond (et demande de répéter) mais n'extrait aucun fait. Mesuré sur le corpus : phrases ratées sous -1, bonnes au-dessus de -0,7. Sans ce garde-fou, un faux prénom tiré d'une phrase mal entendue s'installait en mémoire.

## Voix

- **Aucun son ne quitte la machine avant « Hey Jarvis »** — réveil et détection de parole tournent en local. Ensuite, seule la phrase qui suit part en ligne pour être transcrite (choix d'Octave, voir plus bas) ; en réglage « locale », rien ne sort.
- **Réveil en deux temps : openWakeWord `hey_jarvis`, puis vérification locale** — le modèle est entraîné sur l'anglais : prononcé « à la française », « Jarvis » obtient un score moyen. Score ≥ 0,5 : réveil. Entre 0,15 et 0,5 : les 2 dernières secondes sont transcrites en local et l'on y cherche « Jarvis », même écorché (« Jarvi », « Jervus »…). Corpus du 15/10 : 17 réveils sur 21 (11/11 prononcés à l'anglaise, 6/6 à la française par Cartesia dont 3 grâce à la vérification, 0/4 pour espeak-ng à l'accent français), aucun faux réveil sur les phrases sans mot de réveil. Coût : ~4 ms par trame de 80 ms.
- **openWakeWord sans TFLite** — sa dépendance `tflite-runtime` n'existe pas pour Python 3.12 ; elle est retirée de ses métadonnées (`[tool.uv.dependency-metadata]`), le modèle tourne en ONNX.
- **Détection de parole : Silero VAD v6, fourni avec faster-whisper** — rien à télécharger. Fin de phrase après 0,6 s de silence ; dès 0,2 s, la transcription démarre en avance (jetée si la parole reprend).
- **Transcription : Groq Whisper large-v3, français imposé (décidé avec Octave le 15/10/2026)** — premier essai au vrai micro : Whisper `base` local rendait du charabia (93 % de mots faux sur le corpus rendu faible et bruité). Essai suivant avec Cartesia Ink-2 en flux : parfait sur des voix natives, mais il devine la langue seul (il ignore le paramètre `language`, vérifié) et, avec l'accent d'Octave, entendait souvent de l'anglais ; `ink-whisper`, qui impose le français, perd la fin des phrases. Groq : langue imposée, ~0,4 s par phrase, envoyée dès la courte pause (le texte est prêt à la fin de la phrase), ~0,0003 $ par phrase. Vrai pipeline : 12/12 tours justes, latence médiane 1,9 s.
- **Relais de la transcription** — Groq limite l'offre gratuite à 20 phrases par minute : sur un 429, il est mis de côté le temps demandé et la phrase enregistrée part d'un bloc chez Cartesia Ink-2 ; Whisper local en dernier secours. La vérification du mot de réveil reste locale : rien ne sort avant le réveil.
- **Whisper local : `base` int8, fenêtre de 10 s au lieu de 30 s** — pour la vérification du réveil et le secours. `small` prend ~5 s ici, trop lent ; `base` prend 0,45 s sur une fenêtre de 10 s au lieu de 1,7 s sur 30 s.
- **Volume du micro interne : 80 % (au lieu de 25 %)** — à 25 %, la voix arrivait à −36 dB : trop faible pour le réveil comme pour la transcription (15/10/2026).
- **Synthèse : Cartesia Sonic `sonic-3.6` en flux (WebSocket)** — connexion ouverte dès le mot de réveil (250 ms gagnées), première phrase envoyée sans tampon (premier son en ~90 ms), clé en en-tête et jamais dans l'URL. Voix par défaut « Étienne » (masculine, posée), modifiable dans Réglages.
- **Repli : Piper `fr_FR-tom-medium`** — si Cartesia échoue, même au milieu d'une réponse, Piper reprend à la phrase en cours ; Cartesia est ensuite mis de côté une minute. Réglage « tout local » possible.
- **Réponse découpée en phrases au fil de l'eau** — la voix commence pendant que le modèle écrit ; la première proposition part dès qu'elle est complète ; le markdown est retiré.
- **Latence mesurée (15/10/2026)** — de la fin réelle de la phrase au premier son : médiane 1,75 s, maximum 1,9 s, sur 17 tours avec DeepSeek et Cartesia (objectif : moins de 3 s).
- **Capture et lecture : `sounddevice` (PortAudio, hôte PulseAudio)** — périphériques choisis par leur nom PipeWire. La liste vient de `pactl` : réinitialiser PortAudio pendant l'écoute coupait le micro sans erreur. Un micro muet 3 s est signalé et relancé.
- **Annulation d'écho : module `echo-cancel` de PipeWire** — chargé par le moteur sans sudo, il crée un micro et une sortie virtuels ; les périphériques par défaut du système ne changent pas ; déchargé à l'arrêt. C'est lui qui permet de couper Jarvis en parlant ; sans lui (ni casque), seul « Hey Jarvis » le coupe, pour qu'il ne s'interrompe pas en s'entendant.
- **Relance sans mot de réveil** — après une réponse, Jarvis écoute encore 6 s ; la conversation continue (même conversation pendant 10 minutes).
- **Pipeline découplé du chat** — un service à part, désactivable dans Réglages ; s'il échoue, le reste du moteur continue.
- **Modèles vocaux téléchargés au premier lancement** (~210 Mo : réveil, Whisper base, voix Piper), à révision figée et vérifiés par SHA-256.

## Sécurité

- **Clés dans le trousseau (Secret Service via `keyring`)**, jamais renvoyées à l'interface. Seuls les 12 noms du brief sont importés depuis `.env`, plus `GROQ_API_KEY` (transcription, décidée avec Octave le 15/10/2026).
- **Masquage central avant tout appel à un modèle** — le routeur remplace chaque valeur du trousseau et chaque format de clé connu par `***` ; le chat et la mémoire font de même avant d'enregistrer. Une clé collée dans le chat ne part jamais au modèle et ne reste pas en clair dans la base (15/10/2026).
- **Noms de périphériques audio à caractères sûrs** — ils sont passés à `pactl` : pas d'espace, donc pas d'option glissée en plus.
- **Trois niveaux de permission appliqués par une seule couche** — lire, agir (validation), détruire ou s'étendre (sandbox puis double confirmation).
- **Journal immuable** — table en ajout seul (triggers SQLite qui refusent UPDATE et DELETE), chaînée par SHA-256, vérifiée au démarrage. Chaque action y est écrite avant (« Lancé : ») et après (« Fait : » ou « Échec : ») ; chaque décision sur un plan aussi.
- **Sandbox : Docker sans réseau, limites CPU, mémoire, PID et durée, nettoyage garanti** ; bubblewrap en repli.

## Jarvis agit 

- **Une seule porte d'exécution des outils** (`permissions/gate.py`) — niveau calculé avec les vrais arguments ; niveau 2 sur validation d'une étape de plan, niveau 3 sur double confirmation ; une étape ne dépasse jamais le niveau que tu as vu (ex. une adresse reprise d'une étape qui mène à un site où tu es connecté).
- **Un appel direct à une action devient un plan d'une étape** — plutôt que de le refuser : la porte tient même si le modèle se trompe ou est manipulé.
- **Un plan est vérifié avant d'être montré** — outils connus, arguments valides, niveaux calculés ; la cible d'une action (adresse, fichier, bouton) doit être connue à la validation : `{{etape N}}` n'est permis que dans un contenu (texte à écrire, valeur d'un champ).
- **Plan signalé après un contenu externe** (« tainted ») — l'écran de validation prévient : vérifie que les étapes correspondent à ta demande.
- **Après une lecture externe, une adresse fabriquée demande ta validation** — lire est autonome, donc une page pouvait faire ouvrir « https://…/?d= ce que tu sais » ; suivre un lien réellement lu reste libre.
- **Un plan coupé par un arrêt du moteur n'est jamais relancé** — marqué en échec et journalisé ; une action ne se rejoue pas sans toi.
- **Validation selon le niveau** — un plan qui ne fait que lire (niveau 1) part sans attendre, sauf s'il suit la lecture d'un contenu externe ; niveau 2 : ta validation ; niveau 3 : double confirmation.
- **Synthèse finale par le modèle**, à partir des résultats encadrés comme données ; repli sur un résumé chiffré si le modèle ne répond pas.
- **Navigateur : Playwright + Chromium, deux contextes** — un anonyme (aucun cookie) pour lire, un « identité » limité aux domaines autorisés ; y naviguer est une action de niveau 2. Une page piégée lue anonymement n'a jamais tes sessions.
- **Sessions dans un coffre chiffré plutôt qu'un profil Chromium persistant** — Chromium lancé par Playwright chiffre ses cookies avec une clé fixe ; ici, Fernet avec une clé dérivée (HKDF) de la clé locale du trousseau, fichier en 0600, filtré par domaine autorisé.
- **Tout le trafic du navigateur par un proxy de garde maison** (`browser/proxy.py`, ~150 lignes) — le filtrage de Playwright ne voit pas les redirections (vérifié : une redirection vers `127.0.0.2` passait). Le proxy résout, vérifie toutes les adresses, se connecte lui-même à l'adresse vérifiée (DNS épinglé) ; WebRTC limité au proxy. `lire_page` passe par le même proxy.
- **Connexion : fenêtre visible, l'utilisateur se connecte lui-même** — Jarvis garde seulement les cookies du domaine choisi (et de ses domaines parents utiles, ex. `.google.com` pour `mail.google.com`) ; il ne remplit jamais un mot de passe, une carte ou un code à usage unique.
- **Import de cookies Chrome/Chromium/Brave domaine par domaine** — base copiée puis lue ; `v10` (clé fixe), `v11` (mot de passe du navigateur dans le trousseau), préfixe SHA-256 du domaine des versions récentes retiré. Testé sur des bases fabriquées : les vrais cookies ne sont lus que sur ta demande, depuis Réglages.
- **Recherche web : Tavily** — la clé existe déjà ; résultats encadrés comme données.
- **Bac à sable : Docker `python:3.12-alpine`** sans réseau, en lecture seule, utilisateur `nobody`, 1 CPU, 256 Mo, 64 processus, 10 s ; **bubblewrap + prlimit** en repli. Le code tourne en niveau 1 : il n'atteint ni le réseau ni tes fichiers.
- **Suppression = corbeille** (`gio trash`) — récupérable ; sans `gio`, rien n'est supprimé.
- **Les outils sont proposés aussi à la voix** — ~2 000 jetons de plus par requête ; latence vocale mesurée : médiane 1,93 s (1,9 s avant).

## Jarvis s'organise 

- **Horaires calculés en heure locale (zoneinfo), rangés en UTC** — « 8 h » reste 8 h après chaque changement d'heure ; testé sur les nuits du 29 mars et du 25 octobre 2026 (heure inexistante → une heure plus tard ; heure doublée → une seule exécution). Pas de cron : chaque jour / certains jours / une fois / intervalle / chaque mois suffisent et se décrivent simplement.
- **Moteur de workflows maison, en base** — chaque étape enregistrée avec le contexte produit ; reprise à l'étape coupée ; trois essais espacés sur erreur. Pas de file externe (brief : pas de microservices).
- **Échéance manquée** (PC éteint) : rattrapée si le retard est de moins de 6 h, sinon notée « manquée » ; une automatisation ponctuelle se désactive après son passage.
- **Une automatisation n'agit jamais seule** — consigne exécutée comme un tour de conversation (canal « automation ») : lecture libre, action → plan qui t'attend, notification.
- **Pas de réaction en chaîne** — les exécutions de workflows portent un identifiant de corrélation `wf…` ; leurs événements ne déclenchent aucune automatisation ; une automatisation au plus une fois par minute. Événements déclencheurs limités à une liste lisible (démarrage, plan terminé ou proposé, mémoire, Telegram, voix).
- **Créer une automatisation depuis le chat = niveau 2** — elle agira plus tard en ton nom ; la supprimer = niveau 3. Depuis l'écran Automatisations, c'est toi qui agis : journalisé directement.
- **Client MCP officiel (SDK Python 2.x)** — un serveur par tâche asyncio ; niveau de chaque outil tiré des annotations MCP (lecture seule → 1, destructeur → 3, sinon 2, prudence par défaut).
- **Deux outils génériques pour le modèle** (`integrations_outils`, `integration_appeler`) plutôt que 70 outils dans chaque requête — coût et latence de la voix ; les arguments sont validés par le schéma de l'outil (jsonschema).
- **GitHub : `ghcr.io/github/github-mcp-server:v1.12.2` en conteneur** (système en lecture seule, sans privilèges, 256 Mo) ; outils `context, repos, issues, pull_requests, notifications`. Version d'il y a un mois plutôt que la 2.0 sortie la semaine passée.
- **Notion : `@notionhq/notion-mcp-server@2.5.2` dans `node:24.21.0-alpine3.24`** — cache npm dans un volume monté sur `/home/node` (monté sur `.npm`, il appartenait à root). Notion annote ses écritures comme destructrices : elles demandent donc une double confirmation.
- **Google Agenda : OAuth « Desktop app » maison (PKCE, état, redirection 127.0.0.1)** — jeton durable au trousseau. Serveur MCP officiel de Google (préversion, à activer dans le projet) s'il répond, sinon serveur MCP de Jarvis (`calendar_server.py`, trois outils annotés).
- **Telegram en « long polling »** (pas de webhook : rien à exposer sur Internet) ; seul un vrai lancement de l'app écoute (un banc d'essai ne répond pas à ta place) ; conversation continue tant qu'on se parle (2 h) ; message vocal transcrit par Groq (fichier OGG envoyé tel quel).
- **Briefing à la première session du jour = première ouverture de l'app** (annoncée par l'interface) ; date notée tout de suite (deux fenêtres → un seul briefing) ; c'est un workflow (rassembler, rédiger, dire) ; la voix sait dire un texte d'elle-même, interruptible.
- **Interface : relecture complète à chaque reconnexion du flux d'événements** — une coupure faisait manquer des événements (un test a échoué une fois en CI).

## Jarvis contrôle le PC 

- **Désactivé par défaut, une seule marque sur les outils** (`os_control`) — tant que le réglage est éteint, la porte refuse ces outils et le modèle ne les voit pas ; il sait dire où l'activer. L'activer demande une confirmation dans Réglages ; chaque changement est journalisé (niveau 3, par toi).
- **Capture : portail `org.freedesktop.portal.Screenshot` sous Wayland (jeepney), `mss` sous X11** — GNOME 50 n'a plus de session X11 et Wayland interdit la lecture directe de l'écran. Mesuré : 1366 × 768 en 4,1 s, sans boîte de dialogue. Le fichier écrit par le portail est effacé dès sa lecture.
- **Lecture de l'écran en local par défaut (`ministral-3:3b`, image de 768 px)** — un écran peut montrer un secret qu'aucun filtre ne sait masquer dans une image, et les offres gratuites en ligne peuvent garder ce qu'elles reçoivent. Mesuré sur une capture de test : juste (« 1 045 € ») ; 30 s modèle chargé, 94 s pour une nouvelle image de 768 px, plus de 2 min à froid. À 512 px, c'est plus rapide (34 s), mais un texte de 13 px sur l'écran tombe à 5 px.
- **« En ligne » au choix (OpenRouter, 1 600 px), repli local** — rapide quand OpenRouter répond ; l'offre gratuite renvoie souvent 429 : l'écran est alors relu en local avec l'image de 768 px (en 1 600 px, le modèle local dépassait son délai).
- **Ollama revérifié avant une demande « locale seulement »** (au plus une fois par minute) — juste après le démarrage, ou si Ollama démarre après le moteur, la lecture locale échouait (« aucun modèle disponible »).
- **Ouvrir : `gtk-launch -- <identifiant>`** — l'entrée du menu telle quelle, comme GNOME, sous Wayland comme sous X11 ; jamais de shell, jamais `Exec` interprété par Jarvis ; `--` empêche un identifiant de passer pour une option.
- **Noms traduits par gettext** — sous Ubuntu, 86 fichiers `.desktop` sur 126 n'ont pas de `Name[fr]` : le nom français vient du catalogue désigné par `X-Ubuntu-Gettext-Domain` (ou `X-GNOME-Gettext-Domain`). Le nom d'origine reste reconnu ; un nom de catalogue qui sortirait du dossier des traductions est ignoré.
- **Fermer : SIGTERM, tes processus seulement, jamais un processus protégé** — session, shells, Python, Jarvis, lanceurs partagés (`flatpak`, `snap`, `env`). Une application lancée par un script (Chrome, LibreOffice, VS Code) est reconnue à son dossier, jamais un dossier partagé (`/usr/bin`, ton dossier personnel). Les entrées du menu qui partagent ce programme sont nommées à la validation.
- **Fichiers : déplacer, copier, créer un dossier = niveau 2** — mêmes garde-fous que la phase 3 (dossier personnel, dossiers sensibles refusés, liens suivis avant la vérification).

## Jarvis s'étend (phase 6, 15-16/10/2026)

- **Skill = calcul en bac à sable** (`skills/`) — description, mode d'emploi, outils à schéma JSON, code Python pur, tests. Ni réseau, ni fichiers, ni clés : ses outils peuvent donc être de niveau 1. Ce qui demande le réseau passe par un serveur MCP, installé en connaissance de cause.
- **Le code d'un skill ne tourne jamais dans le moteur** — un programme d'accueil le charge dans le bac à sable (Docker, sinon bubblewrap), lance les tests ou l'outil, et rend le résultat sur une ligne marquée d'un jeton ASCII tiré à chaque fois. Tests : 20 s ; appel : 10 s ; schéma des arguments : 5 000 caractères au plus.
- **Versions figées** — une version qui a été active ne se réécrit plus (empreinte SHA-256 au journal) ; une seule version active par skill ; un brouillon ou un échec se réécrit à la même version.
- **Activer = « s'étendre », niveau 3** — double confirmation, par un plan (outil `activer_skill`) ou depuis l'écran ; impossible si un test échoue.
- **Un outil peut proposer sa suite (`FollowUp`)** — le vrai DeepSeek créait et testait bien son skill mais n'en proposait pas l'activation ; c'est maintenant `creer_skill` qui la propose, et l'agent en fait un plan au niveau de la suite. Seul le code d'un outil le peut, jamais un contenu lu.
- **Registre MCP officiel (API v0)** — fiches lues prudemment : identifiants vérifiés avant de devenir des arguments, version fixe exigée (« latest » refusé), transport stdio ou service https sans en-tête secret. Préférence : paquet npm, puis Python, puis image Docker, puis service en ligne.
- **Serveurs isolés : Docker, sinon bubblewrap** — conteneur sans privilèges, 512 Mo, 256 processus, un volume par serveur (un serveur ne peut pas toucher au cache d'un autre) ; Python par `ghcr.io/astral-sh/uv:0.11.22-python3.12-trixie-slim`. Sans Docker : `npx` ou `uvx` de la machine dans bubblewrap, sans ton dossier personnel (seul son propre dossier est monté) ; il garde le réseau, y compris local : c'est une limite connue, Docker reste préférable.
- **Essai avant installation** — depuis l'écran : « Essayer » (outils et niveaux) puis « Installer… » (double confirmation). Depuis le chat : double confirmation d'abord (rien de tiers ne tourne avant), puis essai ; s'il échoue, rien n'est installé.
- **Clés des serveurs MCP au trousseau (`OJARVIS_MCP_<SERVEUR>_<RÉGLAGE>`)** — ajoutées au filtre des logs, jamais renvoyées à l'interface, jamais transmises par le modèle (un serveur qui en exige une s'installe depuis l'écran).
- **Serveurs installés = intégrations** — ils rejoignent `integrations_outils` et `integration_appeler` ; niveaux tirés des annotations MCP ; la liste de leurs outils est encadrée comme une donnée (texte écrit par un tiers).
- **Agents (`agents/`) : un `BaseAgent`, trois agents de départ** — dev, recherche, coach. Chacun : un objectif, ses outils, son contexte (il ne voit que sa tâche). Ils passent par le même cœur que Jarvis (porte, plans, encadrement) ; un outil hors de leur liste est refusé, même dans un plan.
- **Orchestrateur sans logique métier** — il ne choisit pas l'agent (Jarvis ou toi le nommez), ne découpe pas la tâche, ne lit pas le résultat : il lance, publie l'état (`agent.*`), enregistre la mission et rend le compte rendu. Une mission par agent à la fois ; une mission coupée par un arrêt n'est jamais relancée ; une trace par mission (coûts et latences par agent).
- **Mémoire graphe dans SQLite** (`graph_entities`, `graph_relations`) — remplie par le même appel d'extraction que les faits (aucun appel de plus), une entité par nom comparé sans majuscules ni accents ; ses liens entrent dans le contexte quand tu cites une entité. Vue graphe : placement par forces calculé une fois (sans animation, même entrée → même dessin), en SVG, avec la même chose en liste pour le clavier.
- **Demandes répétées : index des seules demandes** — les réponses varient trop. Mesuré avec e5 : paraphrases 0,97-0,98 ; demandes sans rapport jusqu'à 0,88 ; « mêmes mots, autre projet » 0,96-0,97. Seuil 0,95 **et** mêmes noms propres ; trois jours différents en un mois ; heure habituelle si l'écart est de deux heures au plus.
- **Anticipation en lecture seule** — elle ne reçoit qu'une vue `GraphView` (lire) ; règle de départ : projet ou sujet cité au moins 3 fois, délaissé 5 jours ; confiance de 0,4 à 0,9 selon les mentions et la durée. Toutes les 6 heures. Une suggestion n'agit jamais : l'accepter ouvre une conversation, où l'action passe par un plan.

## Release (phase 7, 16/10/2026)

- **Moteur embarqué par PyInstaller, en dossier (« onedir »)** — démarrage rapide (rien à décompresser à chaque lancement), placé dans `usr/lib/O-jarvis/engine/` des paquets ; la coque le lance en priorité (puis `OJARVIS_ENGINE_DIR`, puis les sources). Recette : `engine/packaging/ojarvis-engine.spec` ; `mcp.cli` exclu (il quitte sans `typer`). Environ 800 Mo, surtout Whisper (ctranslate2), ONNX, PyAV, Playwright et Piper.
- **Le moteur n'emporte aucune bibliothèque du système** (`SYSTEM_LIBS` dans la recette) — son (PulseAudio, ALSA, JACK, PortAudio et les codecs de libsndfile), D-Bus, systemd, affichage, et la base (glibc, libstdc++, libgcc, zlib) : ce sont celles de la machine, à leur version et à jour. Restent les copies renommées des roues Python (`libgomp-e985bcbb.so`…), que leurs modules chargent par ce nom. Le moteur demande ainsi glibc ≥ 2.34 (une copie de `libmvec` exigeait la 2.43).
- **Le moteur ajouté aux paquets après Tauri, tel quel** (`scripts/build-release.sh`) — Tauri recopiait ses 57 liens symboliques en fichiers (+300 Mo) et `linuxdeploy` (AppImage) échouait sur les bibliothèques renommées des roues Python. Le `.deb` est rouvert, complété (sommes de contrôle, taille) et recompressé en xz ; l'AppImage est extraite, complétée et refermée.
- **Chromium téléchargé au premier usage du navigateur** — par le pilote de Playwright embarqué (~170 Mo, dans `~/.cache/ms-playwright`), une seule fois ; rien n'est téléchargé tant que le navigateur ne sert pas.
- **Dépendances du `.deb`** : `libportaudio2` (micro et haut-parleur), `pulseaudio-utils` (`pactl`, annulation d'écho), `libgtk-3-bin` (`gtk-launch`) ; recommandées : `bubblewrap` (bac à sable sans Docker), `xdg-desktop-portal-gnome` (capture d'écran).
- **Démarrage automatique : greffon officiel `tauri-plugin-autostart`** — une entrée XDG dans `~/.config/autostart`, qui pointe vers l'AppImage elle-même si l'app en vient ; réglable dans Réglages → Démarrage. Lancée ainsi, l'app reste discrète dans la barre système (fenêtre créée cachée) : « Hey Jarvis » et le briefing du matin sont prêts.
- **Paquets publiés dans une version GitHub (`v1.0.0`), à ta demande** — construits en une commande (`scripts/build-release.sh`, ~15 min) sur cette machine, pas par la CI : ils pèsent plusieurs centaines de Mo et leur vérification (session propre) se fait ici.

## Après la 1.0 : 

- **Suite de scénarios par niveaux** (`engine/tools/scenarios.py`, résultats dans `docs/scenarios.md`) — vrai modèle ; chaque scénario vérifie lui-même son résultat, mesure durée et coût, et échoue si un plan de niveau 2 ou 3 s'exécute sans la validation qu'il a donnée. Un Jarvis neuf et un processus par scénario, 2 Go de mémoire au plus (`systemd-run --user --scope`) : la première version, 41 Jarvis dans un même processus, a saturé la mémoire et gelé l'ordinateur.
- **Les chemins montrés au modèle commencent par « ~/ »** (`guards.shown`) — plus lisibles, et le nom d'utilisateur ne part pas vers le modèle ; avec le chemin complet, Jarvis a cru un fichier perdu dans un dossier temporaire.
- **Le plan est la demande de confirmation** — Jarvis ne demande plus « tu confirmes ? » avant de le proposer ; ce qu'il peut lire lui-même (identifiant, contenu d'un fichier), il le lit d'abord ; une demande en partie impossible : il fait le reste et dit ce qui manque.
- **Un plan annoncé sans être proposé est relancé une fois** (`PLAN_CLAIM`, sur toute la réponse du tour) — « valide-le à l'écran », « passe par un plan », ou l'appel de l'outil écrit en texte (`<proposer_un_plan>`, vu avec DeepSeek) : sinon tu cherches en vain quoi valider ; la relance n'est jamais enregistrée dans la conversation.
- **Le compte rendu d'un plan entre dans la conversation avant que le plan soit dit fini** — une question posée juste après le voit déjà.
- **Le compte rendu d'un plan présente les résultats comme ceux de ses propres outils** — rédigé à partir de « Ma demande : <ta dernière phrase> », il a cru te répondre et a douté d'un fichier bien créé (vu chez Octave le 16/10).
- **L'état réel des plans de la conversation entre dans chaque tour** (`Agent.plan_states`) — Jarvis le lit au lieu de se fier à sa mémoire de l'échange.
- **La coque donne le `.env` du dépôt au moteur lancé depuis les sources** (`OJARVIS_DOTENV`) — le lanceur « un clic » n'est pas en mode développement et ne lisait que `~/.config/o-jarvis/.env` ; le moteur empaqueté garde ce dernier. La règle n'est pas changée côté moteur : les tests liraient sinon le vrai `.env`.
- **« Ton dossier personnel » dans les descriptions d'outils** reste tel quel (l'écran Réglages s'adresse à toi) ; c'est la règle de Jarvis qui précise qu'il s'agit du tien.

## Interface

- **pnpm** pour le frontend — déjà installé, rapide.
- **TypeScript 6.0** — TypeScript 7 (compilateur natif) n'est pas encore pris en charge par `typescript-eslint` (< 6.1).
- **Tauri confirmé, pas d'Electron (14/10/2026)** — sphère mesurée dans l'app réelle (WebKitGTK, Intel HD 4600, Wayland, toile de 363 px) : 59,7 à 60,0 i/s selon l'état, p95 ≤ 19 ms, au plus 1 image longue sur 360.
- **Sphère en WebGL2 avec un shader maison** — nuage de points (spirale de Fibonacci), cœur et halo sur un quadrilatère, anneaux en pointillés. Plus léger que three.js. Couleurs lues dans les tokens CSS.
- **Repli sans WebGL** — un disque lumineux en CSS qui garde les couleurs d'état.
- **CSS Modules + tokens** — Stylelint refuse toute couleur hors de `tokens.css`. Seule exception : la couleur de fond de la fenêtre native dans `tauri.conf.json`, copie de `--color-bg`, pour éviter un flash blanc à l'ouverture.
- **Radix UI** pour les dialogues et boutons radio accessibles, **lucide** pour les icônes, **TanStack Query** pour les données du moteur, **Zustand** pour l'état de connexion.
- **Routeur à hachage** — une app de bureau n'a pas de serveur pour réécrire les URL.
- **Polices embarquées (`@fontsource`)** : Inter, Space Grotesk, JetBrains Mono — l'app fonctionne hors ligne (leçon de l'ancien : polices Google bloquées au build).
- **Journal de l'interface vers la coque** (`ui_log`) — les erreurs de la fenêtre arrivent dans les logs même si le moteur est arrêté. Les caractères de contrôle sont retirés : une ligne ne peut pas en imiter une autre.
- **Permissions de la fenêtre au minimum** — écouter les événements du shell, ouvrir un lien http(s) dans le navigateur, afficher une notification ; `core:default` aurait aussi permis de créer menus et icônes système.
- **Raccourci global par le portail GlobalShortcuts (`ashpd`)** — sous Wayland, c'est le seul moyen d'écouter une touche hors de la fenêtre. Ctrl+Alt+J par défaut ; GNOME demande confirmation au premier lancement. L'app s'annonce au portail sous `io.github.octavebahoun.ojarvis`, d'où le nom du fichier `.desktop` du lanceur.
- **Notifications du bureau seulement fenêtre en arrière-plan** — fournisseur en panne, voix de secours, voix arrêtée.
- **Niveau sonore hors de React** — une douzaine de mesures par seconde, lissées et lues par la sphère à chaque image, sans redessiner l'écran.
- **Sous-titres** — chaque phrase dite par Jarvis s'affiche à l'accueil, au rythme de sa voix.
- **Validation : « Refuser » et « Valider » de même taille, côte à côte** (brief : refuser aussi simple qu'accepter) ; niveau 3 : « Valider… » ouvre une seconde confirmation qui nomme l'action destructrice.
- **Plans dans la conversation et dans un écran dédié** — la même carte, tenue à jour par les événements du moteur ; compteur des plans en attente dans la navigation ; la sphère passe en « action en attente ».

## Qualité

- **Tests de bout en bout : Playwright sur l'interface branchée au vrai moteur**, avec audit axe à chaque écran. La mesure de fluidité, elle, se fait dans l'app Tauri réelle.
- **Audit Rust (`cargo audit`) en CI** — aucune vulnérabilité au 15/10/2026. Deux avertissements viennent de la pile GTK de Tauri : `proc-macro-error` n'est plus maintenue (outil de compilation) et `glib::VariantStrIter` est non sûre (fonction que le projet n'utilise pas). Acceptés, surveillés.
- **Voix testée sans matériel** — en développement, `OJARVIS_FAKE_AUDIO=1` remplace micro, modèles et haut-parleur par des simulations ; le pipeline, le chat et l'interface restent réels. Les tests du pipeline utilisent une horloge virtuelle (aucune attente).
- **Corpus vocal et banc d'essai** (`engine/tools/`) — « Hey Jarvis, … » dit par Piper, espeak-ng et Cartesia ; le banc rejoue le corpus dans le vrai pipeline (vrais modèles, vrai DeepSeek, vrai Cartesia) et mesure réveil, transcription et latence.
- **Audit npm : dépendances livrées strictement propres** ; une faille « haute » sans correctif existe dans `braces`, utilisé seulement par Stylelint au moment du lint, sur nos propres fichiers : aucun contenu externe ne l'atteint. Risque accepté et surveillé (la CI bloque au niveau critique pour les outils de développement).
