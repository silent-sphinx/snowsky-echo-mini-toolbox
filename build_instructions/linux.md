# Build for Linux x86-64 (Always Bundle ffprobe)

These steps create:

- a portable `.tar.gz` archive (`dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz`)
- a Debian package (`dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.deb`)

## 1) Prepare build environment

Install required system dependencies:

```bash
sudo apt-get update && sudo apt-get install -y \
  python3.12 python3.12-venv python3-pip ffmpeg \
  libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1
```

Create and activate a virtual environment:

```bash
cd /path/to/snowsky-echo-mini-toolbox

python3.12 -m venv .venv-build
source .venv-build/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

## 2) Prepare bundled ffprobe files

Make sure ffprobe is installed and available:

```bash
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe is required. Install with: sudo apt-get install ffmpeg"
  exit 1
fi
```

Copy ffprobe into local build assets:

```bash
mkdir -p build_assets
cp "$(which ffprobe)" build_assets/ffprobe
chmod +x build_assets/ffprobe
```

Create runtime hook so bundled ffprobe is available on PATH:

```bash
cat > pyi_ffprobe_path_hook.py <<'PY'
import os
import sys

base = getattr(sys, "_MEIPASS", "")
if base:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = base + os.pathsep + current if current else base
PY
```

## 3) Build executable (PyInstaller)

```bash
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
```

Build output directory:

- `dist/Snowsky Echo Mini Toolbox/`

## 4) Test executable

```bash
"dist/Snowsky Echo Mini Toolbox/Snowsky Echo Mini Toolbox"
```

## 5) Package as .tar.gz

Copy the app icon into the output directory so it is included in all packages:

```bash
cp assets/toolbox-logo.png "dist/Snowsky Echo Mini Toolbox/toolbox-logo.png"
```

Then create the archive:

```bash
tar -czf "dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz" \
  -C dist "Snowsky Echo Mini Toolbox"
```

Output archive:

- `dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.tar.gz`

## 6) Package as .deb

Install `dpkg-deb` (part of `dpkg`):

```bash
sudo apt-get install -y dpkg
```

Create the package structure and build the `.deb`:

```bash
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
```

Output package:

- `dist/Snowsky-Echo-Mini-Toolbox-Linux-x86_64.deb`
