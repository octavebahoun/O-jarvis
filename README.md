# O-jarvis Shell

[![CI](https://github.com/octavebahoun/o-jarvis-shell/actions/workflows/ci.yml/badge.svg)](https://github.com/octavebahoun/o-jarvis-shell/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Built on O-jarvis](https://img.shields.io/badge/built%20on-O--jarvis-4fe3ff)](https://github.com/octavebahoun/O-jarvis)
[![Meta models only](https://img.shields.io/badge/models-Meta%20only-0866ff)](#meta-models-and-where-they-run)

Jarvis in your terminal, in your Chrome and on your desktop, for Ubuntu. It doesn't just talk: it acts on your web, your apps and your terminal, always with your approval, and every model it calls is a Meta model.

- **What it does.** `o-jarvis-shell` opens Jarvis in any folder: it reads at once (files, `git`, `df`…), proposes the commands that change something and runs them once you approve. A Chrome extension puts Jarvis next to the page you are reading and lets it click or fill fields in your tab. On the desktop, SAM 3.1 frames the buttons of any app and Jarvis clicks the right one through GNOME's portal.
- **Meta models only.** Muse Spark answers and sees, Muse Spark Open (on Baseten) takes over when it is down, Muse Wonder reviews code, Muse Voice Transcribe listens, SAM 3.1 finds what to click, and Meta's MMS voices speak, on your CPU. No other provider is ever called, even as a fallback.
- **Always with your approval.** Every shell command is classified by rules in code, not by the model: reading runs at once, acting waits for your `o`, destroying needs `oui` typed in full. Secrets never reach a model.
- **Where your data goes.** Text goes to Meta's API (or to Baseten for Muse Spark Open), screenshots too when you ask Jarvis to look or click. Your voice leaves the PC only after you press the shortcut, the mic button or type `/voix`. Speech is synthesized on your machine.

<p align="center"><sub>Built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026) · built on <a href="https://github.com/octavebahoun/O-jarvis">O-jarvis</a> · what was built during the event: <a href="HACKATHON.md">HACKATHON.md</a></sub></p>

## Built on O-jarvis

[**O-jarvis**](https://github.com/octavebahoun/O-jarvis) existed before the hackathon (release v1.1.0, October 24, 2026): a desktop assistant for Ubuntu with a Python engine (FastAPI on `127.0.0.1`, memory, permission gate, audit journal, model router). **This repository holds only what was built during the event.**

- O-jarvis Shell is a Python package, `ojarvis_shell`, that declares O-jarvis as a dependency (`ojarvis-engine`, pinned to a commit in [`pyproject.toml`](pyproject.toml) and installed from its repository).
- The O-jarvis engine has a single extension point, `ojarvis.extension`. At start it loads [`ojarvis_shell/extension.py`](src/ojarvis_shell/extension.py), lends it its services (model router, memory, audit journal, tool registry, keyring) and lets it add its tools, API routes and voices.
- O-jarvis runs without this repository; the `.deb` ships both in one engine.

## Run it

You need **Ubuntu 24.04 or later** (x86_64, GNOME on Wayland for the desktop features) and a **Meta Model API key**. No other key is required.

Put your key where the engine reads it (never commit it):

```bash
mkdir -p ~/.config/o-jarvis
printf 'MODEL_API_KEY=%s\n' 'your-meta-key' > ~/.config/o-jarvis/.env && chmod 600 ~/.config/o-jarvis/.env
```

### Option A: the package

Download the `.deb` from the [latest release](https://github.com/octavebahoun/o-jarvis-shell/releases/latest), then open Jarvis in any folder:

```bash
sudo apt install ./O-jarvis_*_amd64.deb
cd ~/some-project && o-jarvis-shell
```

The package also installs the O-jarvis desktop app (`o-jarvis`), which O-jarvis Shell plugs into.

### Option B: from source

Needs [uv](https://docs.astral.sh/uv/). `uv sync` installs O-jarvis from its repository as a dependency.

```bash
git clone https://github.com/octavebahoun/o-jarvis-shell.git
cd o-jarvis-shell
uv sync
cd ~/some-project && ~/o-jarvis-shell/scripts/o-jarvis-shell
```

If the O-jarvis app is running, the terminal joins its engine; otherwise it starts its own, which stops with it. The first `/voix` downloads Meta's MMS voices, French and English, once (about 230 MB).

### Jarvis in Chrome

With the `.deb`, the bridge to the engine is already declared to Chrome and Chromium. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, press Ctrl+L and type `/usr/lib/O-jarvis/extension`. From source, run `scripts/install-chrome.sh` first, then load the [`extension/`](extension) folder the same way.

### PC control

Clicking in desktop apps and looking at the screen are off by default. Turn them on once in the O-jarvis app (`o-jarvis` → **Réglages → Contrôle du PC**). The first click and the first screen share each ask for GNOME's permission once.

## Things to try

| Where | Type or say | What happens |
|---|---|---|
| Terminal | `How much disk space is left?` | Jarvis runs `df` at once and answers; the header shows `◆ Jarvis   meta / muse-spark-1.3` |
| Terminal | `Create notes.txt here with a shopping list.` | A plan card; nothing happens until you answer `o` |
| Terminal | `Add a total() function to cart.py, then run the tests.` | The diff, reviewed by Muse Wonder, then the tests after your `o` |
| Terminal | `/modele wonder`, then `Build a one-page site for a tea shop in this folder.` | Muse Wonder writes the site; the header shows `meta / muse-wonder-1.0`; `/modele auto` goes back |
| Terminal | `/voix`, then speak | Muse Voice Transcribe listens, Muse Spark answers, MMS speaks in your language |
| Terminal | `Click the Export as PDF button in LibreOffice.` | SAM 3.1 frames the buttons, Muse Spark picks one, you approve, GNOME clicks |
| Terminal | `/partage`, then `What's on my screen?` | Live screen share, read by Muse Spark |
| Chrome panel | `Open the latest article and summarize it.` | A click plan in your tab, then a summary |
| Terminal | `Which tabs are open in my Chrome?` | The extension stays connected: the terminal sees and acts in your tabs |

## Meta models and where they run

| Meta model | Where it runs | What it does |
|---|---|---|
| **Muse Spark 1.3** | Meta Model API | Every answer: shell mode and its plans, reading the screen and web pages (vision), picking what to click, watching a short screen recording |
| **Muse Spark Open** (`muse-spark-open-1026`, open weights) | Baseten (partner inference) | Takes over every task when Muse Spark does not answer |
| **Muse Wonder 1.0** | Meta Model API | Reviews each code edit before you approve it; takes over when the same command fails twice |
| **Muse Voice Transcribe 1.0** | Meta Model API | Speech to text, after the shortcut, the mic button or `/voix` |
| **MMS TTS**, French and English (`facebook/mms-tts-fra`, `facebook/mms-tts-eng`, open weights) | On your PC (ONNX Runtime, CPU) | Jarvis's voice: each sentence is spoken by the voice of its language |
| **SAM 3.1** | Meta Model API | Frames the buttons and icons of a desktop app on a screenshot; in Chrome, the fallback for an element the page does not name |

The "All Meta" profile ([`llm/meta_profile.py`](src/ojarvis_shell/llm/meta_profile.py)) lists only these routes; O-jarvis's router then refuses any other provider, and if no Meta model can answer, Jarvis says so.

Plain code and OS features, no AI model: the command classifier, secret masking, GNOME's ScreenCast and RemoteDesktop portals, GStreamer for the live share, espeak-ng as the voice of last resort.

## Safety and privacy

- **Three permission levels**: reading runs at once; acting needs your approval; destroying needs a second confirmation. The classifier ([`shell/commands.py`](src/ojarvis_shell/shell/commands.py)) splits pipes, `;`, `&&`, `$(…)`, heredocs and `xargs`, and the riskiest part wins.
- **Refused outright**: `sudo` (Jarvis shows the command, you run it yourself and your password goes to sudo only), reading a secrets file (`.env`, `~/.ssh`, keyrings), commands that print a token.
- **Pages are data**: a tab or a web page is framed as data for the model; a plan proposed after reading one is flagged, and an order hidden in a page is ignored.
- **Chrome stays local**: the tab tools are offered only to the terminal, the app and the Chrome panel, never to Telegram or automations.
- Every action goes into O-jarvis's append-only audit journal.

## Repository layout

| Path | What |
|---|---|
| [`src/ojarvis_shell/extension.py`](src/ojarvis_shell/extension.py) | The entry point O-jarvis loads: assembles everything below |
| [`src/ojarvis_shell/shell/`](src/ojarvis_shell/shell) | The terminal client and the command classifier |
| [`src/ojarvis_shell/tools/`](src/ojarvis_shell/tools) | Tools added to O-jarvis: commands, sudo, tabs, screen actions, file edits, machine map |
| [`src/ojarvis_shell/chrome/`](src/ojarvis_shell/chrome), [`extension/`](extension) | The bridge to Chrome and the extension itself |
| [`src/ojarvis_shell/desktop/`](src/ojarvis_shell/desktop) | SAM 3.1 client, clicks, live share, recordings |
| [`src/ojarvis_shell/voice/`](src/ojarvis_shell/voice), [`llm/`](src/ojarvis_shell/llm) | Meta's voices and transcription, the "All Meta" profile |
| [`packaging/`](packaging), [`scripts/`](scripts) | The `o-jarvis-shell` command, the Chrome native host, dev scripts |

## Checks

```bash
uv run ruff check src tests && uv run mypy src tests
uv run pytest -m "not real"
```

The tests run on a simulated O-jarvis engine (`ojarvis.testing`), with fake model providers: no network, no real model.

## Known gaps

- Linux only (Ubuntu with GNOME on Wayland for the desktop features).
- The Chrome extension is loaded unpacked, not yet published on the Chrome Web Store.
- Muse Voice Transcribe can take a few seconds for a short sentence.

## License

MIT, see [LICENSE](LICENSE). O-jarvis is MIT too; Meta's MMS voices are CC-BY-NC-4.0 (non-commercial use).
