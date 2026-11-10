# O-jarvis Shell

[![CI](https://github.com/octavebahoun/o-jarvis-shell/actions/workflows/ci.yml/badge.svg)](https://github.com/octavebahoun/o-jarvis-shell/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Built on O-jarvis](https://img.shields.io/badge/built%20on-O--jarvis-4fe3ff)](https://github.com/octavebahoun/O-jarvis)
[![Meta models only](https://img.shields.io/badge/models-Meta%20only-0866ff)](#meta-models-and-where-they-run)

<p align="center"><a href="https://www.youtube.com/watch?v=U_sVJ2BQKUo"><img src="https://img.youtube.com/vi/U_sVJ2BQKUo/hqdefault.jpg" alt="O-jarvis Shell demo video" width="560"></a><br><b><a href="https://www.youtube.com/watch?v=U_sVJ2BQKUo">▶ Demo video (2:56)</a></b>: filmed on a real Ubuntu with the real Meta models. Recorded runs and transcripts: <a href="docs/evidence">docs/evidence</a>.</p>

Jarvis in your terminal, in your Chrome and on your desktop, for Ubuntu. It doesn't just talk: it acts on your web, your apps and your terminal, always with your approval, and every model it calls is a Meta model.

- **What it does.** `o-jarvis-shell` opens Jarvis in any folder: it reads at once (files, `git`, `df`…), proposes the commands that change something and runs them once you approve. A Chrome extension puts Jarvis next to the page you are reading and lets it click or fill fields in your tab. On the desktop, SAM 3.1 frames the buttons of any app and Jarvis clicks the right one through GNOME's portal; if the window is hidden (behind the terminal, say), it brings it back first through GNOME's Activities view, and you see the pointer glide to its target.
- **Meta models only.** Muse Spark answers and sees, Muse Spark Open (on Baseten) takes over when it is down, Muse Wonder reviews code, Muse Voice Transcribe listens, SAM 3.1 finds what to click, and Meta's MMS voices speak, on your CPU. No other provider is ever called, even as a fallback.
- **Always with your approval.** Every shell command is classified by rules in code, not by the model: reading runs at once, acting waits for your `o`, destroying needs `oui` typed in full. Secrets never reach a model.
- **Where your data goes.** Text goes to Meta's API (or to Baseten for Muse Spark Open), screenshots too when you ask Jarvis to look or click. Your voice leaves the PC only after you press the shortcut, the mic button or type `/voix`. Speech is synthesized on your machine.

<p align="center"><sub>Built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026) · built on <a href="https://github.com/octavebahoun/O-jarvis">O-jarvis</a> · what was built during the event: <a href="HACKATHON.md">HACKATHON.md</a></sub></p>

## Built on O-jarvis

[**O-jarvis**](https://github.com/octavebahoun/O-jarvis) existed before the hackathon (release v1.1.0, October 24, 2026): a desktop assistant for Ubuntu with a Python engine (FastAPI on `127.0.0.1`, memory, permission gate, audit journal, model router). Everything built during the event lives in this `shell/` folder of the O-jarvis repository; the snapshot the jury saw stays frozen in [octavebahoun/o-jarvis-shell](https://github.com/octavebahoun/o-jarvis-shell).

- O-jarvis Shell is a Python package, `ojarvis_shell`, that declares O-jarvis as a dependency (`ojarvis-engine`, installed from the neighbouring [`engine/`](../engine) folder, see [`pyproject.toml`](pyproject.toml)).
- The O-jarvis engine has a single extension point, `ojarvis.extension`. At start it loads [`ojarvis_shell/extension.py`](src/ojarvis_shell/extension.py), lends it its services (model router, memory, audit journal, tool registry, keyring) and lets it add its tools, API routes and voices.
- O-jarvis runs without this folder; the `.deb` ships both in one engine.

## Quickstart

You need **Ubuntu 24.04 or later** (x86_64, GNOME on Wayland for the desktop features) and a **Meta Model API key**. A **Baseten API key** is optional: it lets Muse Spark Open, Meta's open-weight model, answer (as the fallback, or after `/modele open`). Without it, Muse Spark answers instead and `/modele open` says so.

1. **Your keys.** Copy [`.env.example`](.env.example) to where the engine reads it, then fill in your Meta key (and your Baseten key if you have one). Never commit the filled file.

   ```bash
   mkdir -p ~/.config/o-jarvis && cd ~/.config/o-jarvis
   [ -e .env ] || curl -fsSL https://raw.githubusercontent.com/octavebahoun/o-jarvis-shell/main/.env.example -o .env
   chmod 600 .env && nano .env
   ```

   The O-jarvis app also asks for both keys on its first launch and stores them in the system keyring.

2. **Install.** Download the `.deb` from the [latest release](https://github.com/octavebahoun/o-jarvis-shell/releases/latest):

   ```bash
   sudo apt install ./O-jarvis_*_amd64.deb
   ```

   The package installs O-jarvis Shell (`o-jarvis-shell`), the Chrome extension and the O-jarvis desktop app (`o-jarvis`), which O-jarvis Shell plugs into.

3. **Run.** Open Jarvis in any folder, then ask `How much disk space is left?`

   ```bash
   cd ~/some-project && o-jarvis-shell
   ```

### On a real Ubuntu 24.04 (GNOME on Wayland)

O-jarvis Shell drives the real desktop: GNOME's portals for screen share and clicks, Chrome through its extension, your microphone. It needs a real Ubuntu install, not a container. Built for Ubuntu 24.04 and later (x86_64, glibc 2.39); tested on Ubuntu 26.04 with 8 GB of RAM.

1. **Check the session.** Desktop clicks and screen share were only tested on Wayland:

   ```bash
   echo $XDG_SESSION_TYPE    # should print: wayland
   gnome-shell --version     # GNOME Shell 46 or later
   ```

   If it prints `x11`, log out, click the gear at the bottom right of the login screen, choose **Ubuntu** (not "Ubuntu on Xorg") and log in again. The terminal and Chrome work in either session.

2. **Install the package** (Quickstart, step 2). apt also installs the recommended packages: GStreamer for the live screen share, ffmpeg for screen recordings. To check them:

   ```bash
   gst-inspect-1.0 pipewiresrc > /dev/null && command -v ffmpeg && echo ready
   ```

3. **Open the O-jarvis app once** (`o-jarvis`, or from the app grid). Its welcome screen asks for everything at once: your keys, the "All Meta" profile, PC control and GNOME's two permissions (screen and pointer), Meta's voices, the Chrome extension. Then `o-jarvis-shell` works in any folder.

4. **Browser.** Use Google Chrome (tested). Ubuntu's Chromium is a snap, whose confinement may block the extension's bridge to the engine.

To uninstall: `sudo apt remove o-jarvis`. Your keys stay in the system keyring until you remove them in **Réglages → Clés**.

### From source

Needs [uv](https://docs.astral.sh/uv/), your keys (step 1 above) and the system packages the `.deb` would bring. `uv sync` installs the O-jarvis engine from the neighbouring `engine/` folder.

```bash
sudo apt install git curl libportaudio2 pulseaudio-utils libgtk-3-bin xdg-desktop-portal-gnome \
  gstreamer1.0-tools gstreamer1.0-pipewire gstreamer1.0-plugins-good ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/octavebahoun/O-jarvis.git
cd O-jarvis/shell
uv sync
cd ~/some-project && ~/O-jarvis/shell/scripts/o-jarvis-shell
```

If the O-jarvis app is running, the terminal joins its engine; otherwise it starts its own, which stops with it. The first start of the engine can take up to a minute on a slow machine.

### Jarvis in Chrome

With the `.deb`, the bridge to the engine is already declared to Chrome and Chromium. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, press Ctrl+L and type `/usr/lib/O-jarvis/extension`. From source, run `scripts/install-chrome.sh` first, then load the [`extension/`](extension) folder the same way.

### PC control

Clicking in desktop apps and looking at the screen are off by default. Turn them on once in the O-jarvis app: its welcome screen on first launch, or later **Réglages → Contrôle du PC**. The welcome screen also asks GNOME's two permissions (screen and pointer) right away, so that none pops up in the middle of a task.

### Before you try: permissions and first downloads

Jarvis never acts without asking, and neither does your system. Expect these prompts, each once:

| When | What asks | What to answer |
|---|---|---|
| Before any click or screen reading | O-jarvis: PC control is off by default | Turn it on in `o-jarvis` → **Réglages → Contrôle du PC** |
| First `/partage` or first screen recording | GNOME: share your screen | Pick the screen, then **Share** |
| First click in a desktop app | GNOME: allow remote control | **Allow** (the pointer moves only after you approve a plan) |
| First tab Jarvis reads in Chrome | Chrome: access to this site | **Allow** in the side panel |
| First `/voix` | The microphone | Allow it if your system asks |

What downloads the first time, with the default "All Meta" profile:

- **Meta's MMS voices**, French and English (about 230 MB), at the first `/voix` or spoken answer. Nothing else is downloaded for voice: no Whisper, no wake word.
- **Playwright's Chromium** (one download, into `~/.cache/ms-playwright`), the first time Jarvis browses the web with its own browser, if it is not there yet.

Everything else runs through Meta's API with your key.

## Things to try

Real transcripts of the first terminal rows, recorded on November 2: [`docs/evidence/`](docs/evidence).

| Where | Type or say | What happens |
|---|---|---|
| Terminal | `How much disk space is left?` | Jarvis runs `df` at once and answers; the header shows `◆ Jarvis   meta / muse-spark-1.3` |
| Terminal | `Create notes.txt here with a shopping list: milk, eggs, rice, tea.` | A plan card with the diff, reviewed by Muse Wonder; nothing happens until you answer `o` |
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
| **Muse Spark Open** (`muse-spark-open-1026`, open weights) | Baseten (partner inference) | Takes over every task when Muse Spark does not answer; answers every request after `/modele open` |
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
| [`docs/evidence/`](docs/evidence) | Recorded runs on a real machine: terminal transcripts, and how they were recorded |
| [`.env.example`](.env.example) | The keys the engine reads: Meta (required), Baseten (optional) |

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
