#!/usr/bin/env bash
set -euo pipefail

# release_mac_os.sh
# Creates a macOS release: PyInstaller app bundle and DMG installer
# Usage: ./build_instructions/release_mac_os.sh [version]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-1.3.2}"
VENV_DIR="$ROOT_DIR/.venv-build"
APP_NAME="Snowsky Echo Mini Toolbox"
APP_SLUG="snowsky-echo-mini-toolbox"

echo "Root: $ROOT_DIR"
echo "Version: $VERSION"

PYBIN="python3.12"
if ! command -v "$PYBIN" >/dev/null 2>&1; then
  PYBIN="python3"
fi

echo "Using Python: $PYBIN"

cd "$ROOT_DIR"

# Ensure ffprobe is available before attempting to bundle it
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe not found in PATH. Install ffmpeg (ffprobe) on your system first." >&2
  exit 1
fi

rm -rf build/ dist/ build_assets/ "$VENV_DIR"

"$PYBIN" -m venv "$VENV_DIR"
. "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

mkdir -p build_assets
cp "$(which ffprobe)" build_assets/ffprobe
chmod +x build_assets/ffprobe

# runtime hook
cat > pyi_ffprobe_path_hook.py <<'PY'
import os
import sys

base = getattr(sys, "_MEIPASS", "")
if base:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = base + os.pathsep + current if current else base
PY

echo "Running PyInstaller with app spec (this may take a while)..."
pyinstaller --noconfirm --clean "Snowsky Echo Mini Toolbox.spec"

echo "Creating DMG release..."
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/$APP_NAME.app" \
  -ov -format UDZO \
  "dist/${APP_SLUG}-${VERSION}-macOS.dmg"

echo "Release artifacts:"
ls -lh "dist/${APP_SLUG}-${VERSION}-macOS.dmg" || true

echo "Done. Installer saved in dist/."
echo "Version: $VERSION"
