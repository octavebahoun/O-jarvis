# Evidence

Recorded runs of O-jarvis Shell on a real machine, kept as produced, for judges who cannot run it on Ubuntu.

Machine: HP laptop, Intel i5-4200M, 8 GB of RAM (7.2 GB usable), Ubuntu 26.04.1 LTS, GNOME Shell 50.1 on Wayland.

## Demo video

[https://youtu.be/a3-_38qB3gs](https://youtu.be/a3-_38qB3gs) (2:53), November 2, 2026, real Meta models, on the machine above. What it shows, in order: the O-jarvis v1.1.0 release (before the event), then O-jarvis Shell: reading at once and a plan reviewed by Muse Wonder; Muse Wonder building a website; one approved plan that brings LibreOffice back and exports the letter as a PDF (SAM 3.1 finds each button); one approved plan that comments on a LinkedIn post in Chrome, with the O-jarvis cursor showing each gesture; voice, then a `sudo` install whose password the user types; Muse Spark Open answering from Baseten.

Editing, so you know what you did not see: waits for the model are sped up (2× to 15×), never the gestures; two short cuts remove GNOME search results that showed personal file names; another browser extension's toolbar icon and a terminal's tab bar are masked. Voice-over and subtitles were added; the screen recordings have no sound.

## Terminal sessions

November 2, 2026, O-jarvis Shell 1.4.0, real Meta models. Recorded with [`record_session.py`](record_session.py) in an empty git repository (`~/demo-jarvis`). The only change is the removal of the terminal's control codes (`sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | tr -d '\r'`).

| Transcript | Typed | What it shows |
|---|---|---|
| [Disk space and a vague request](terminal/2026-11-02-disk-space-and-a-vague-request.txt) | `How much disk space is left?`, `Create notes.txt here with a shopping list.`, `/modele open`, `In one sentence, what is in notes.txt?` | `df` runs at once, since reading needs no approval. Asked for a list without items, Jarvis asks what to put in it instead of inventing them. After `/modele open`, the header reads `baseten / meta-models/muse-spark-open-1026` and Muse Spark Open finds that the file does not exist. |
| [Plan, review and the open model](terminal/2026-11-02-plan-review-and-open-model.txt) | `Create notes.txt here with a shopping list: milk, eggs, rice, tea.`, `/modele open`, `In one sentence, what is in notes.txt?` | A level 2 plan card with its diff, reviewed by Muse Wonder 1.0 before the approval. The file is written only after `o`, then checked with `cat`. Muse Spark Open reads it back. Three Meta models in one session. |

Each answer ends with its duration and cost: from 4.4 s and $0.034 to 25.8 s and $0.047.

The last line of both transcripts, "L'app utilisait ce moteur", is wrong: the app was closed. The engine counted its own internal subscribers as clients. Fix: [O-jarvis#73](https://github.com/octavebahoun/O-jarvis/pull/73).

## Automated checks

479 tests (`uv run pytest -m "not real"`), plus ruff, ruff format and mypy, run on every pull request and every push to `main`: [CI runs](https://github.com/octavebahoun/o-jarvis-shell/actions).

## Earlier measurements

[HACKATHON.md](../../HACKATHON.md) describes earlier real tests (clicks in LibreOffice, screen recordings, Chrome). Their screenshots show private files and messages, so they are not published here.
