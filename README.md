# O-jarvis Shell

Jarvis in your terminal, in your Chrome and on your desktop, for Ubuntu. It doesn't just talk: it acts on your web, your apps and your terminal, always with your approval, and every model it calls is a Meta model.

*Version française : [README.fr.md](./README.fr.md).*

> **Language.** Jarvis answers in the language you use: ask in English, it answers in English, and speaks with Meta's English MMS voice.

## Meta Global AI Developer Hackathon

O-jarvis existed before the event (release v1.1.0, tag [`avant-hackathon`](https://github.com/octavebahoun/O-jarvis/tree/avant-hackathon)): a desktop app for Ubuntu with chat, voice, memory and validated actions, running on several providers. **The submission is O-jarvis Shell, what we built during the event:** Jarvis in the terminal, the Chrome extension, control of the PC and of the browser, all on Meta models. The desktop app is not part of the submission. [`HACKATHON.md`](./HACKATHON.md) lists everything built during the event.

## What O-jarvis Shell does

- **In the terminal.** `o-jarvis-shell` opens Jarvis in the current folder. It reads at once (files, `git`, `df`…), proposes the commands that change something and runs them once you approve, measures before and after, and tells you which model answered each turn. Type `/voix` and speak: Muse Voice Transcribe listens, Muse Spark answers, MMS speaks.
- **In Chrome.** An extension opens Jarvis in a side panel next to the page: it reads the open tab and clicks, fills fields or opens pages in it, after your approval. It stays connected in the background, so the terminal can list your tabs, read one and act in it too.
- **On the desktop.** Jarvis finds a button or an icon on screen (SAM 3.1 frames the elements, Muse Spark picks and checks the right one) and clicks it through GNOME's remote desktop portal. It can watch your screen live (`/partage`), or film it and explain what happened.
- **In its own browser.** For research and web tasks, Jarvis drives its own Chromium; you can show its window and stop a plan at any step.
- **Always with your approval.** Reading runs at once. Any action becomes a plan you approve; risky ones (payment, deletion, destructive command) need a second confirmation. Every action goes into an append-only journal chained by SHA-256.

The engine is Python (FastAPI) and runs on `127.0.0.1` only, protected by a token drawn at every launch. Data stays in a local SQLite database, and API keys live in the system keyring.

## Meta models (Meta Global AI Developer Hackathon)

With the default **"All Meta"** profile, every model O-jarvis calls at runtime is a Meta model, fallbacks included. If no Meta model can answer, Jarvis says so; it never falls back to another provider.

| Meta model | Where it runs | What it does in O-jarvis |
|---|---|---|
| **Muse Spark 1.3** | Meta Model API | Every answer: chat, voice, shell mode and its plans, screen and web page reading (vision), choosing what to click on screen, watching a short screen recording (video), memory extraction, titles |
| **Muse Spark Open** (`muse-spark-open-1026`) | Baseten | Takes over every task when Muse Spark does not answer |
| **Muse Wonder 1.0** | Meta Model API | Reviews each code edit in shell mode; takes over when the same command fails twice |
| **Muse Voice Transcribe 1.0** | Meta Model API | Speech to text, after the shortcut, the mic button or `/voix` in the terminal |
| **MMS TTS, French and English** (`facebook/mms-tts-fra`, `facebook/mms-tts-eng`) | On the user's PC (ONNX Runtime, CPU) | Jarvis's voice: each sentence is spoken by the voice of its language; numbers, units and acronyms are spelled out first by plain code |
| **SAM 3.1** | Meta Model API | Frames the buttons and icons of a desktop app on a screenshot, so Jarvis can click the one Muse Spark picked; in Chrome, the same fallback for an element the page does not name (an unlabeled icon, a canvas) |

Operating system features and plain code (no AI model):

- **espeak-ng**, the Ubuntu system voice: fallback voice if MMS fails.
- **Speech detection**: the sound volume compared with the room's background noise.
- **Listening starts** with the global shortcut (GNOME desktop portal), the mic button or `/voix`: there is no wake word, and no sound leaves the PC before that.
- **Echo cancellation**: PipeWire's `module-echo-cancel`.
- **Clicking in a desktop app**: GNOME's remote desktop portal moves the pointer, after the user allows it once.
- **Live screen share and recordings**: GNOME's ScreenCast portal, read by GStreamer (two images per second, kept in memory only); ffmpeg turns a recording into an MP4 clip, deleted once Muse Spark has watched it.
- **Memory search**: SQLite full-text search and hashed word vectors.
- **Browser actions**: Playwright drives Jarvis's own Chromium; screenshots use the desktop portal.
- **Jarvis in Chrome**: a Chrome extension talks to the engine through Chrome's native messaging; it reads the open tab (text and numbered elements) and sends it to Muse Spark as framed data.
- **Safety**: command classification and secret masking are rules in code.

The "Automatic" profile, outside the submission, can also use DeepSeek, Anthropic, OpenAI, OpenRouter, Ollama, Groq, Cartesia, Piper, Whisper, openWakeWord, Silero VAD and multilingual-e5.

## Run it

You need **Ubuntu 24.04 or later** (x86_64, GNOME on Wayland for the desktop features) and a **Meta Model API key**. No other key is required: the "All Meta" profile is the default.

### Option A: install the package

Download the `.deb` from the [latest release](https://github.com/octavebahoun/O-jarvis/releases/latest), then put your key where Jarvis reads it, and open Jarvis in any folder:

```bash
sudo apt install ./O-jarvis_*_amd64.deb
mkdir -p ~/.config/o-jarvis
printf 'MODEL_API_KEY=%s\n' 'your-meta-key' > ~/.config/o-jarvis/.env && chmod 600 ~/.config/o-jarvis/.env
cd ~/some-project && o-jarvis-shell
```

At launch, the engine imports the key into the system keyring (see [`.env.example`](./.env.example) for the other optional keys). The first `/voix` downloads Meta's MMS voices, French and English, once (about 230 MB).

### Option B: from source

Needs Node.js 22+. The script does not use sudo: if system packages are missing, it prints the `apt` command to run.

```bash
git clone https://github.com/octavebahoun/O-jarvis.git
cd O-jarvis
scripts/install-dev.sh
cp .env.example .env              # then set MODEL_API_KEY=… (never commit .env)
cd engine && uv run ojarvis-shell # Jarvis in the terminal, in the current folder
```

### PC control

Clicking in desktop apps and looking at the screen are off by default. Turn them on once in the settings window that ships with the package: run `o-jarvis` (from source: `cd app && pnpm tauri dev`), then **Réglages → Contrôle du PC**. The first click and the first screen share each ask for GNOME's permission once.

### Things to try

- **Read at once**: `How much disk space is left?` → Jarvis runs `df` and answers; the model of each turn is shown, e.g. `· meta / muse-spark-1.3`.
- **Act with approval**: `Create notes.txt here with a shopping list.` → a plan; nothing happens until you answer `o`.
- **Voice**: type `/voix`, then speak.
- **Desktop**: `Click the Export as PDF button in LibreOffice.`, or `/partage` then `What's on my screen?`
- **Chrome**: install the extension (below), open a page and ask in the side panel `Open the latest article and summarize it.`; from the terminal, `Which tabs are open in my Chrome?`

## Jarvis in Chrome

An extension opens Jarvis in Chrome's side panel, next to the page you are looking at.

- **Reading.** On the sites you allow (Chrome asks once per site), Jarvis reads the open tab: title, address, visible text, your selection, and a numbered list of its links, buttons and fields. Never your cookies, your passwords or what you typed in fields. To Jarvis, the page is data, never instructions: a plan proposed after reading it is flagged, and an injected order is ignored.
- **Acting.** Clicking, filling a field, opening an address, closing a tab: always through a plan you approve in the panel. Before acting, the extension checks that the element is the one in the plan. Jarvis never fills a password or a card number, and a payment, a purchase or a deletion is a level 3 action, confirmed twice. After a plan, it reads the tab again and continues (eight times at most per request).
- **Visual fallback.** When the page does not name an element (an unlabeled icon, a canvas), Jarvis finds it on a screenshot of the tab: SAM 3.1 frames the elements, Muse Spark picks and checks the right one; before clicking, a new screenshot checks that nothing moved.
- **"Act without asking"**, per site and off by default, runs plans that stay in the tab without waiting for your approval; everything else (files, commands, level 3) still asks. A Stop button halts everything.
- **Always connected.** The extension stays connected in the background while Chrome runs: from the terminal, Jarvis can list your tabs, read one and act in it, with the same approvals.

Install: with the `.deb`, the bridge to the engine is already declared to Chrome and Chromium; open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and pick `/usr/lib/O-jarvis/extension`. From source, run `scripts/install-chrome.sh` first, then load the `extension/` folder the same way.

## Safety and privacy

- Three permission levels: reading runs at once; acting needs your approval; destroying needs a second confirmation. Shell commands are classified by rules in code, not by the model.
- Content from the web, a file or a tab is framed as data; after reading it, an address the model makes up waits for your approval, so a page cannot make Jarvis leak data.
- Secrets and cookies are never sent to a model, and logs are filtered. Code that Jarvis writes runs in a Docker sandbox without network.
- Details (in French): [`docs/securite.md`](./docs/securite.md).

## Checks

44 scenarios played with the real model check Jarvis from the simplest to the hardest (understanding a request full of typos, writing a file only after approval, ignoring a trapped page, fixing a small Python project): [`docs/scenarios.md`](./docs/scenarios.md).

```bash
cd engine && uv run pytest -m "not real"     # engine
cd app && pnpm test && pnpm e2e               # interface, then end to end with the real engine
cd app/src-tauri && cargo test                # Tauri shell
```

More (in French): architecture [`docs/architecture.md`](./docs/architecture.md), decisions [`DECISIONS.md`](./DECISIONS.md), progress [`PROGRESS.md`](./PROGRESS.md), journal [`docs/journal/`](./docs/journal/README.md).
