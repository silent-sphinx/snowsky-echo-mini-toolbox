import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .constants import AUDIO_EXTENSIONS
from .curses_ui import _curses_message, _curses_select
from .ui import _format_bytes, _print_screen_header, _report_error, _usage_bar


logger = logging.getLogger(__name__)


def _drive_label(drive):
    if sys.platform.startswith("win"):
        try:
            import ctypes

            volume_name = ctypes.create_unicode_buffer(261)
            fs_name = ctypes.create_unicode_buffer(261)
            serial_number = ctypes.c_uint()
            max_component = ctypes.c_uint()
            file_system_flags = ctypes.c_uint()

            ctypes.windll.kernel32.GetVolumeInformationW(
                str(drive),
                volume_name,
                len(volume_name),
                ctypes.byref(serial_number),
                ctypes.byref(max_component),
                ctypes.byref(file_system_flags),
                fs_name,
                len(fs_name),
            )
            label = volume_name.value.strip()
            return label or str(drive)
        except (AttributeError, OSError, ValueError):
            logger.debug("Failed to read drive label for %s", drive, exc_info=True)
            return str(drive)

    return drive.name or str(drive)


def _drive_type_label(drive):
    if sys.platform.startswith("win"):
        try:
            import ctypes

            get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
            dtype = get_drive_type(str(drive))
            types = {
                0: "Unknown",
                1: "No root",
                2: "Removable",
                3: "Fixed",
                4: "Remote",
                5: "CD-ROM",
                6: "RAM disk",
            }
            return types.get(dtype, "Unknown")
        except (AttributeError, OSError, ValueError):
            logger.debug("Failed to read drive type for %s", drive, exc_info=True)
            return "Unknown"

    return "Removable"


def _mac_volume_info(target_dir):
    try:
        import plistlib

        result = subprocess.run(
            ["diskutil", "info", "-plist", str(target_dir)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            return {}
        data = plistlib.loads(result.stdout)
        return {
            "device": data.get("DeviceNode"),
            "volume_name": data.get("VolumeName"),
            "filesystem": data.get("FilesystemName")
            or data.get("FilesystemType")
            or data.get("FileSystemPersonality"),
            "protocol": data.get("Protocol"),
            "uuid": data.get("VolumeUUID"),
            "removable": data.get("Removable"),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.debug("Failed to read macOS volume metadata for %s", target_dir, exc_info=True)
        return {}


def _linux_fs_type(target_dir):
    try:
        result = subprocess.run(
            ["df", "--output=fstype", str(target_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return ""
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) >= 2:
            return lines[1]
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.debug("Failed to read Linux filesystem type for %s", target_dir, exc_info=True)
        return ""
    return ""


def _windows_volume_info(drive):
    try:
        import ctypes

        volume_name = ctypes.create_unicode_buffer(261)
        fs_name = ctypes.create_unicode_buffer(261)
        serial_number = ctypes.c_uint()
        max_component = ctypes.c_uint()
        file_system_flags = ctypes.c_uint()

        ctypes.windll.kernel32.GetVolumeInformationW(
            str(drive),
            volume_name,
            len(volume_name),
            ctypes.byref(serial_number),
            ctypes.byref(max_component),
            ctypes.byref(file_system_flags),
            fs_name,
            len(fs_name),
        )
        return {
            "volume_name": volume_name.value.strip() or None,
            "filesystem": fs_name.value.strip() or None,
            "serial": f"{serial_number.value:08X}",
        }
    except (AttributeError, OSError, ValueError):
        logger.debug("Failed to read Windows volume metadata for %s", drive, exc_info=True)
        return {}


def _count_audio_files(target_dir):
    counts = {}
    total = 0
    for path in Path(target_dir).rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in AUDIO_EXTENSIONS:
            total += 1
            counts[ext] = counts.get(ext, 0) + 1
    return total, counts


def _is_compatible_filesystem(fs_type):
    if not fs_type:
        return False
    normalized = fs_type.strip().lower().replace(" ", "")
    if "fat32" in normalized:
        return True
    return normalized in {"vfat", "exfat", "msdos"}


def _drive_display(drive):
    label = _drive_label(drive)
    size_text = ""
    try:
        usage = shutil.disk_usage(drive)
        used_text = _format_bytes(usage.used)
        total_text = _format_bytes(usage.total)
        ratio = usage.used / usage.total if usage.total else 0
        bar_width = 10
        filled = int(round(ratio * bar_width))
        bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
        percent = int(round(ratio * 100))
        size_text = f"{bar} {percent}% used {used_text} / {total_text}"
    except OSError:
        logger.debug("Failed to read disk usage for %s", drive, exc_info=True)
        size_text = ""

    if size_text:
        if label and label != str(drive):
            return f"{label} - {drive} ({size_text})"
        return f"{drive} ({size_text})"
    if label and label != str(drive):
        return f"{label} - {drive}"
    return str(drive)


def _list_removable_drives():
    system = sys.platform
    drives = []

    if system.startswith("darwin"):
        volumes = Path("/Volumes")
        if volumes.exists():
            for entry in sorted(volumes.iterdir()):
                if entry.is_dir() and not entry.is_symlink():
                    drives.append(entry)
    elif system.startswith("win"):
        try:
            import ctypes

            get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
            get_logical_drives = ctypes.windll.kernel32.GetLogicalDrives

            bitmask = get_logical_drives()
            for i in range(26):
                if bitmask & (1 << i):
                    letter = chr(ord("A") + i)
                    root = f"{letter}:\\"
                    dtype = get_drive_type(root)
                    # DRIVE_REMOVABLE = 2
                    if dtype == 2:
                        drives.append(Path(root))
        except (AttributeError, OSError, ValueError):
            logger.debug("Failed to enumerate Windows removable drives", exc_info=True)
    else:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        candidates = [
            Path("/media") / user,
            Path("/run/media") / user,
        ]
        for base in candidates:
            if base.exists():
                for entry in sorted(base.iterdir()):
                    if entry.is_dir() and not entry.is_symlink():
                        drives.append(entry)

    return drives


def _choose_drive(drives):
    if not drives:
        print("Error: No removable drives detected.")
        return None

    options = [_drive_display(drive) for drive in drives]
    try:
        index = _curses_select(
            options,
            "Select target removable drive",
            "Use arrow keys, Enter to select, q to cancel",
        )
        if index is None:
            return None
        return drives[index]
    except (KeyboardInterrupt, RuntimeError, ValueError):
        logger.debug("Falling back to text drive selection", exc_info=True)

    print("\nAvailable removable drives:")
    for idx, drive in enumerate(drives, start=1):
        print(f"  {idx}. {drive}")

    while True:
        choice = input("Select a target drive by number (or 'q' to quit): ").strip()
        if choice.lower() == "q":
            return None
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(drives):
                return drives[index - 1]
        print("Invalid selection. Try again.")


def _select_target_drive():
    drives = _list_removable_drives()
    if not drives:
        _report_error("Error: No removable drives detected.")
        return None

    selected = _choose_drive(drives)
    if selected is None:
        return None

    return selected


def _select_target_dir(input_path=None):
    if input_path:
        path = Path(input_path).expanduser()
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            logger.debug("Failed to resolve input path %s; using expanded path", input_path)
            path = Path(input_path).expanduser()

        if not path.exists():
            _report_error(f"Error: Input path does not exist: {path}")
            return None
        if not path.is_dir():
            _report_error(f"Error: Input path is not a directory: {path}")
            return None
        return path

    return _select_target_drive()


def _show_drive_info(target_dir, collect=False, include_audio=True):
    use_curses = sys.stdin.isatty() and not collect
    if not use_curses and not collect:
        _print_screen_header("About drive")

    drive = Path(target_dir)
    label = _drive_label(drive)
    drive_type = _drive_type_label(drive)
    mount_point = str(drive)

    fs_type = ""
    protocol = ""
    uuid = ""
    serial = ""
    removable = ""
    device = ""

    if sys.platform.startswith("win"):
        info = _windows_volume_info(drive)
        fs_type = info.get("filesystem") or ""
        serial = info.get("serial") or ""
    elif sys.platform.startswith("darwin"):
        info = _mac_volume_info(drive)
        fs_type = info.get("filesystem") or ""
        protocol = info.get("protocol") or ""
        uuid = info.get("uuid") or ""
        device = info.get("device") or ""
        removable = "Yes" if info.get("removable") else "No"
        label = info.get("volume_name") or label
    else:
        fs_type = _linux_fs_type(drive)

    usage = shutil.disk_usage(drive)

    items = []
    if label:
        items.append(("Label", label))
    items.append(("Path", mount_point))
    if device:
        items.append(("Device", device))
    if fs_type:
        if _is_compatible_filesystem(fs_type):
            items.append(("Filesystem", fs_type))
        else:
            items.append(
                (
                    "Filesystem",
                    f"{fs_type} (warning: may be incompatible, use FAT32)",
                )
            )
    if protocol:
        items.append(("Protocol", protocol))
    if uuid:
        items.append(("Volume UUID", uuid))
    if serial:
        items.append(("Volume serial", serial))
    if removable:
        items.append(("Removable", removable))
    if drive_type:
        items.append(("Drive type", drive_type))

    total_text = _format_bytes(usage.total)
    if usage.total > 256 * 1024**3:
        total_text = f"{total_text} (warning: exceeds 256 GB)"

    size_items = [
        ("Total", total_text),
        ("Used", _format_bytes(usage.used)),
        ("Free", _format_bytes(usage.free)),
        ("Usage", _usage_bar(usage.used, usage.total)),
    ]

    audio_items = []
    audio_text = "N/A"
    if include_audio:
        audio_total, audio_counts = _count_audio_files(drive)
        audio_limit = 8192
        audio_text = f"{audio_total}/{audio_limit}"
        if audio_total >= audio_limit:
            audio_text = f"{audio_text} (warning: limit reached)"
        audio_items.append(("Audio files", audio_text))
        if audio_total:
            formatted = ", ".join(
                f"{ext} ({count})"
                for ext, count in sorted(
                    audio_counts.items(), key=lambda item: (-item[1], item[0])
                )
            )
            if formatted:
                audio_items.append(("By format", formatted))

    label_width = 0
    for key, _value in items + size_items + audio_items:
        label_width = max(label_width, len(key))

    lines = ["Drive information:"]
    for key, value in items:
        lines.append(f"{key:<{label_width}} : {value}")
    lines.append("")
    for key, value in size_items:
        lines.append(f"{key:<{label_width}} : {value}")
    if audio_items:
        lines.append("")
        for key, value in audio_items:
            lines.append(f"{key:<{label_width}} : {value}")

    result = {
        "title": "About drive",
        "status": "success",
        "metrics": [
            ("Total", total_text),
            ("Used", _format_bytes(usage.used)),
            ("Free", _format_bytes(usage.free)),
            ("Audio files", audio_text),
        ],
        "details": items + size_items + audio_items,
        "lists": {},
    }

    if collect:
        return result

    if use_curses:
        _curses_message(lines, "About drive")
    else:
        for line in lines:
            print(line)
    return result
