$ErrorActionPreference = 'Stop'
#
# build_release_windows.ps1
# Creates a Windows release: PyInstaller onedir build + Inno Setup installer
# Usage: .\build_instructions\build_release_windows.ps1 [version]

$ROOT_DIR   = (Get-Item "$PSScriptRoot\..").FullName
$APP_NAME   = "Snowsky Echo Mini Toolbox"
$APP_SLUG   = "snowsky-echo-mini-toolbox"
$FFMPEG_DIR = "$ROOT_DIR\build_binaries\windows_x64"

# ---------------------------------------------------------------------------
# 1. Version
# ---------------------------------------------------------------------------
if ($args.Count -gt 0) {
    $VERSION = $args[0]
} else {
    try {
        $VERSION = & python -c "import sys; sys.path.insert(0, r'$ROOT_DIR'); from src.constants import APP_VERSION; print(APP_VERSION)"
    } catch {
        $VERSION = "1.3.2"
    }
}

Write-Host "Root:    $ROOT_DIR"
Write-Host "Version: $VERSION"

Set-Location $ROOT_DIR

# ---------------------------------------------------------------------------
# 2. Verify bundled binaries exist — do NOT search system PATH
# ---------------------------------------------------------------------------
foreach ($BIN in @("ffprobe.exe", "ffmpeg.exe")) {
    $BIN_PATH = "$FFMPEG_DIR\$BIN"
    if (-not (Test-Path $BIN_PATH)) {
        Write-Error "Missing: $BIN_PATH"
        Write-Error "Run scripts/fetch_ffmpeg.py --platform windows_x64 first."
        exit 1
    }
    Write-Host "  Found: $BIN_PATH"
}

# ---------------------------------------------------------------------------
# 3. Python venv
# ---------------------------------------------------------------------------
if (-not (Test-Path .venv-build)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $PYBIN = "py -3.12"
    } else {
        $PYBIN = "python"
    }
    Invoke-Expression "$PYBIN -m venv .venv-build"
}

.\.venv-build\Scripts\Activate.ps1
pip install -r requirements.txt | Out-Null
pip install pyinstaller pillow       | Out-Null

# ---------------------------------------------------------------------------
# 4. Use static .ico from build_assets/icons/windows-icon.ico
#    Do not regenerate during the build; warn if missing
# ---------------------------------------------------------------------------
$ICO_PATH = "build_assets\icons\windows-icon.ico"
if (Test-Path $ICO_PATH) {
    Write-Host "Using static icon: $ICO_PATH"
} else {
    Write-Warning "Static icon not found: $ICO_PATH — installer may use default icon."
}

# ---------------------------------------------------------------------------
# 5. Kill any stale processes that lock build outputs
# ---------------------------------------------------------------------------
foreach ($PROC in @("pyinstaller", $APP_NAME, "ffprobe", "ffmpeg", "ISCC")) {
    Stop-Process -Name $PROC -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# 6. Clean build artifacts only — preserve build_assets
# ---------------------------------------------------------------------------
Remove-Item ".\dist\$APP_NAME"                              -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item ".\dist\$APP_SLUG-Windows-Setup.exe"            -Force   -ErrorAction SilentlyContinue
Remove-Item ".\build_instructions\dist_tmp"                 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item ".\build"                                       -Recurse -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# 7. PyInstaller
# ---------------------------------------------------------------------------
Write-Host "Running PyInstaller..."
pyinstaller --noconfirm --clean "$APP_NAME.spec"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$BUILT_EXE = ".\dist\$APP_NAME\$APP_NAME.exe"
if (-not (Test-Path $BUILT_EXE)) {
    Write-Host "dist contents after PyInstaller:" -ForegroundColor Yellow
    if (Test-Path '.\dist') {
        Get-ChildItem '.\dist' -Force | Select-Object Name, Length, LastWriteTime, Mode | Format-Table -AutoSize
    }
    throw "PyInstaller completed but expected output is missing: $BUILT_EXE"
}

# ---------------------------------------------------------------------------
# 8. Inno Setup
# ---------------------------------------------------------------------------
$ISCC_CANDIDATES = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$ISCC = $ISCC_CANDIDATES | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ISCC) {
    throw "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php"
}

New-Item -ItemType Directory -Force '.\build_instructions\dist_tmp' | Out-Null

$STAMP          = Get-Date -Format 'yyyyMMdd-HHmmss'
$TEMP_FILENAME  = "$APP_SLUG-Windows-Setup-$STAMP"
$TEMP_DIR       = (Resolve-Path ".\build_instructions\dist_tmp").Path

& $ISCC `
    "/DMyOutputDir=$TEMP_DIR" `
    "/DMyOutputBaseFilename=$TEMP_FILENAME" `
    "/DMyAppVersion=$VERSION" `
    ".\build_assets\windows_installer.iss"

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$TEMP_SETUP  = "$TEMP_DIR\$TEMP_FILENAME.exe"
$FINAL_SETUP = ".\dist\$APP_SLUG-$VERSION-Windows-Setup.exe"

if (-not (Test-Path $TEMP_SETUP)) {
    throw "Expected Inno Setup output missing: $TEMP_SETUP"
}

Move-Item $TEMP_SETUP $FINAL_SETUP -Force
Remove-Item '.\build_instructions\dist_tmp' -Recurse -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Done."
Get-Item $FINAL_SETUP | Select-Object FullName, Length, LastWriteTime