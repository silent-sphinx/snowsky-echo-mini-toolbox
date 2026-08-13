# -*- mode: python ; coding: utf-8 -*-
#
# Snowsky Echo Mini Toolbox — PyInstaller spec
#
# Usage:
#   pyinstaller "Snowsky Echo Mini Toolbox.spec"
#
# Supports: macOS (Apple Silicon + Intel), Windows (x64), Linux (x64)
#
# If you add new PySide6 imports to src/, add the module to
# PYSIDE6_MODULES below. Check with:
#   grep -r "from PySide6" src/
#
# Notes:
#   - UPX must be installed separately on Linux: apt install upx
#   - Linux app icons are set via a .desktop file, not the executable

import sys
import platform
from PyInstaller.utils.hooks import collect_all


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def _get_ffmpeg_asset_dir() -> str:
    if sys.platform == 'darwin':
        machine = platform.machine()  # 'arm64' or 'x86_64'
        if machine == 'arm64':
            return 'build_binaries/macos_silicon'
        return 'build_binaries/macos_intel'
    if sys.platform == 'linux':
        return 'build_binaries/linux_amd64'
    if sys.platform == 'win32':
        return 'build_binaries/windows_x64'
    raise RuntimeError(f'Unsupported platform: {sys.platform}')


def _get_icon():
    if sys.platform == 'win32':
        return ['build_assets/icons/windows-icon.ico']
    return ['build_assets/icons/mac-icon.icns']


# strip is unsupported on Windows — PyInstaller warns and ignores it,
# but we guard it explicitly to keep the spec clean.
_strip = sys.platform != 'win32'

ffmpeg_asset_dir = _get_ffmpeg_asset_dir()


# ---------------------------------------------------------------------------
# WebEngine exclusion
#
# Two-layered approach:
#   1. _filter_webengine_entries — strips WebEngine from anything collect_all
#      pulls in transitively.
#   2. `excludes` in Analysis — prevents PyInstaller's own auto-discovery
#      from pulling WebEngine back in.
# Both layers are needed; neither alone is sufficient.
# ---------------------------------------------------------------------------

WEBENGINE_BLOCKLIST = (
    'QtWebEngine',
    'QtWebView',
    'QtWebChannel',
)


def _uses_webengine(value) -> bool:
    if isinstance(value, str):
        return any(blocked in value for blocked in WEBENGINE_BLOCKLIST)
    if isinstance(value, (list, tuple)):
        return any(_uses_webengine(item) for item in value)
    return False


def _filter_webengine_entries(entries):
    return [entry for entry in entries if not _uses_webengine(entry)]


# ---------------------------------------------------------------------------
# PySide6 selective collection
#
# Only collect the modules actually used by src/.
# Add modules here if new PySide6 imports are introduced:
#   grep -r "from PySide6" src/
# ---------------------------------------------------------------------------

PYSIDE6_MODULES = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]


def _collect_pyside_modules(module_names):
    datas = []
    binaries = []
    hiddenimports = []
    for module_name in module_names:
        module_datas, module_binaries, module_hiddenimports = collect_all(module_name)
        datas.extend(module_datas)
        binaries.extend(module_binaries)
        hiddenimports.extend(module_hiddenimports)
    return datas, binaries, hiddenimports


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

pyside_datas, pyside_binaries, pyside_hiddenimports = _collect_pyside_modules(
    PYSIDE6_MODULES
)

datas = [] + _filter_webengine_entries(pyside_datas)

# Add platform-specific binary extensions
_exe_ext = '.exe' if sys.platform == 'win32' else ''

binaries = [
    (f'{ffmpeg_asset_dir}/ffprobe{_exe_ext}', '.'),
    (f'{ffmpeg_asset_dir}/ffmpeg{_exe_ext}', '.'),
] + _filter_webengine_entries(pyside_binaries)

hiddenimports = [
    'certifi',
] + _filter_webengine_entries(pyside_hiddenimports)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['build_assets/hooks/pyi_ffprobe_path_hook.py'],
    # Prevent PyInstaller's auto-discovery from pulling WebEngine back in.
    # (collect_all filtering above handles transitive pulls.)
    excludes=[
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebView',
        'PySide6.QtWebChannel',
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Snowsky Echo Mini Toolbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=_strip,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,   # macOS only: set to 'universal2' for a fat binary
                        # (requires universal2 Python + universal2 PySide6 wheels)
    codesign_identity=None,
    entitlements_file=None,
    icon=_get_icon(),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=_strip,
    upx=True,
    upx_exclude=[],
    name='Snowsky Echo Mini Toolbox',
)

# ---------------------------------------------------------------------------
# BUNDLE — macOS only (.app package)
# Windows and Linux do not use this block.
# ---------------------------------------------------------------------------

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Snowsky Echo Mini Toolbox.app',
        icon='build_assets/icons/mac-icon.icns',
        bundle_identifier='com.snowsky.echo-mini-toolbox',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '12.0',
        },
    )