# Build Installer for Windows (Always Bundle ffprobe)

These steps create:

- a standalone app folder (`dist\Snowsky Echo Mini Toolbox\...`)
- a Windows installer (`dist\Snowsky-Echo-Mini-Toolbox-Windows-Setup.exe`)

## 1) Prepare build environment

Open PowerShell in the project root:

```powershell
cd "C:\path\to\snowsky-echo-mini-toolbox"

py -3.12 -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller pillow
```

Install Inno Setup (one-time):

```powershell
winget install --id JRSoftware.InnoSetup -e
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

Prepare app icon (Windows uses `.ico`):

```powershell
Copy-Item "assets\toolbox-logo.png" "build_assets\toolbox-logo.png" -Force

@'
from PIL import Image

img = Image.open("build_assets/toolbox-logo.png").convert("RGBA")
img.save(
  "build_assets/toolbox-logo.ico",
  format="ICO",
  sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)],
)
'@ | .\.venv-build\Scripts\python.exe -
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

## 3) Build executable folder (PyInstaller)

```powershell
pyinstaller `
  --name "Snowsky Echo Mini Toolbox" `
  --icon "build_assets\toolbox-logo.ico" `
  --windowed `
  --noconfirm `
  --clean `
  --hidden-import certifi `
  --add-binary "build_assets\ffprobe.exe;." `
  --runtime-hook pyi_ffprobe_path_hook.py `
  main.py
```

Build output:

- `dist\Snowsky Echo Mini Toolbox\Snowsky Echo Mini Toolbox.exe`

## 4) Build installer (Inno Setup)

Update app version in `build_instructions\windows_installer.iss` before release:

```iss
#define MyAppVersion "1.0.0"
```

Compile installer:

```powershell
$isccCandidates = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)

$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "ISCC.exe not found. Confirm Inno Setup is installed."
}

& $iscc ".\build_instructions\windows_installer.iss"
```

Installer output:

- `dist\Snowsky-Echo-Mini-Toolbox-Windows-Setup.exe`

## 5) Test

Run the portable executable:

```powershell
.\dist\"Snowsky Echo Mini Toolbox"\"Snowsky Echo Mini Toolbox.exe"
```

Run installer:

```powershell
.\dist\Snowsky-Echo-Mini-Toolbox-Windows-Setup.exe
```
