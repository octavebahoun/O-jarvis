# O-jarvis Shell: Meta Global AI Developer Hackathon

O-jarvis existed before the hackathon. This file separates what was built during the build period (October 27 to November 4, 2026, ET) from what was already there.

## Starting point

Everything built before the event is frozen under the tag [`avant-hackathon`](https://github.com/octavebahoun/O-jarvis/tree/avant-hackathon) (October 26, 2026): release v1.1.0 plus one dependency security fix. All hackathon work lands on the `hackathon` branch, whose history cannot be rewritten (no force push, no deletion).

To see exactly what was built during the hackathon:

- on GitHub: [compare `avant-hackathon...hackathon`](https://github.com/octavebahoun/O-jarvis/compare/avant-hackathon...hackathon)
- locally: `git diff avant-hackathon..hackathon`

## Built during the hackathon

Each feature has an issue labeled `hackathon`.

### All-Meta profile ([#47](https://github.com/octavebahoun/O-jarvis/issues/47)), done

- **Meta provider**: Muse Spark 1.3 and Muse Wonder 1.0 through the Meta Model API, with their prices so every call shows its cost.
  - Muse Spark always reasons before answering. Jarvis asks for `minimal` effort on quick tasks (first word in 0.8 s instead of 1.6 s, measured on October 26), and `high` effort on reasoning tasks.
  - The hidden reasoning counts toward the token limit. Jarvis adds headroom, so a short answer (a 30-token title) never comes back empty.
  - Contributor model IDs (`-contributor`), where Meta trains on your requests, are refused.
- **"All Meta" profile in Settings**: Muse Spark answers every task (chat, voice, reasoning, screen reading). It needs a Meta key, and the key has its own "Test" button. Since October 30 it is the default and calls Meta models only (see below).
- **Speech to text with Muse Voice Transcribe**: with the profile on, each spoken sentence is transcribed by Meta, with a French hint and the keyword "Jarvis".
- **Muse Spark Open as fallback**: if Muse Spark does not answer, Muse Spark Open, Meta's open-weight model served by Baseten, takes over for every task (text, tools, vision, code review). Real test with Muse Spark made unreachable: a text answer, a tool call and an image read all went through Muse Spark Open, for less than a tenth of a cent each.
- **Relay to Muse Wonder**: Muse Spark does the everyday work. When the same command fails twice in a row (a test still red), the next turn goes to Muse Wonder, Meta's strongest model; a success resets the count. Settings: how many failures before the relay (1 to 3, or off) and which model.
- **The Journal shows it all**: every model call records its provider, model, attempts (fallbacks included), tokens and cost, plus a "relais" line with the reason when the relay is used; every spoken sentence records its transcription model (`muse-voice-transcribe-1.0`) and its voice (`mms`).

### Meta models only (rule of October 29), done

The rule: every model the project calls at runtime must be a Meta model, small local ones included (confirmed by the organizers on October 30). The "All Meta" profile became strict, and the default:

- **Text**: Muse Spark, then Muse Spark Open; never DeepSeek, Claude, GPT or Ollama, even as a fallback. A model forced by hand counts only if it is a Meta model. Screen reading goes to Muse Spark. With no Meta model ready, Jarvis says how to fix it.
- **Voice**: MMS TTS (French), a Meta model run on the CPU with ONNX Runtime (114 MB, pinned by SHA-256), replaces Cartesia and Piper; numbers, units and acronyms are spelled out by plain code ("6,8 Go" becomes "six virgule huit gigaoctets"). espeak-ng, the system voice, takes over if MMS fails. Measured on an i5-4200M: 0.7 s for a short sentence.
- **Listening**: Muse Voice Transcribe alone (no Groq, Cartesia or Whisper fallback; a failure is said out loud). Speech is detected from the sound volume above the room's noise, in place of Silero VAD. No wake word (openWakeWord is not a Meta model): you speak after the global shortcut, the mic button or `/voix`.
- **Memory**: search by hashed words in place of multilingual-e5; memories already kept get their new vector at startup.

The full list of Meta models and of OS features is in the [README](./README.md#meta-models-meta-global-ai-developer-hackathon).

### Shell mode: Jarvis in your terminal ([#7](https://github.com/octavebahoun/O-jarvis/issues/7)), done

- **`ojarvis-shell`**: run it in any folder of your home. Jarvis works in that folder: it runs commands, reads their output, fixes and runs again, and shows you each command with the end of its output.
- **Every command is classified before it runs** (`engine/src/ojarvis/shell/commands.py`, 182 tests): reading (`ls`, `git status`, `df`) runs at once; any other action waits for your "o"; a destruction (`rm`, `docker prune`, `git reset --hard`) needs "oui" typed in full. Pipes, `;`, `&&`, `$(…)`, heredocs and `xargs` are split and each part is classified; the riskiest one wins.
- **Refused outright**: `sudo` (Jarvis shows the command, you type it), and any command that would read a secrets file (`.env`, `~/.ssh`, keyrings) or search your whole home: its output would reach the model.
- **Safe execution**: no input (a command that asks a question fails instead of hanging), a deadline that stops the whole process group, secret environment variables removed, output truncated (beginning and end kept) and given to the model as data, never as instructions.
- **Validate, then continue**: after you approve a plan, Jarvis reads the results and carries on until the task is done. In shell mode it can chain up to 16 steps per answer; actions still wait for you.
- **Same Jarvis as the app**: the terminal joins the engine the app already runs (same memory, same audit log) through a private connection file (readable by your account only, removed when the engine stops). If the app is closed, the terminal starts its own engine and stops it on exit.
- The command tool exists only in shell mode: the app chat can neither call it nor slip it into a plan.
- **"Passe en mode shell"**: said to the app (or by voice), it opens your own terminal (the system default first, Ptyxis on Ubuntu 26.04) on `ojarvis-shell`, in the folder you asked for, joined to the same Jarvis.
- **No secret leaves through a command**: commands that print a token (`gcloud auth print-access-token`, `gh auth token`, `pass`…) are refused unless used inside a substitution such as `-H "Authorization: Bearer $(…)"`; and Google tokens (even truncated), JWTs, private keys and more are masked before anything reaches the model. Found during a real test on October 26, fixed the same night.

### Coding agents in the shell ([#8](https://github.com/octavebahoun/O-jarvis/issues/8)), done

- **Targeted edits with a diff**: `modifier_fichier` replaces an exact passage; every file change shows its colored diff before you approve. An edit that would not apply goes back to the model, never to you.
- **A second model reviews every change**: Muse Spark writes, Muse Wonder reviews the diff against your request (in "All Meta" mode). Its verdict is shown with the plan and sent back to the author, who can propose a corrected plan. You still decide.
- **Tests until green**: after an edit, Jarvis runs the project's tests and fixes until they pass (real test: a failing `unittest` fixed in one 2-line edit, both tests green).
- **Jarvis looks at its own web page**: `voir_page` opens a page served on your machine in a separate, throwaway Chromium (no cookies, local network blocked), takes a screenshot and has Muse Spark's vision read it, with the HTTP status and console errors. Real test: before the fix the model saw "a broken image icon, no visible button" (white on white); after it, "a blue rounded Commander button, well contrasted", and no console error.
- The shell thinks harder (Muse Spark at `high` effort) since it writes code.
- **Fewer approvals**: answer `t` and that kind of command (`npm install`, `pytest`…) is approved for the rest of the session. Never for a destruction, a code edit, a network or cloud command, inline code, or after reading anything from outside (a web page, GitHub, an MCP server, `curl` output).
- **Budgets**: a request stops cleanly after 12 follow-ups, 20 minutes or $1, and waits for your "continue"; the cost of each request is shown, and the results of the last plan reach the model with your next message.
- **Dead-ends memory**: a fix that failed is noted per project. It never fills the context on its own: Jarvis looks it up when a test fails, so it does not try the same fix twice. Real test over two sessions: it found "returning None failed" and proposed `return 0`, tests green.

### Managing the machine ([#9](https://github.com/octavebahoun/O-jarvis/issues/9)), done

- **"Why is my disk full?"**: Jarvis measures with a 5-minute budget (a home folder takes 2 minutes here) and drills into the biggest folders, deleting nothing. Real test: Downloads 48 GB (27 GB of it Telegram), caches 47 GB, npm 17 GB, Videos 32 GB, trash 9 GB.
- **Jarvis never runs `sudo`**: refused everywhere, even hidden in `bash -c`, a substitution, `xargs` or `find -exec`, plus `sudoedit` and `machinectl shell`.
- **`sudo` in one tap**: Jarvis proposes the command, and the terminal asks "[e]xecute it myself" ("oui" typed in full for a destruction). It runs in your own terminal: `sudo` asks for your password directly, and Jarvis never sees it. The output comes back to Jarvis, masked, as data, and the command goes to the audit log. Real test: `sudo du -sh /var/lib/docker` → "Docker uses 6.8 GB".
- **Machine map**: on the first shell turn, Jarvis records the hardware, system, disks, installed tools, running services and project folders, read-only (names, sizes and versions, never file contents or secrets). It lives in Memory, where you can read, edit or delete it, and reaches the model in shell mode or when a question is about the PC. Real test: "my PC is slow, why?" → "your RAM is saturated: 4.9 GB used of 7.2 GB, 3.6 GB of swap".

### Guardrails for autonomy ([#15](https://github.com/octavebahoun/O-jarvis/issues/15)), done

- **Mission budgets**: on top of its step limit, a mission given to an agent has limits: $0.50, 10 minutes, 300,000 tokens and 3 tool failures in a row (money and duration can be set when it starts). Past them, it stops without another model call, becomes "suspended" with what it already did, and a notification tells you, in the app and on Telegram.
- **Never an action on its own**: tested, a mission never runs an action (level 2) or a destruction (level 3) by itself; each one becomes a plan that waits for your approval. Automations go through the same gate.
- **Before / after**: a shell plan carries a measure (a read-only command) taken before the actions and after them; when a plan ends with a read, that read becomes its measure. The terminal shows both side by side, Jarvis compares them and says "verified" or "not verified"; a plan of actions without proof can never be called "done". Real test: before "ls: cannot access 'preuve.txt'", after "6 bytes", answer "Verified".

### Voice in the terminal ([#10](https://github.com/octavebahoun/O-jarvis/issues/10)), done

- **`/voix`** in `ojarvis-shell`: the app's voice pipeline hears one sentence, the terminal shows it ("Tu as dit : …") and handles it like a typed request, plan approvals included. The final answer is also spoken.
- **One listener at a time**: the microphone stays with the app; the terminal asks the engine for one sentence, then for the spoken answer.
- **Nothing leaves the PC before you speak**: like the mic button, and silence sends nothing. If the app is closed, the terminal says to open it first.
- Real test: "ouvre, ouvre mon navigateur" said to the terminal, transcribed, plan approved, Chrome opened.

### Watch Jarvis act ([#34](https://github.com/octavebahoun/O-jarvis/issues/34)), done

- **Live view**: Chromium films Jarvis's page (DevTools screencast). Only the latest image is kept, and the app reads it from the engine on 127.0.0.1; nothing leaves the PC. It shows in the card of a running plan that uses the browser, with the page address.
- **"Stop"**: the running step finishes and the next ones never start. The plan ends as "stopped by you", and the stop goes to the audit log.
- **"Show Jarvis's browser"** (Settings › Browser): Chromium opens in a window. Approvals stay the same.
- **Found during the real test**: in a web app (React), a click changed the address without reloading, and Jarvis read the old page; it now waits until the text changes and settles. Two links with the same name and the same address (menu and footer) are no longer ambiguous.
- Real tests on octave.excellenceteam.site: "click Projects, then Blog, and tell me the latest article" → the right title, watched in a Chromium window; a 4-click plan stopped during the 2nd click → the last two never ran.

### Clicking in desktop apps ([#48](https://github.com/octavebahoun/O-jarvis/issues/48)), done

- **Find** (`trouver_a_l_ecran`, read-only): two Meta models work together. SAM 3.1 frames every button and icon on a screenshot, pixel-accurate, but does not read labels. Muse Spark points to roughly where the element is, then picks it in an enlarged view of that area, where the frames are numbered and colored, and checks its pick on a zoom of the element. If SAM framed nothing there (a letter "B"), SAM frames the object under the pointed spot.
- **Never at random**: nothing found, two possible elements, or a pick the zoom denies twice → Jarvis asks, describing places in words.
- **Click** (`cliquer_ecran`, approval needed): only an element found less than 10 minutes ago, never raw coordinates. The plan card shows the screenshot with the element framed in green. After approval the app steps aside, and the engine waits until the element is visible again before clicking (15 s at most). The click goes through GNOME's remote desktop portal: GNOME asks the user once, and refusing means no click. Every click goes to the audit log with its annotated screenshot.
- **Measured**: on a copy of LibreOffice's toolbars (about 50 real icons), 12 right picks, 2 questions and no wrong click, in 6 to 11 s. Real test on October 31: "wait 3 s, then click Export as PDF in LibreOffice", the right icon clicked 0.8 s after approval.
- First real test (October 30): Muse Spark picked the printer next to the PDF icon among 54 numbered icons. That led to the zoom check and the pointed area.

### Live screen share and screen recordings ([#50](https://github.com/octavebahoun/O-jarvis/issues/50)), done

- **Live share** (asked for during the #48 tests): GNOME's ScreenCast portal (the user allows it once, and GNOME's indicator stays on while it lasts), read by GStreamer. Two images per second, only the latest kept, in memory; nothing is recorded. Looking at the screen, finding an element and checking it before a click read that image instead of taking a screenshot. Settings › PC control › "Share…"; a banner shows the live preview and "Stop sharing", and stopping it from GNOME stops it for Jarvis too.
- **`filmer_ecran`** (on request only, 30 s at most): the images of the share (started and stopped for the occasion if it was off) become an MP4 clip (ffmpeg), sent to Muse Spark with the question through Meta's Responses API (`input_video`). The working file is deleted as soon as Muse Spark has answered.
- Tests: on a fake window filmed for 6 s, Muse Spark told in order the menu opened then closed, and the error banner shown for barely 2 s. Real test on October 31: 10 s filmed (20 images, 236 KB), explained in 18 s, nothing left on disk.

### Jarvis in Chrome, done

- **Side panel**: a Chrome extension opens Jarvis next to the page. On the sites the user allows (Chrome asks once per site), it reads the open tab: title, address, visible text, selection, and a numbered list of links, buttons and fields, including the ones web apps declare by role (a Discord server icon). Never cookies, passwords or what was typed in fields. Muse Spark gets the tab as framed data, never as instructions.
- **Acting in the tab**: click, fill a field, open an address, close a tab, always through a plan. Before acting, the extension checks the element is the one in the plan; it never fills a password or a card number, and a payment, a purchase or a deletion is level 3 (double confirmation). After a plan, Jarvis reads the tab again and continues (eight times at most).
- **"Act without asking"**, per site and off by default: plans that only act in the tab run without waiting; everything else still asks. A Stop button halts everything.
- **Visual fallback with SAM 3.1**: for an element the page does not name (an unlabeled icon, a canvas), Jarvis finds it on a screenshot of the tab, like in desktop apps; a new screenshot checks nothing moved before the click.
- **Always connected**: the extension stays connected in the background while Chrome runs, so the terminal, the app and the voice can list the tabs, read one and act in it. The bridge is Chrome's native messaging; the `.deb` declares it to Chrome and Chromium.
- Real tests: Muse Spark read a test tab and ignored the order hidden in it; "add the green tea to the basket" → a plan clicking the right button, not the black tea's nor "Pay"; on Discord, "click Les IAstronautes" from the panel; from the terminal, "which tabs are open?" then "close my Discord tab".

### Answers in the user's language, done

- Jarvis answers in the language of the question (judges write in English), and Meta's English MMS voice (`facebook/mms-tts-eng`, run on the PC) speaks English sentences; French ones keep the French MMS voice.

### One engine for the app, the terminal and Chrome, done

- Whichever starts second joins the engine already running; if it stops, the others join the one running then, or start their own. The `.deb` also installs `o-jarvis-shell`, to open Jarvis in any folder.

### Hardening after a code review (October 27)

A review of the night's commits found 21 problems; each was checked in the code, fixed, and covered by tests. The most serious: `bash -c 'rm -rf ~/projet' --help` was classified as a read, so it would have run without asking, and `find … | xargs cat` could send secret files to the model. Also fixed: results lost when the budget stopped a request, "always" still active after reading GitHub or `curl` output, a non-UTF-8 file losing its accents on edit, and the terminal crashing on a U+2028 character.

## Already there before (not part of the submission)

The desktop app (Tauri + React), the Python engine, voice ("Hey Jarvis", in the "Automatic" profile), memory, the permission gate, the browser agent, skills, MCP servers, agents and Telegram. See the [README](./README.md).

## Contributors

- Octave BAHOUN-HOUTOUKPE
