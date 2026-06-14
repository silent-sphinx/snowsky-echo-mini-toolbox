param(
    [string]$Version = "1.4.1"
)
$ErrorActionPreference = 'Stop'
$ROOT_DIR = (Get-Item "$PSScriptRoot\.." ).FullName
$APP_SLUG = "snowsky-echo-mini-toolbox"
$ISCC = "C:\Users\Administrator\AppData\Local\Programs\Inno Setup 6\ISCC.exe"

Set-Location $ROOT_DIR

if (-not (Test-Path $ISCC)) {
    Write-Error "ISCC not found at: $ISCC"
    exit 1
}

New-Item -ItemType Directory -Force '.\build_instructions\dist_tmp' | Out-Null
$STAMP = Get-Date -Format 'yyyyMMdd-HHmmss'
$TEMP_FILENAME = "$APP_SLUG-Windows-Setup-$STAMP"
$TEMP_DIR = (Resolve-Path ".\build_instructions\dist_tmp").Path

& $ISCC "/DMyOutputDir=$TEMP_DIR" "/DMyOutputBaseFilename=$TEMP_FILENAME" "/DMyAppVersion=$Version" ".\build_assets\windows_installer.iss"

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$TEMP_SETUP = Join-Path $TEMP_DIR "$TEMP_FILENAME.exe"
$FINAL_SETUP = Join-Path "$ROOT_DIR\dist" "$APP_SLUG-$Version-Windows-Setup.exe"

if (-not (Test-Path $TEMP_SETUP)) {
    throw "Expected Inno Setup output missing: $TEMP_SETUP"
}

New-Item -ItemType Directory -Force '.\dist' | Out-Null
Move-Item $TEMP_SETUP $FINAL_SETUP -Force
Remove-Item '.\build_instructions\dist_tmp' -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Done. Created installer: $FINAL_SETUP"
