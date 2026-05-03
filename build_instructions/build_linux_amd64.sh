#!/usr/bin/env bash
set -euo pipefail
#
# build_release_linux.sh
# Creates a Linux release: onedir PyInstaller build, tar.gz, and .deb
# Usage: ./build_instructions/build_release_linux.sh [version] [arch]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(python3 -c "import sys; sys.path.insert(0, '$ROOT_DIR'); from src.constants import APP_VERSION; print(APP_VERSION)" 2>/dev/null || echo "1.3.2")}"
ARCH="${2:-$(dpkg --print-architecture 2>/dev/null || echo amd64)}"
VENV_DIR="$ROOT_DIR/.venv-build"
APP_NAME="Snowsky Echo Mini Toolbox"
APP_SLUG="snowsky-echo-mini-toolbox"
FFMPEG_DIR="$ROOT_DIR/build_binaries/linux_amd64"

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
# 2. Verify bundled binaries exist — do NOT pull from system PATH
# ---------------------------------------------------------------------------
for BIN in ffprobe ffmpeg; do
    BIN_PATH="$FFMPEG_DIR/$BIN"
    if [[ ! -f "$BIN_PATH" ]]; then
        echo "Missing: $BIN_PATH" >&2
        exit 1
    fi
    if [[ ! -x "$BIN_PATH" ]]; then
        chmod +x "$BIN_PATH"
    fi
    echo "  Found: $BIN_PATH"
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

if [[ ! -d "dist/$APP_NAME" ]]; then
    echo "PyInstaller output missing: dist/$APP_NAME" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 6. tar.gz
# ---------------------------------------------------------------------------
echo "Creating tar.gz..."
tar -C dist -czf "dist/${APP_SLUG}-${VERSION}-linux-amd64.tar.gz" "$APP_NAME"

# ---------------------------------------------------------------------------
# 7. Debian package
# ---------------------------------------------------------------------------
echo "Building Debian package..."

DIST_APP_DIR="$ROOT_DIR/dist/$APP_NAME"
PKG_ROOT="$ROOT_DIR/dist/deb_pkg_root"
PKG_NAME="${APP_SLUG}_${VERSION}_${ARCH}.deb"
PKG_PATH="$ROOT_DIR/dist/$PKG_NAME"

rm -rf "$PKG_ROOT"
mkdir -p \
    "$PKG_ROOT/DEBIAN" \
    "$PKG_ROOT/opt/$APP_SLUG" \
    "$PKG_ROOT/usr/bin"

# App binaries
cp -a "$DIST_APP_DIR/." "$PKG_ROOT/opt/$APP_SLUG/"

# Launcher wrapper
cat > "$PKG_ROOT/usr/bin/$APP_SLUG" <<'SH'
#!/usr/bin/env bash
exec "/opt/snowsky-echo-mini-toolbox/Snowsky Echo Mini Toolbox" "$@"
SH
chmod 0755 "$PKG_ROOT/usr/bin/$APP_SLUG"

# Control file
cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: $APP_SLUG
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Silent Sphinx <115553492+silent-sphinx@users.noreply.github.com>
Depends: libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-xkb1
Description: Snowsky Echo Mini Toolbox
 Desktop tool for preparing music folders and USB drives for Snowsky Echo Mini.
EOF

dpkg-deb --build "$PKG_ROOT" "$PKG_PATH"
rm -rf "$PKG_ROOT"

# ---------------------------------------------------------------------------
# 8. Summary
# ---------------------------------------------------------------------------
echo ""
echo "Done."
ls -lh "dist/${APP_SLUG}-${VERSION}-linux-amd64.tar.gz" || true
ls -lh "dist/${APP_SLUG}_${VERSION}_${ARCH}.deb" || true