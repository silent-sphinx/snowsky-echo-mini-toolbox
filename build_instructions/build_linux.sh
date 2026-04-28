#!/usr/bin/env bash
set -euo pipefail

# Snowsky Echo Mini Toolbox — Linux automated build script
# Produces:
#   dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz
#   dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.deb

VENV=".venv-build"

if [ ! -d "$VENV" ]; then
  python3.12 -m venv "$VENV"
fi

# shellcheck source=/dev/null
source "$VENV/bin/activate"
pip install -r requirements.txt -q
pip install pyinstaller -q

# Locate ffprobe
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ERROR: ffprobe not found. Install with: sudo apt-get install ffmpeg" >&2
  exit 1
fi

mkdir -p build_assets
cp "$(which ffprobe)" build_assets/ffprobe
chmod +x build_assets/ffprobe

# Runtime hook
cat > pyi_ffprobe_path_hook.py <<'PY'
import os
import sys
base = getattr(sys, "_MEIPASS", "")
if base:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = base + os.pathsep + current if current else base
PY

# Clean previous output
rm -rf "dist/Snowsky Echo Mini Toolbox" \
       dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz \
       dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.deb

# Build with PyInstaller
pyinstaller \
  --name "Snowsky Echo Mini Toolbox" \
  --windowed \
  --noconfirm \
  --clean \
  --collect-all PySide6 \
  --hidden-import certifi \
  --add-binary "build_assets/ffprobe:." \
  --runtime-hook pyi_ffprobe_path_hook.py \
  main.py

BUILT_EXE="dist/Snowsky Echo Mini Toolbox/Snowsky Echo Mini Toolbox"
if [ ! -f "$BUILT_EXE" ]; then
  echo "ERROR: PyInstaller output missing: $BUILT_EXE" >&2
  exit 1
fi

# Package as .tar.gz
tar -czf "dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz" \
  -C dist "Snowsky Echo Mini Toolbox"
echo "Created: dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz"

# Package as .deb
PKG_ROOT="dist/deb-staging"
APP_DIR="$PKG_ROOT/opt/snowsky-echo-mini-toolbox"
DESKTOP_DIR="$PKG_ROOT/usr/share/applications"
BIN_DIR="$PKG_ROOT/usr/bin"

rm -rf "$PKG_ROOT"
mkdir -p "$APP_DIR" "$DESKTOP_DIR" "$BIN_DIR"

cp -r "dist/Snowsky Echo Mini Toolbox/." "$APP_DIR/"

ln -s "/opt/snowsky-echo-mini-toolbox/Snowsky Echo Mini Toolbox" \
  "$BIN_DIR/snowsky-echo-mini-toolbox"

cat > "$DESKTOP_DIR/snowsky-echo-mini-toolbox.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Snowsky Echo Mini Toolbox
Exec=/opt/snowsky-echo-mini-toolbox/Snowsky Echo Mini Toolbox
Icon=/opt/snowsky-echo-mini-toolbox/toolbox-logo.png
Categories=AudioVideo;Audio;
DESKTOP

mkdir -p "$PKG_ROOT/DEBIAN"
cat > "$PKG_ROOT/DEBIAN/control" <<'CONTROL'
Package: snowsky-echo-mini-toolbox
Version: 1.0.0
Section: sound
Priority: optional
Architecture: amd64
Maintainer: Snowsky
Description: Snowsky Echo Mini Toolbox
 An easy desktop app to prepare your music folders and USB drives
 for the Snowsky Echo Mini.
CONTROL

dpkg-deb --build "$PKG_ROOT" \
  "dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.deb"

rm -rf "$PKG_ROOT"
echo "Created: dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.deb"
