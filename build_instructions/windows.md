# Build for Windows (Always Bundle ffprobe)

These steps create a standalone Windows app and always include ffprobe in the build output.

## 1) Prepare build environment

Open PowerShell in the project root:

```powershell
cd "C:\path\to\Snowsky Echo Mini Toolbox"

py -3.12 -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

## 2) Prepare bundled ffprobe files

Make sure ffprobe is installed and available on PATH:

```powershell
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "ffprobe is required. Install FFmpeg first and ensure ffprobe is on PATH."
}
```

Copy ffprobe to local build assets:

```powershell
New-Item -ItemType Directory -Force build_assets | Out-Null
$ffprobePath = (Get-Command ffprobe).Source
Copy-Item $ffprobePath "build_assets\ffprobe.exe" -Force
```

Create runtime hook so bundled ffprobe is available on PATH:

```powershell
@'
import os
import sys

base = getattr(sys, "_MEIPASS", "")
if base:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = base + os.pathsep + current if current else base
'@ | Set-Content -Encoding UTF8 pyi_ffprobe_path_hook.py
```

## 3) Build executable

```powershell
pyinstaller `
  --name "Snowsky Echo Mini Toolbox" `
  --windowed `
  --noconfirm `
  --clean `
  --collect-all PySide6 `
  --hidden-import certifi `
  --add-binary "build_assets\ffprobe.exe;." `
  --runtime-hook pyi_ffprobe_path_hook.py `
  main.py
```

Build output:

- dist\Snowsky Echo Mini Toolbox\Snowsky Echo Mini Toolbox.exe

## 4) Test executable

```powershell
.\dist\"Snowsky Echo Mini Toolbox"\"Snowsky Echo Mini Toolbox.exe"
```
