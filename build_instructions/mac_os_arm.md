# Build for macOS (Always Bundle ffprobe)

These steps create a standalone macOS app and always include ffprobe inside the app bundle.

## 1) Prepare build environment

```bash
cd "/Users/legion/Documents/Snowsky Echo Mini Toolbox"

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
  echo "ffprobe is required. Install with: brew install ffmpeg"
  exit 1
fi
```

Copy ffprobe into local build assets:

```bash
mkdir -p build_assets
cp "$(which ffprobe)" build_assets/ffprobe
chmod +x build_assets/ffprobe
```

Prepare app icon (macOS requires .icns):

```bash
cp -f assets/toolbox-logo.png build_assets/toolbox-logo.png

rm -rf build_assets/toolbox-logo.iconset
mkdir -p build_assets/toolbox-logo.iconset

sips -z 16 16   build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_16x16.png
sips -z 32 32   build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_16x16@2x.png
sips -z 32 32   build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_32x32.png
sips -z 64 64   build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_32x32@2x.png
sips -z 128 128 build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_128x128.png
sips -z 256 256 build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_128x128@2x.png
sips -z 256 256 build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_256x256.png
sips -z 512 512 build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_256x256@2x.png
sips -z 512 512 build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_512x512.png
sips -z 1024 1024 build_assets/toolbox-logo.png --out build_assets/toolbox-logo.iconset/icon_512x512@2x.png

iconutil -c icns build_assets/toolbox-logo.iconset -o build_assets/toolbox-logo.icns
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

## 3) Build .app

```bash
pyinstaller \
  --name "Snowsky Echo Mini Toolbox" \
  --icon "build_assets/toolbox-logo.icns" \
  --windowed \
  --noconfirm \
  --clean \
  --collect-all PySide6 \
  --hidden-import certifi \
  --add-binary "build_assets/ffprobe:." \
  --runtime-hook pyi_ffprobe_path_hook.py \
  main.py
```

Output app:

- dist/Snowsky Echo Mini Toolbox.app

## 4) Test app

```bash
open "dist/Snowsky Echo Mini Toolbox.app"
```

## 5) Build DMG

```bash
hdiutil create \
  -volname "Snowsky Echo Mini Toolbox" \
  -srcfolder "dist/Snowsky Echo Mini Toolbox.app" \
  -ov -format UDZO \
  "dist/Snowsky-Echo-Mini-Toolbox-macOS.dmg"
```

Output installer image:

- dist/Snowsky-Echo-Mini-Toolbox-macOS.dmg
