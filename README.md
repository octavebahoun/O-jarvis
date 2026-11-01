# O-jarvis

A personal desktop assistant for Ubuntu: you talk to it, it remembers, and it acts under your control. O-jarvis is the desktop app (Tauri v2, React) and its Python engine (FastAPI), which runs on `127.0.0.1` only, protected by a token drawn at every launch.

*Version française, plus détaillée : [README.fr.md](./README.fr.md).*

![O-jarvis home screen](docs/captures/accueil.webp)

> **O-jarvis Shell**, built during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026), lives in its own repository: **[octavebahoun/o-jarvis-shell](https://github.com/octavebahoun/o-jarvis-shell)**. It plugs into this engine as an extension: Jarvis in the terminal, Jarvis in Chrome, clicks on the desktop with SAM 3.1, Meta's voices, all on Meta models. O-jarvis runs without it; the `.deb` ships both.

## What O-jarvis does

- **Chat and voice**: a conversation with memory; it speaks and listens (wake word, or a global shortcut).
- **Memory**: what Jarvis knows about you is visible, editable and deletable (facts, conversations, a graph).
- **Actions under control**: web search, reading pages and files, code in a sandbox without network, its own browser. Reading runs at once; any action becomes a plan you approve, and destroying needs a second confirmation. Every action goes into an append-only journal chained by SHA-256.
- **Organization**: reminders and tasks that survive a restart, a morning briefing, GitHub, Notion and Google Calendar, Telegram.
- **PC control** (off by default): open and close apps, sort files, look at the screen.
- **Extensions**: skills written by Jarvis and tested in the sandbox, MCP servers from the official registry, and O-jarvis Shell.

Data stays in a local SQLite database; API keys live in the system keyring.

## Run it from source

You need Ubuntu 24.04 or later and Node.js 22+. The script does not use sudo: if system packages are missing, it prints the `apt` command to run.

```bash
git clone https://github.com/octavebahoun/O-jarvis.git
cd O-jarvis
scripts/install-dev.sh
cp .env.example .env   # then set your keys (never commit .env)
cd app && pnpm tauri dev
```

To add O-jarvis Shell, follow [its README](https://github.com/octavebahoun/o-jarvis-shell#run-it).

## Extending the engine

`ojarvis.extension` is the single point where an extension plugs in: the engine lends it its services (models, memory, journal, tool registry) and the extension adds its tools, routes and voices. `ojarvis.testing` gives extensions the same simulated engine the engine's own tests use.

## Checks

```bash
cd engine && uv run pytest -m "not real"     # engine
cd app && pnpm test && pnpm e2e               # interface, then end to end with the real engine
cd app/src-tauri && cargo test                # Tauri shell
```

## License

O-jarvis is open source under the [MIT license](./LICENSE). Bundled software and downloaded models keep their own licenses: see [`THIRD-PARTY.md`](./THIRD-PARTY.md).

More (in French): architecture [`docs/architecture.md`](./docs/architecture.md), decisions [`DECISIONS.md`](./DECISIONS.md), progress [`PROGRESS.md`](./PROGRESS.md), journal [`docs/journal/`](./docs/journal/README.md).
