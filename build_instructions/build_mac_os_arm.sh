#!/usr/bin/env bash
set -euo pipefail
#
# release_mac_os.sh
# Creates a macOS release: PyInstaller app bundle + DMG installer
# Usage: ./build_instructions/release_mac_os.sh [version]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-1.3.2}"
VENV_DIR="$ROOT_DIR/.venv-build"
APP_NAME="Snowsky Echo Mini Toolbox"
APP_SLUG="snowsky-echo-mini-toolbox"
ARCH="$(uname -m)"   # arm64 or x86_64

echo "Root:    $ROOT_DIR"
echo "Version: $VERSION"
echo "Arch:    $ARCH"

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
PYBIN="python3.12"
command -v "$PYBIN" >/dev/null 2>&1 || PYBIN="python3"
echo "Python:  $PYBIN"

cd "$ROOT_DIR"

# ---------------------------------------------------------------------------
# 2. Verify bundled binaries exist and are the correct architecture
# ---------------------------------------------------------------------------
FFMPEG_DIR="build_binaries/macos_silicon"
if [ "$ARCH" = "x86_64" ]; then
    FFMPEG_DIR="build_binaries/macos_intel"
fi

for BIN in ffprobe ffmpeg; do
    BIN_PATH="$FFMPEG_DIR/$BIN"
    if [ ! -f "$BIN_PATH" ]; then
        echo "Missing: $BIN_PATH" >&2
        exit 1
    fi
    # Verify architecture matches
    BIN_ARCH="$(lipo -archs "$BIN_PATH" 2>/dev/null || file "$BIN_PATH")"
    echo "  $BIN: $BIN_ARCH"
done

# ---------------------------------------------------------------------------
# 3. Clean build artifacts only — preserve build_assets
# ---------------------------------------------------------------------------
rm -rf build/ dist/ "$VENV_DIR"

# ---------------------------------------------------------------------------
# 4. Venv + dependencies
# ---------------------------------------------------------------------------
"$PYBIN" -m venv "$VENV_DIR"
. "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

# ---------------------------------------------------------------------------
# 5. PyInstaller
# ---------------------------------------------------------------------------
echo "Running PyInstaller..."
pyinstaller --noconfirm --clean "$APP_NAME.spec"

# ---------------------------------------------------------------------------
# 6. DMG
# ---------------------------------------------------------------------------
echo "Creating DMG..."

# Staging folder with Applications symlink for drag-to-install UX
STAGE_DIR="dist/dmg_stage"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "dist/$APP_NAME.app" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"

hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$STAGE_DIR" \
    -ov -format UDZO \
    "dist/${APP_SLUG}-${VERSION}-macOS.dmg"

rm -rf "$STAGE_DIR"

echo ""
echo "Done."
ls -lh "dist/${APP_SLUG}-${VERSION}-macOS.dmg"