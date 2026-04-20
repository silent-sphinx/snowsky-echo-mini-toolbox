import os
import logging
import shutil
import sys
from pathlib import Path

from .curses_ui import _curses_message
from .ui import _format_bytes, _print_screen_header, _usage_bar


logger = logging.getLogger(__name__)


def _folder_sizes(root_dir):
    root_dir = Path(root_dir)
    sizes = {}
    root_files_size = 0

    try:
        for entry in root_dir.iterdir():
            if entry.is_file() and not entry.is_symlink():
                try:
                    root_files_size += entry.stat().st_size
                except (OSError, PermissionError):
                    logger.debug("Unable to read file size for %s", entry, exc_info=True)
    except (OSError, PermissionError):
        logger.debug("Unable to iterate root directory %s", root_dir, exc_info=True)

    for dirpath, dirnames, filenames in os.walk(
        root_dir, topdown=False, followlinks=False
    ):
        dirpath = Path(dirpath)
        total = 0

        for filename in filenames:
            path = dirpath / filename
            if path.is_symlink():
                continue
            try:
                total += path.stat().st_size
            except (OSError, PermissionError):
                logger.debug("Unable to stat file %s", path, exc_info=True)
                continue

        for dirname in dirnames:
            child = dirpath / dirname
            if child.is_symlink():
                continue
            total += sizes.get(child, 0)

        sizes[dirpath] = total

    return sizes, root_files_size


def _show_disk_usage(target_dir, collect=False):
    use_curses = sys.stdin.isatty() and not collect
    if not use_curses and not collect:
        _print_screen_header("Disk space usage")

    usage = shutil.disk_usage(target_dir)
    overview = [
        "Drive overview:",
        f"Total: {_format_bytes(usage.total)}",
        f"Used: {_format_bytes(usage.used)}",
        f"Free: {_format_bytes(usage.free)}",
        f"Usage: {_usage_bar(usage.used, usage.total)}",
        "",
        "Folders by size (largest first):",
    ]

    sizes, root_files_size = _folder_sizes(target_dir)
    root_path = Path(target_dir)
    children = {}
    for dirpath in sizes:
        if dirpath == root_path:
            continue
        parent = dirpath.parent
        if parent in sizes:
            children.setdefault(parent, []).append(dirpath)

    def _walk_tree(node, depth):
        entries = []
        for child in sorted(children.get(node, []), key=lambda p: sizes.get(p, 0), reverse=True):
            entries.append((child, sizes.get(child, 0), depth))
            entries.extend(_walk_tree(child, depth + 1))
        return entries

    ordered_entries = _walk_tree(root_path, 0)
    if root_files_size:
        overview.append(f"(root files): {_format_bytes(root_files_size)}")
    for dirpath, size, depth in ordered_entries:
        indent = "  " * depth
        name_only = dirpath.name or str(dirpath)
        overview.append(f"{indent}{name_only}: {_format_bytes(size)}")

    result = {
        "title": "Disk space usage",
        "status": "success",
        "metrics": [
            ("Total", _format_bytes(usage.total)),
            ("Used", _format_bytes(usage.used)),
            ("Free", _format_bytes(usage.free)),
        ],
        "details": [("Usage", _usage_bar(usage.used, usage.total))],
        "lists": {
            "Folders by size": [
                f"{('  ' * depth)}{(dirpath.name or str(dirpath))}: {_format_bytes(size)}"
                for dirpath, size, depth in ordered_entries
            ]
        },
    }
    if root_files_size:
        result["details"].append(("Root files", _format_bytes(root_files_size)))

    if collect:
        return result

    if use_curses:
        _curses_message(overview, "Disk space usage")
    else:
        for line in overview:
            print(line)
    return result
