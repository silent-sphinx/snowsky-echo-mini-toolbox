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
