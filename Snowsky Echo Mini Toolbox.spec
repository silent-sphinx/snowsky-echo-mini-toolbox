# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


WEBENGINE_BLOCKLIST = (
    'QtWebEngine',
    'QtWebView',
)


def _uses_webengine(value) -> bool:
    if isinstance(value, str):
        return any(blocked in value for blocked in WEBENGINE_BLOCKLIST)

    if isinstance(value, (list, tuple)):
        return any(_uses_webengine(item) for item in value)

    return False


def _filter_webengine_entries(entries):
    return [entry for entry in entries if not _uses_webengine(entry)]


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


datas = []
binaries = [('build_assets/ffprobe', '.')]
hiddenimports = ['certifi']
pyside_datas, pyside_binaries, pyside_hiddenimports = _collect_pyside_modules(
    [
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ]
)
datas += _filter_webengine_entries(pyside_datas)
binaries += _filter_webengine_entries(pyside_binaries)
hiddenimports += _filter_webengine_entries(pyside_hiddenimports)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_ffprobe_path_hook.py'],
    excludes=[
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebView',
        'PySide6.QtWebChannel',
    ],
    noarchive=False,
    optimize=0,
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
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['build_assets/app_icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Snowsky Echo Mini Toolbox',
)
app = BUNDLE(
    coll,
    name='Snowsky Echo Mini Toolbox.app',
    icon='build_assets/app_icon.icns',
    bundle_identifier=None,
)
