"""Helpers for safely ejecting mounted volumes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time


_SYSTEM_ROOTS = {"/", "C:\\", "C:/"}
_SYSTEM_VOLUME_NAMES = frozenset({"Macintosh HD", "Recovery", "Preboot", "Update", "VM"})


def _subprocess_no_window_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}

    kwargs: dict[str, object] = {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is not None:
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs


def _run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        **_subprocess_no_window_kwargs(),
    )


def _command_error(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    return (result.stderr or result.stdout or fallback).strip()


def _is_system_root(path: str) -> bool:
    normalized = os.path.normpath(path)
    return normalized in _SYSTEM_ROOTS or normalized.upper() == "C:\\"


def _volume_still_mounted(path: str) -> bool:
    from PySide6.QtCore import QStorageInfo

    info = QStorageInfo(path)
    return bool(info.isValid() and info.isReady())


def _eject_macos(path: str, device: str) -> tuple[bool, str]:
    attempts = [["diskutil", "eject", path]]
    if device:
        attempts.append(["diskutil", "eject", device])
    attempts.append(["diskutil", "unmount", path])

    last_error = "Failed to eject the drive."
    for command in attempts:
        try:
            result = _run(command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue
        if result.returncode == 0:
            return True, ""
        last_error = _command_error(result, last_error)
    return False, last_error


def _eject_windows(path: str) -> tuple[bool, str]:
    drive = os.path.splitdrive(path)[0]
    if not drive:
        return False, "Could not determine the drive letter."

    script = (
        f"$item = (New-Object -ComObject Shell.Application).NameSpace(17).ParseName('{drive}'); "
        "if (-not $item) { exit 2 }; "
        "$item.InvokeVerb('Eject')"
    )
    try:
        result = _run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=40,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    if result.returncode != 0:
        return False, _command_error(result, "Failed to eject the drive.")

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _volume_still_mounted(path):
            return True, ""
        time.sleep(0.2)

    if _volume_still_mounted(path):
        return False, "The drive is still mounted. Close any apps using it and try again."
    return True, ""


def _eject_linux(path: str, device: str) -> tuple[bool, str]:
    attempts: list[list[str]] = []
    if shutil.which("udisksctl"):
        attempts.append(["udisksctl", "unmount", "--path", path])
        if device:
            attempts.append(["udisksctl", "unmount", "--block-device", device])
    if shutil.which("gio"):
        attempts.append(["gio", "mount", "--unmount", path])
    if shutil.which("umount"):
        attempts.append(["umount", path])

    if not attempts:
        return False, "No unmount tool was found (udisksctl, gio, or umount)."

    last_error = "Failed to unmount the drive."
    for command in attempts:
        try:
            result = _run(command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue
        if result.returncode == 0:
            return True, ""
        last_error = _command_error(result, last_error)
    return False, last_error


def is_ejectable_volume(root: str, name: str = "") -> bool:
    """True when *root* is a non-system volume that can be safely ejected."""
    if not root or _is_system_root(root):
        return False
    if name in _SYSTEM_VOLUME_NAMES:
        return False
    if root.startswith("/System/Volumes"):
        return False
    return True


def removable_volume_for_path(path: str) -> tuple[str, str, str] | None:
    """Return (root, name, device) if *path* lives on a removable volume."""
    if not path:
        return None

    from PySide6.QtCore import QStorageInfo

    info = QStorageInfo(path)
    if not info.isValid() or not info.isReady():
        return None

    root = info.rootPath()
    name = info.name() or os.path.basename(root.rstrip("\\/")) or root
    if not is_ejectable_volume(root, name):
        return None

    raw = info.device()
    device = bytes(raw).decode("utf-8", errors="ignore") if raw else ""
    return root, name, device


def eject_volume(path: str, device: str = "") -> tuple[bool, str]:
    """Unmount or eject the volume at *path*. Returns (ok, error_message)."""
    if not path or _is_system_root(path):
        return False, "The system volume cannot be ejected."

    if sys.platform == "darwin":
        return _eject_macos(path, device)
    if sys.platform == "win32":
        return _eject_windows(path)
    return _eject_linux(path, device)
