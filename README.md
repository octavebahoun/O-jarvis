# O-jarvis

Assistant personnel desktop pour Ubuntu. On lui parle à voix haute (« Hey Jarvis »), il se souvient, agit sous contrôle et sait s'étendre.

![Accueil d'O-jarvis : la sphère au centre](docs/captures/accueil.webp)

- **App** : Tauri v2 (Rust) + React + TypeScript.
- **Cerveau** : moteur Python (FastAPI) lancé par l'app, sur `127.0.0.1` uniquement, protégé par un jeton tiré à chaque lancement.
- **Modèles** : profil « Tout Meta » par défaut, rien que des modèles de Meta (voir [Meta models](#meta-models-meta-global-ai-developer-hackathon)) ; le profil « Automatique » ouvre aux autres fournisseurs et aux modèles locaux.
- **Voix** : en « Tout Meta », on parle après le raccourci global (`Ctrl+Alt+J`) ou le bouton micro ; Muse Voice Transcribe écoute, MMS répond. En « Automatique » : « Hey Jarvis » reconnu en local, transcription Groq (Whisper local en secours), voix Cartesia ou Piper, réponse parlée en moins de 2 s. On peut l'interrompre en parlant.
- **Mémoire** : ce que Jarvis sait de toi est visible, modifiable et supprimable.
- **Actions** : il cherche sur le web, lit pages et fichiers, lance du code en bac à sable et pilote son propre navigateur. Toute action (cliquer, écrire, utiliser ta session) passe par un plan que tu valides ; chaque action est écrite dans un journal impossible à effacer. Détails : [`docs/securite.md`](./docs/securite.md).
- **Organisation** : rappels et tâches à l'heure dite ou sur un événement, qui survivent à un redémarrage ; briefing du matin ; GitHub, Notion et Google Agenda par leurs serveurs MCP officiels ; Telegram pour lui parler depuis ton téléphone.
- **Contrôle du PC** (désactivé par défaut, à activer dans Réglages) : ouvrir et fermer tes applications, ranger tes fichiers, « regarde mon écran » (lu sur ta machine par défaut, ou en ligne si tu le choisis).
- **Extension** : Jarvis s'écrit des skills (code testé dans le bac à sable, activé après ta double confirmation), installe des serveurs MCP du registre officiel (isolés), confie des tâches à ses agents (dev, recherche, coach), relie ta vie dans une mémoire graphe et te suggère d'automatiser ce que tu demandes souvent. Voir [`docs/creer-un-skill.md`](./docs/creer-un-skill.md).
- **Données** : SQLite local ; les clés vivent dans le trousseau du système.

| Conversation et plan à valider | Agents | Mémoire graphe |
|---|---|---|
| ![Une conversation avec un plan à valider](docs/captures/plan.webp) | ![Les agents dev, recherche et coach](docs/captures/agents.webp) | ![La mémoire graphe](docs/captures/memoire-graphe.webp) |

Le projet s'est construit phase par phase. L'état du travail est dans [`PROGRESS.md`](./PROGRESS.md), les choix dans [`DECISIONS.md`](./DECISIONS.md), le journal dans [`docs/journal/`](./docs/journal/README.md), le schéma dans [`docs/architecture.md`](./docs/architecture.md).

## Meta models (Meta Global AI Developer Hackathon)

With the default **"All Meta"** profile, every model O-jarvis calls at runtime is a Meta model, fallbacks included. If no Meta model can answer, Jarvis says so; it never falls back to another provider. What was built during the hackathon is listed in [`HACKATHON.md`](./HACKATHON.md).

| Meta model | Where it runs | What it does in O-jarvis |
|---|---|---|
| **Muse Spark 1.3** | Meta Model API | Every answer: chat, voice, shell mode and its plans, screen and web page reading (vision), choosing what to click on screen, watching a short screen recording (video), memory extraction, titles |
| **Muse Spark Open** (`muse-spark-open-1026`) | Baseten | Takes over every task when Muse Spark does not answer |
| **Muse Wonder 1.0** | Meta Model API | Reviews each code edit in shell mode; takes over when the same command fails twice |
| **Muse Voice Transcribe 1.0** | Meta Model API | Speech to text, after the shortcut, the mic button or `/voix` in the terminal |
| **MMS TTS, French** (`facebook/mms-tts-fra`) | On the user's PC (ONNX Runtime, CPU) | Jarvis's voice; numbers, units and acronyms are spelled out first by plain code |
| **SAM 3.1** | Meta Model API | Frames the buttons and icons of a desktop app on a screenshot, so Jarvis can click the one Muse Spark picked |

Operating system features and plain code (no AI model):

- **espeak-ng**, the Ubuntu system voice: fallback voice if MMS fails.
- **Speech detection**: the sound volume compared with the room's background noise.
- **Listening starts** with the global shortcut (GNOME desktop portal), the mic button or `/voix`: there is no wake word, and no sound leaves the PC before that.
- **Echo cancellation**: PipeWire's `module-echo-cancel`.
- **Clicking in a desktop app**: GNOME's remote desktop portal moves the pointer, after the user allows it once.
- **Live screen share and recordings**: GNOME's ScreenCast portal, read by GStreamer (two images per second, kept in memory only); ffmpeg turns a recording into an MP4 clip, deleted once Muse Spark has watched it.
- **Memory search**: SQLite full-text search and hashed word vectors.
- **Browser actions**: Playwright drives Chromium; screenshots use the desktop portal.
- **Jarvis in Chrome**: a Chrome extension (side panel) talks to the engine through Chrome's native messaging; the open tab is read by the extension and sent to Muse Spark as framed data.
- **Safety**: command classification and secret masking are rules in code.

The "Automatic" profile, outside the submission, can also use DeepSeek, Anthropic, OpenAI, OpenRouter, Ollama, Groq, Cartesia, Piper, Whisper, openWakeWord, Silero VAD and multilingual-e5.

## Ce que Jarvis sait faire

Une suite de 44 scénarios, joués avec le vrai modèle, le vérifie du plus simple au plus difficile : comprendre une question pleine de fautes, écrire un fichier seulement après ta validation, ignorer une page piégée, réparer un petit projet Python. Dernière série : **43 réussis sur 43 joués** (le 44ᵉ agit sur ton bureau et ne se joue qu'à la demande), sans aucune action exécutée sans validation. Le tableau complet est dans [`docs/scenarios.md`](./docs/scenarios.md).

## Installer

Ubuntu 24.04 ou plus récent (glibc 2.39 ; vérifié sur Ubuntu 26.04). Récupère les paquets de la [dernière version](https://github.com/octavebahoun/O-jarvis/releases/latest) ou construis-les (`scripts/build-release.sh`, ~15 min ; ils arrivent dans `app/src-tauri/target/release/bundle/`), puis :

```bash
sudo apt install ./O-jarvis_1.1.0_amd64.deb      # paquet Debian/Ubuntu
# ou, sans installation :
chmod +x O-jarvis_1.1.0_amd64.AppImage && ./O-jarvis_1.1.0_amd64.AppImage
```

Le paquet `.deb` ajoute deux commandes : `o-jarvis` ouvre l'app, et `o-jarvis-shell` ouvre Jarvis dans le terminal, dans le dossier où tu es (mode shell). Celui-ci rejoint le Jarvis de l'app s'il tourne, sinon il lance le sien.

Au premier lancement : renseigne tes clés dans Réglages (ou dépose un `.env` dans `~/.config/o-jarvis/` : l'app importe ses clés dans le trousseau), puis attends la voix (modèles téléchargés une fois, ~350 Mo). Réglages → Démarrage : lancer O-jarvis à l'ouverture de session.

## Installer pour le développement

Ubuntu 24.04 ou plus récent, Node.js 22+. Le script n'utilise pas sudo : s'il manque des paquets système, il affiche la commande `apt` à lancer.

```bash
git clone https://github.com/octavebahoun/O-jarvis.git
cd O-jarvis
scripts/install-dev.sh
```

Complète ensuite `.env` avec tes clés (voir `.env.example`). Au premier lancement, l'app les importe dans le trousseau.

## Lancer

```bash
cd app && pnpm tauri dev          # développement, rechargement à chaud
scripts/install-launcher.sh       # compile et ajoute « O-jarvis » au menu des applications
```

Fermer la fenêtre arrête tout, moteur compris. `Ctrl+Q` aussi.

Au premier lancement, le moteur télécharge ses modèles locaux (environ 350 Mo : recherche par le sens, mot de réveil, transcription, voix de secours), puis la voix est prête. Le script d'installation installe aussi le Chromium du navigateur de Jarvis (environ 170 Mo) et l'image Docker du bac à sable. Au premier lancement par l'icône, GNOME demande d'autoriser le raccourci `Ctrl+Alt+J`.

## Jarvis dans Chrome

Une extension ouvre Jarvis dans le panneau latéral de Chrome, à côté de la page que tu regardes. Sur les sites que tu autorises (Chrome te le demande une fois par site), Jarvis lit l'onglet ouvert : son titre, son adresse, son texte visible et ta sélection, jamais tes cookies ni tes champs de saisie. Pour lui, la page est une donnée, jamais une consigne : un plan proposé après sa lecture est signalé, et chaque action attend ta validation dans le panneau.

```bash
scripts/install-chrome.sh         # déclare à Chrome le pont vers le moteur (messagerie native)
```

Puis, dans Chrome : `chrome://extensions` → Mode développeur → Charger l'extension non empaquetée → dossier `extension/`. Le panneau rejoint le Jarvis de l'app s'il tourne, sinon il lance le sien.

## Vérifier

```bash
cd engine && uv run pytest -m "not real"     # moteur
cd app && pnpm test && pnpm e2e               # interface, puis bout en bout avec le vrai moteur
cd app/src-tauri && cargo test                # coque
cd engine && uv run python tools/voice_bench.py --only cartesia   # voix : réveil et latence réels
```
