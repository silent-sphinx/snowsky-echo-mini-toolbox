# Build for Linux (Always Bundle ffprobe)

These steps create a standalone Linux app directory and always include ffprobe in the build output.

## 1) Prepare build environment

From the project root:

```bash
cd /path/to/snowsky-echo-mini-toolbox

# Debian/Ubuntu prerequisite packages:
sudo apt-get update
sudo apt-get install -y python3.12-venv python3-pip ffmpeg

python3.12 -m venv .venv-build
source .venv-build/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

## 2) Prepare bundled ffprobe files

Make sure ffprobe is installed and available:

```bash
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe is required. Install with your distro package manager (for example: sudo apt install ffmpeg)."
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

## 3) Build executable folder

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

Build output:

- dist/Snowsky Echo Mini Toolbox/Snowsky Echo Mini Toolbox

## 4) Test executable

```bash
./dist/"Snowsky Echo Mini Toolbox"/"Snowsky Echo Mini Toolbox"
```

## 5) Package as tar.gz (optional)

```bash
tar -C dist -czf dist/Snowsky-Echo-Mini-Toolbox-linux.tar.gz "Snowsky Echo Mini Toolbox"
```

Archive output:

- dist/Snowsky-Echo-Mini-Toolbox-linux.tar.gz

## 6) Build a Debian package (.deb) (optional)

Create package (version and arch are optional args):

```bash
chmod +x build_instructions/package_deb.sh
./build_instructions/package_deb.sh 0.1.0 amd64
```

Deb output:

- dist/snowsky-echo-mini-toolbox_0.1.0_amd64.deb

## 7) Linux release installation steps

Install runtime dependencies on Debian/Ubuntu target systems:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1
```

Install from tar.gz release:

```bash
mkdir -p "$HOME/.local/opt/snowsky-echo-mini-toolbox"
tar -xzf Snowsky-Echo-Mini-Toolbox-linux.tar.gz -C "$HOME/.local/opt/snowsky-echo-mini-toolbox"
"$HOME/.local/opt/snowsky-echo-mini-toolbox/Snowsky Echo Mini Toolbox/Snowsky Echo Mini Toolbox"
```

Install from .deb release:

```bash
sudo apt-get install -y ./snowsky-echo-mini-toolbox_0.1.0_amd64.deb
snowsky-echo-mini-toolbox
```
