import ctypes
import logging
import os
import plistlib
import platform
import shutil
import subprocess
import time
from pathlib import Path

from .models import DriveOption
from .music_compatibility import _subprocess_no_window_kwargs


logger = logging.getLogger(__name__)


def format_bytes(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size_bytes} B"


def top_level_counts(path: str) -> tuple[str, str]:
    try:
        file_count = 0
        dir_count = 0
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    file_count += 1
                elif entry.is_dir(follow_symlinks=False):
                    dir_count += 1
        return str(file_count), str(dir_count)
    except (PermissionError, OSError):
        return "Unknown", "Unknown"


def _mounted_directories(root: str) -> list[str]:
    mounts: list[str] = []
    root_path = Path(root)
    if not root_path.exists() or not root_path.is_dir():
        return mounts

    try:
        children = list(root_path.iterdir())
    except (PermissionError, OSError):
        return mounts

    for child in children:
        if child.is_dir() and os.path.ismount(str(child)):
            mounts.append(str(child))

    # Linux often nests mount points under /media/<user>/<label>
    for child in children:
        if not child.is_dir() or os.path.ismount(str(child)):
            continue
        try:
            for nested in child.iterdir():
                if nested.is_dir() and os.path.ismount(str(nested)):
                    mounts.append(str(nested))
        except (PermissionError, OSError):
            continue

    return mounts


def list_removable_drives() -> list[DriveOption]:
    system_name = platform.system().lower()
    options: list[DriveOption] = []

    if system_name == "windows":
        drive_type_removable = 2
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{letter}:\\"
            if not os.path.exists(drive):
                continue
            try:
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
            except (AttributeError, OSError, ValueError):
                logger.debug("Failed to inspect drive type for %s", drive, exc_info=True)
                continue
            if drive_type == drive_type_removable:
                options.append(DriveOption(label=f"{drive} (Removable)", path=drive))
        return options

    search_roots = ["/Volumes"] if system_name == "darwin" else ["/media", "/run/media", "/mnt"]

    seen: set[str] = set()
    for root in search_roots:
        for mount in _mounted_directories(root):
            if mount in seen:
                continue
            seen.add(mount)
            mount_path = Path(mount)
            options.append(DriveOption(label=f"{mount_path.name} ({mount})", path=mount))

    return sorted(options, key=lambda item: item.label.lower())


def filesystem_type(path: str) -> str:
    resolved = str(Path(path).expanduser().resolve())
    system_name = platform.system().lower()

    if system_name == "windows":
        try:
            drive_root = str(Path(resolved).anchor) or resolved
            volume_name = ctypes.create_unicode_buffer(261)
            fs_name = ctypes.create_unicode_buffer(261)
            serial_number = ctypes.c_uint()
            max_component = ctypes.c_uint()
            file_system_flags = ctypes.c_uint()

            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                drive_root,
                volume_name,
                len(volume_name),
                ctypes.byref(serial_number),
                ctypes.byref(max_component),
                ctypes.byref(file_system_flags),
                fs_name,
                len(fs_name),
            )
            if ok:
                name = fs_name.value.strip()
                return name if name else "Unknown"
        except (AttributeError, OSError, ValueError):
            logger.debug("Failed to read Windows filesystem type for %s", resolved, exc_info=True)
            return "Unknown"

    if system_name == "darwin":
        try:
            result = subprocess.run(
                ["diskutil", "info", "-plist", resolved],
                capture_output=True,
                check=False,
                **_subprocess_no_window_kwargs(),
            )
            if result.returncode == 0 and result.stdout:
                data = plistlib.loads(result.stdout)
                fs_name = (
                    data.get("FilesystemName")
                    or data.get("FilesystemType")
                    or data.get("FileSystemPersonality")
                )
                if fs_name:
                    return str(fs_name)
        except (OSError, ValueError, subprocess.SubprocessError):
            logger.debug("Failed to read macOS filesystem type for %s", resolved, exc_info=True)
            return "Unknown"

    try:
        result = subprocess.run(
            ["df", "-T", resolved],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            **_subprocess_no_window_kwargs(),
        )
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 2:
                    return parts[1]
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.debug("df -T failed for %s", resolved, exc_info=True)

    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", resolved],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            **_subprocess_no_window_kwargs(),
        )
        if result.returncode == 0:
            fs_name = result.stdout.strip()
            if fs_name:
                return fs_name
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.debug("stat filesystem fallback failed for %s", resolved, exc_info=True)

    return "Unknown"


def collect_target_info(path: str) -> list[tuple[str, str]]:
    resolved = str(Path(path).expanduser().resolve())
    stats = shutil.disk_usage(resolved)
    file_count, dir_count = top_level_counts(resolved)
    fs_type = filesystem_type(resolved)

    readable = "Yes" if os.access(resolved, os.R_OK) else "No"
    writable = "Yes" if os.access(resolved, os.W_OK) else "No"
    executable = "Yes" if os.access(resolved, os.X_OK) else "No"
    try:
        modified = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(resolved))
        )
    except Exception:
        # Some filesystems or root paths (especially removable drives on Windows)
        # may not provide a valid modification time. Fall back to Unknown.
        modified = "Unknown"
    used_percent = (stats.used / stats.total * 100) if stats.total else 0

    target_type = "Removable Drive" if os.path.ismount(resolved) else "Folder"

    return [
        ("Name", Path(resolved).name or resolved),
        ("Path", resolved),
        ("Type", target_type),
        ("Mount Point", "Yes" if os.path.ismount(resolved) else "No"),
        ("Filesystem", fs_type),
        ("Permissions", f"Read: {readable} | Write: {writable} | Execute: {executable}"),
        ("Total Space", format_bytes(stats.total)),
        ("Used Space", f"{format_bytes(stats.used)} ({used_percent:.2f}%)"),
        ("Free Space", format_bytes(stats.free)),
        ("Top-Level Folders", dir_count),
        ("Top-Level Files", file_count),
        ("Last Modified", modified),
    ]


def attempt_unmount_mountpoint(mountpoint: str) -> tuple[bool, str]:
    """Try to unmount/eject the given mountpoint.

    Returns (True, "") on success, or (False, "error message") on failure.
    This is best-effort and attempts platform-appropriate commands.
    """
    system_name = platform.system().lower()
    try:
        if system_name == "darwin":
            # Use diskutil for macOS
            for cmd in (["diskutil", "unmount", mountpoint], ["diskutil", "unmountDisk", mountpoint]):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", **_subprocess_no_window_kwargs())
                except Exception as exc:
                    return False, f"Failed to run diskutil: {exc}"
                if result.returncode == 0:
                    return True, ""
                # continue to next command
            return False, (result.stderr or result.stdout or "diskutil failed")

        if system_name == "linux":
            # Try findmnt to discover block device, prefer udisksctl if available
            device = None
            try:
                findmnt = shutil.which("findmnt")
                if findmnt:
                    res = subprocess.run([findmnt, "-n", "-o", "SOURCE", "--target", mountpoint], capture_output=True, text=True, errors="replace", **_subprocess_no_window_kwargs())
                    if res.returncode == 0 and res.stdout:
                        device = res.stdout.strip()
            except Exception:
                device = None

            # Prefer udisksctl if present and we discovered a device
            if device and shutil.which("udisksctl"):
                try:
                    res = subprocess.run(["udisksctl", "unmount", "-b", device], capture_output=True, text=True, errors="replace", **_subprocess_no_window_kwargs())
                    if res.returncode == 0:
                        return True, ""
                    # fallthrough to try umount
                except Exception as exc:
                    return False, f"udisksctl failed: {exc}"

            # Fallback to system umount
            try:
                res = subprocess.run(["umount", mountpoint], capture_output=True, text=True, errors="replace", **_subprocess_no_window_kwargs())
                if res.returncode == 0:
                    return True, ""
                return False, (res.stderr or res.stdout or "umount returned non-zero")
            except Exception as exc:
                return False, f"umount failed: {exc}"

        if system_name == "windows":
            # Attempt Shell.Application Eject via PowerShell
            try:
                # Ensure path ends with backslash for COM ParseName on drive letters
                p = Path(mountpoint)
                drive = p.anchor or str(mountpoint)
                # Normalize to e.g. 'E:\'
                drive_norm = str(Path(drive))
                ps_cmd = [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(New-Object -ComObject Shell.Application).Namespace(17).ParseName('{drive_norm}').InvokeVerb('Eject')",
                ]
                res = subprocess.run(ps_cmd, capture_output=True, text=True, errors="replace", shell=False, **_subprocess_no_window_kwargs())
                if res.returncode == 0:
                    return True, ""
                # If COM eject failed, try WMI Dismount (may require privileges)
                drive_letter = (drive_norm[0] if len(drive_norm) >= 1 else "").upper()
                if drive_letter and drive_letter.isalpha():
                    ps_cmd2 = [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "$v=Get-WmiObject -Class Win32_Volume -Filter \"DriveLetter='" + drive_letter + ":'\";"
                            " if($v) {$v.Dismount($false,$false); exit 0} else { exit 2 }"
                        ),
                    ]
                    res2 = subprocess.run(ps_cmd2, capture_output=True, text=True, errors="replace", shell=False, **_subprocess_no_window_kwargs())
                    if res2.returncode == 0:
                        return True, ""
                    return False, (res2.stderr or res2.stdout or "PowerShell unmount failed")
                return False, (res.stderr or res.stdout or "PowerShell eject failed")
            except Exception as exc:
                return False, f"Windows unmount attempt failed: {exc}"

        return False, f"Unmount not implemented for platform: {system_name}"
    except Exception as exc:
        return False, str(exc)
