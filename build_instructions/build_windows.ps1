$ErrorActionPreference = 'Stop'

if (-not (Test-Path .venv-build)) {
  py -3.12 -m venv .venv-build
}

.\.venv-build\Scripts\Activate.ps1
pip install -r requirements.txt | Out-Null
pip install pyinstaller pillow | Out-Null

$ffprobeCandidates = @(
  "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffprobe.exe",
  "$env:LOCALAPPDATA\Programs\ffmpeg\bin\ffprobe.exe"
)
$ffprobeCandidates += (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffprobe.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$ffprobePath = $ffprobeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $ffprobePath) {
  throw "ffprobe.exe not found"
}

New-Item -ItemType Directory -Force build_assets | Out-Null
Copy-Item $ffprobePath build_assets\ffprobe.exe -Force
Copy-Item assets\toolbox-logo.png build_assets\toolbox-logo.png -Force

@'
from PIL import Image
img = Image.open('build_assets/toolbox-logo.png').convert('RGBA')
img.save('build_assets/toolbox-logo.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
'@ | .\.venv-build\Scripts\python.exe -

@'
import os
import sys
base = getattr(sys, '_MEIPASS', '')
if base:
    current = os.environ.get('PATH', '')
    os.environ['PATH'] = base + os.pathsep + current if current else base
'@ | Set-Content -Encoding UTF8 pyi_ffprobe_path_hook.py

Stop-Process -Name pyinstaller -Force -ErrorAction SilentlyContinue
Stop-Process -Name "Snowsky Echo Mini Toolbox" -Force -ErrorAction SilentlyContinue
Stop-Process -Name ffprobe -Force -ErrorAction SilentlyContinue
Stop-Process -Name ISCC -Force -ErrorAction SilentlyContinue

Remove-Item '.\dist\Snowsky Echo Mini Toolbox' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item '.\dist\Snowsky-Echo-Mini-Toolbox-Windows-Setup.exe' -Force -ErrorAction SilentlyContinue
Remove-Item '.\build_instructions\dist_tmp' -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force '.\build_instructions\dist_tmp' | Out-Null

pyinstaller --name "Snowsky Echo Mini Toolbox" --icon build_assets\toolbox-logo.ico --windowed --noconfirm --clean --collect-all PySide6 --hidden-import certifi --add-binary "build_assets\ffprobe.exe;." --runtime-hook pyi_ffprobe_path_hook.py main.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$builtExe = '.\dist\Snowsky Echo Mini Toolbox\Snowsky Echo Mini Toolbox.exe'
if (-not (Test-Path $builtExe)) {
  Write-Host "dist contents after PyInstaller:" -ForegroundColor Yellow
  if (Test-Path '.\dist') {
    Get-ChildItem '.\dist' -Force | Select-Object Name,Length,LastWriteTime,Mode | Format-Table -AutoSize
  }
  throw "PyInstaller completed but expected output is missing: $builtExe"
}

$isccCandidates = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  throw "ISCC.exe not found"
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$tempOutputName = "Snowsky-Echo-Mini-Toolbox-Windows-Setup-$stamp"

& $iscc "/DMyOutputDir=.\\build_instructions\\dist_tmp" "/DMyOutputBaseFilename=$tempOutputName" .\build_instructions\windows_installer.iss
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup compile failed with exit code $LASTEXITCODE"
}

$tempSetup = ".\\build_instructions\\dist_tmp\\$tempOutputName.exe"
$finalSetup = '.\dist\Snowsky-Echo-Mini-Toolbox-Windows-Setup.exe'
if (-not (Test-Path $tempSetup)) {
  throw "Expected temporary setup output missing: $tempSetup"
}

Move-Item $tempSetup $finalSetup -Force
Remove-Item '.\build_instructions\dist_tmp' -Recurse -Force -ErrorAction SilentlyContinue

Get-Item $finalSetup | Select-Object FullName,Length,LastWriteTime
