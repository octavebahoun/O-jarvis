#!/usr/bin/env bash
# Jarvis dans Chrome (développement) : déclare l'hôte de messagerie native à Chrome et à
# Chromium, pour que l'extension (dossier extension/) puisse parler au moteur.
# Seule notre extension peut joindre l'hôte : son identifiant vient de la clé du manifeste.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="$root/engine/.venv/bin/ojarvis-chrome-host"
name="io.github.octavebahoun.ojarvis"
extension_id="mjdiobmgjkghbmmbfnhifikcaehdndlc"

if [[ ! -x "$host" ]]; then
  (cd "$root/engine" && uv sync --quiet)
fi

declared=0
for config in "$HOME/.config/google-chrome" "$HOME/.config/google-chrome-beta" \
  "$HOME/.config/chromium" "$HOME/.config/BraveSoftware/Brave-Browser"; do
  [[ -d "$config" ]] || continue
  mkdir -p "$config/NativeMessagingHosts"
  cat > "$config/NativeMessagingHosts/$name.json" <<JSON
{
  "name": "$name",
  "description": "O-jarvis : le pont entre l'extension et le moteur",
  "path": "$host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$extension_id/"]
}
JSON
  echo "Hôte déclaré pour ${config##*/}"
  declared=1
done

if [[ "$declared" == 0 ]]; then
  echo "Aucun Chrome ni Chromium trouvé dans ~/.config." >&2
  exit 1
fi
echo
echo "Dans Chrome : chrome://extensions → Mode développeur → Charger l'extension non empaquetée"
echo "→ $root/extension"
