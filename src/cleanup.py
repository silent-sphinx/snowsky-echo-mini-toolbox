import os
import sys
from pathlib import Path

from .constants import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS
from .curses_ui import _curses_confirm, _curses_message, _curses_yes_no_list
from .ui import _print_progress, _print_screen_header


def _remove_irrelevant_files(target_dir, collect=False, config=None):
    use_curses = sys.stdin.isatty() and not collect and config is None
    if not use_curses and not collect:
        _print_screen_header("Remove irrelevant files")

    def _ask_yes_no(prompt):
        return input(f"{prompt} (y/N): ").strip().lower() == "y"

    files_to_remove = []
    hidden_files = []
    image_files = []
    other_files = []

    total_files = sum(1 for path in target_dir.rglob("*") if path.is_file())
    scanned = 0
    for path in target_dir.rglob("*"):
        if not path.is_file():
            continue
        scanned += 1
        if not collect:
            _print_progress("Scanning", scanned, total_files)

        if path.name.startswith("."):
            hidden_files.append(path)
            continue

        ext = path.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            image_files.append(path)
        elif ext not in AUDIO_EXTENSIONS:
            other_files.append(path)

    empty_dirs = []
    for dirpath, dirnames, filenames in os.walk(
        target_dir, topdown=False, followlinks=False
    ):
        dirpath = Path(dirpath)
        if dirpath == target_dir:
            continue
        if dirnames or filenames:
            continue
        empty_dirs.append(dirpath)

    explanation_lines = [
        "Choose which categories to remove from the target drive.",
        "Use Up/Down to move, Space to toggle, Enter to confirm, q to cancel.",
    ]

    questions = [
        f"Remove hidden files? ({len(hidden_files)} found)",
        f"Remove image files? ({len(image_files)} found)",
        f"Remove other non-audio files? ({len(other_files)} found)",
        f"Remove empty folders after cleanup? ({len(empty_dirs)} found)",
    ]

    if config is not None:
        remove_hidden = bool(config.get("remove_hidden", False))
        remove_images = bool(config.get("remove_images", False))
        remove_other = bool(config.get("remove_other", False))
        remove_empty_dirs = bool(config.get("remove_empty_dirs", False))
    elif use_curses:
        selections = _curses_yes_no_list(
            explanation_lines,
            questions,
            "Remove irrelevant files",
        )
        if selections is None:
            return {
                "title": "Remove irrelevant files",
                "status": "canceled",
                "metrics": [],
                "details": [],
                "lists": {},
            }
        (
            remove_hidden,
            remove_images,
            remove_other,
            remove_empty_dirs,
        ) = selections
    else:
        print("\nCleanup options")
        for line in explanation_lines:
            print(line)
        remove_hidden = _ask_yes_no(questions[0])
        remove_images = _ask_yes_no(questions[1])
        remove_other = _ask_yes_no(questions[2])
        remove_empty_dirs = _ask_yes_no(questions[3])
    if remove_hidden:
        files_to_remove.extend(hidden_files)
    if remove_images:
        files_to_remove.extend(image_files)
    if remove_other:
        files_to_remove.extend(other_files)

    if not files_to_remove and not remove_empty_dirs:
        result = {
            "title": "Remove irrelevant files",
            "status": "success",
            "metrics": [("Removed files", "0"), ("Removed folders", "0")],
            "details": [("Info", "No cleanup actions selected")],
            "lists": {},
        }
        if collect:
            return result
        if use_curses:
            _curses_message(["No cleanup actions selected."], "Remove irrelevant files")
        else:
            print("No cleanup actions selected.")
        return result

    preview_lines = [f"Total files to remove: {len(files_to_remove)}"]
    if remove_hidden:
        preview_lines.append(f"Hidden files: {len(hidden_files)}")
    if remove_images:
        preview_lines.append(f"Image files: {len(image_files)}")
    if remove_other:
        preview_lines.append(f"Other non-audio files: {len(other_files)}")
    if remove_empty_dirs:
        preview_lines.append(f"Empty folders (currently): {len(empty_dirs)}")

    if config is not None:
        confirmed = bool(config.get("confirm", True))
        if not confirmed:
            return {
                "title": "Remove irrelevant files",
                "status": "canceled",
                "metrics": [],
                "details": [],
                "lists": {},
            }
    elif use_curses:
        confirmed = _curses_confirm(preview_lines, "Cleanup preview", "Remove files")
        if not confirmed:
            return {
                "title": "Remove irrelevant files",
                "status": "canceled",
                "metrics": [],
                "details": [],
                "lists": {},
            }
    else:
        print("\nCleanup preview")
        for line in preview_lines:
            print(line)
        confirm = input("Remove these files? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cleanup canceled.")
            return {
                "title": "Remove irrelevant files",
                "status": "canceled",
                "metrics": [],
                "details": [],
                "lists": {},
            }

    removed = 0
    failed = []
    total = len(files_to_remove)
    for index, path in enumerate(files_to_remove, start=1):
        try:
            path.unlink()
            removed += 1
        except Exception as exc:
            failed.append((path, str(exc)))
        if not collect:
            _print_progress("Removing", index, total)

    removed_dirs = 0
    failed_dirs = []
    if remove_empty_dirs:
        for dirpath, dirnames, filenames in os.walk(
            target_dir, topdown=False, followlinks=False
        ):
            dirpath = Path(dirpath)
            if dirpath == target_dir:
                continue
            if dirnames or filenames:
                continue
            try:
                dirpath.rmdir()
                removed_dirs += 1
            except Exception as exc:
                failed_dirs.append((dirpath, str(exc)))

    result_lines = [f"Removed {removed} file(s)."]
    if remove_empty_dirs:
        result_lines.append(f"Removed {removed_dirs} empty folder(s).")
    if failed:
        result_lines.append(f"Failed to remove {len(failed)} file(s):")
        for path, reason in failed[:10]:
            result_lines.append(f"  {path} - {reason}")
        if len(failed) > 10:
            result_lines.append("  ...")
    if failed_dirs:
        result_lines.append(f"Failed to remove {len(failed_dirs)} empty folder(s):")
        for path, reason in failed_dirs[:10]:
            result_lines.append(f"  {path} - {reason}")
        if len(failed_dirs) > 10:
            result_lines.append("  ...")

    result = {
        "title": "Remove irrelevant files",
        "status": "success",
        "metrics": [
            ("Removed files", str(removed)),
            ("Removed folders", str(removed_dirs)),
            ("Failed files", str(len(failed))),
            ("Failed folders", str(len(failed_dirs))),
        ],
        "details": [
            ("Hidden candidates", str(len(hidden_files))),
            ("Image candidates", str(len(image_files))),
            ("Other candidates", str(len(other_files))),
        ],
        "lists": {
            "Failed files": [f"{path} - {reason}" for path, reason in failed[:100]],
            "Failed folders": [f"{path} - {reason}" for path, reason in failed_dirs[:100]],
        },
    }

    if collect:
        return result

    if use_curses:
        _curses_message(result_lines, "Cleanup results")
    else:
        for line in result_lines:
            print(line)
    return result
