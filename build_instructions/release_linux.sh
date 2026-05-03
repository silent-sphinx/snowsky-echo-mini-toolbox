#!/usr/bin/env bash
set -euo pipefail

# build_release_linux.sh
# Creates a reproducible Linux release: onedir PyInstaller build, tar.gz, and .deb
# Usage: ./build_instructions/build_release_linux.sh [version] [arch]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-1.3.2}"
ARCH="${2:-$(dpkg --print-architecture 2>/dev/null || echo amd64)}"
VENV_DIR="$ROOT_DIR/.venv-build"
APP_NAME="Snowsky Echo Mini Toolbox"
APP_SLUG="snowsky-echo-mini-toolbox"

echo "Root: $ROOT_DIR"
echo "Version: $VERSION  Arch: $ARCH"

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

echo "Running PyInstaller with optimized spec (this may take a while)..."
pyinstaller --noconfirm --clean Snowsky\ Echo\ Mini\ Toolbox.spec

echo "Creating tar.gz release..."
tar -C dist -czf "dist/${APP_SLUG}-${VERSION}-linux.tar.gz" "$APP_NAME"

echo "Building Debian package (requires dpkg-deb)..."

# Inline Debian packaging (replaces build_instructions/package_deb.sh)
APP_NAME="$APP_NAME"
APP_SLUG="$APP_SLUG"
DIST_APP_DIR="$ROOT_DIR/dist/$APP_NAME"
PKG_ROOT="$ROOT_DIR/dist/deb_pkg_root"
PKG_NAME="${APP_SLUG}_${VERSION}_${ARCH}.deb"
PKG_PATH="$ROOT_DIR/dist/$PKG_NAME"

if [[ ! -d "$DIST_APP_DIR" ]]; then
  echo "Missing build output: $DIST_APP_DIR"
  echo "Run the Linux PyInstaller build first."
  exit 1
fi

rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/$APP_SLUG"
mkdir -p "$PKG_ROOT/usr/bin"

cp -a "$DIST_APP_DIR/." "$PKG_ROOT/opt/$APP_SLUG/"

cat > "$PKG_ROOT/usr/bin/$APP_SLUG" <<'SH'
#!/usr/bin/env bash
exec /opt/snowsky-echo-mini-toolbox/Snowsky\ Echo\ Mini\ Toolbox "$@"
SH
chmod 0755 "$PKG_ROOT/usr/bin/$APP_SLUG"

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: $APP_SLUG
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Snowsky Echo Mini Toolbox Maintainers <noreply@example.com>
Depends: ffmpeg, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-xkb1
Description: Snowsky Echo Mini Toolbox desktop application
 Desktop tool for preparing music folders and USB drives for Snowsky Echo Mini.
EOF

dpkg-deb --build "$PKG_ROOT" "$PKG_PATH"

echo "Built Debian package: $PKG_PATH"

echo "Release artifacts:"
ls -lh "dist/${APP_SLUG}-${VERSION}-linux.tar.gz" || true
ls -lh "dist/${APP_SLUG}_${VERSION}_${ARCH}.deb" || true

echo "Done. Artifacts saved in dist/."
