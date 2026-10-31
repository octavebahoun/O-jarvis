#!/usr/bin/env bash
# Paquets de la version : .deb et AppImage, avec le moteur embarqué (PyInstaller).
# Tauri construit les deux paquets sans le moteur, qui y est ensuite ajouté tel quel :
# Tauri recopierait ses liens symboliques en fichiers (+300 Mo) et linuxdeploy
# retoucherait ses bibliothèques, déjà complètes.
# Résultat : app/src-tauri/target/release/bundle/{deb,appimage}/
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bundle="$root/app/src-tauri/target/release/bundle"
engine="$root/engine/dist/ojarvis-engine"

# OJARVIS_MOTEUR_PRET=1 : réutilise le moteur déjà construit.
if [[ "${OJARVIS_MOTEUR_PRET:-0}" != "1" ]]; then
  "$root/scripts/build-engine.sh"
fi
if [[ ! -x "$engine/ojarvis-engine" ]]; then
  echo "Moteur introuvable : $engine (lancer scripts/build-engine.sh)" >&2
  exit 1
fi

cd "$root/app"
pnpm install --frozen-lockfile
# Tauri réutilise ses dossiers de travail (AppDir, appimage_deb) : on repart de zéro.
rm -rf "$bundle"
pnpm tauri build --bundles deb,appimage
version="$(node -p "require('./package.json').version")"
deb="$bundle/deb/O-jarvis_${version}_amd64.deb"
image="$bundle/appimage/O-jarvis_${version}_amd64.AppImage"

work="$(mktemp -d "$HOME/.cache/ojarvis-release.XXXXXX")"
trap 'rm -rf "$work"' EXIT

# .deb : ouvert, complété, sommes de contrôle et taille installée recalculées, refermé.
dpkg-deb --raw-extract "$deb" "$work/deb"
mkdir -p "$work/deb/usr/lib/O-jarvis"
cp -a "$engine" "$work/deb/usr/lib/O-jarvis/engine"
# La commande du mode shell, à côté de celle de l'app (o-jarvis).
install -m 755 "$root/app/src-tauri/packaging/o-jarvis-shell" "$work/deb/usr/bin/o-jarvis-shell"
chmod -R go-w "$work/deb"
(cd "$work/deb" && find usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums)
size="$(du -sk --apparent-size --exclude=DEBIAN "$work/deb" | cut -f1)"
sed -i "s/^Installed-Size: .*/Installed-Size: $size/" "$work/deb/DEBIAN/control"
dpkg-deb --root-owner-group -Zxz --threads-max="$(nproc)" --build "$work/deb" "$deb"

# AppImage : extraite, complétée, refermée par le greffon AppImage de linuxdeploy. Son lanceur
# devient celui du .deb : visible dans les menus, au nom qu'attend le portail du bureau.
plugins=("$HOME"/.cache/tauri/linuxdeploy-plugin-appimage*.AppImage)
plugin="${plugins[0]}"
if [[ ! -x "$plugin" ]]; then
  echo "Greffon AppImage de linuxdeploy introuvable dans ~/.cache/tauri" >&2
  exit 1
fi
(cd "$work" && "$image" --appimage-extract > /dev/null)
appdir="$work/squashfs-root"
mkdir -p "$appdir/usr/lib/O-jarvis"
cp -a "$engine" "$appdir/usr/lib/O-jarvis/engine"
desktop="io.github.octavebahoun.ojarvis.desktop"
rm -f "$appdir"/*.desktop "$appdir/usr/share/applications/O-jarvis.desktop"
cp "$root/app/src-tauri/packaging/$desktop" "$appdir/usr/share/applications/$desktop"
ln -s "usr/share/applications/$desktop" "$appdir/$desktop"
rm -f "$image"
(cd "$work" && ARCH=x86_64 OUTPUT="$image" LDAI_OUTPUT="$image" \
  "$plugin" --appimage-extract-and-run --appdir squashfs-root > /dev/null)

ls -lh "$deb" "$image"
