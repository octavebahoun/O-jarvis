#!/usr/bin/env bash
# Construit le moteur empaqueté (PyInstaller) dans engine/dist/ojarvis-engine/ :
# l'exécutable « ojarvis-engine » et son dossier _internal, que scripts/build-release.sh
# ajoute ensuite aux paquets.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="${OJARVIS_BUILD_DIR:-$HOME/.cache/o-jarvis/build-engine}"

cd "$root/engine"
uv sync --group package --frozen
# O-jarvis Shell (https://github.com/octavebahoun/o-jarvis-shell), le travail du hackathon Meta,
# vit dans son propre dépôt : il est figé dans le même moteur.
shell="${OJARVIS_SHELL_DIR:-$root/../o-jarvis-shell}"
if [[ ! -d "$shell/src/ojarvis_shell" ]]; then
  echo "O-jarvis Shell introuvable : $shell (le cloner à côté d'O-jarvis, ou OJARVIS_SHELL_DIR)" >&2
  exit 1
fi
uv pip install --quiet --no-deps "$shell"
rm -rf "$work"
uv run --group package pyinstaller packaging/ojarvis-engine.spec \
  --distpath dist --workpath "$work" --noconfirm --log-level WARN
echo "Moteur empaqueté : $root/engine/dist/ojarvis-engine ($(du -sh dist/ojarvis-engine | cut -f1))"
