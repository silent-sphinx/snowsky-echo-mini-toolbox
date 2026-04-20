import sys

from .constants import (
    DSD_FORMATS,
    EXPLICIT_UNSUPPORTED,
    KNOWN_AUDIO_FORMATS,
    LOSSY_FORMATS,
    PCM_FORMATS,
    FLAC_BLOCK_MAX_LIMIT,
)
from .curses_ui import _curses_draw_header, _curses_message, _curses_setup
from .media import _map_dsd_multiple, _read_audio_metadata
from .ui import _print_progress, _print_screen_header, _relative_path, _usage_bar


def _evaluate_audio_file(path, target_dir, include_details=False):
    if path.name.startswith("."):
        return "skipped", None, None

    rel = _relative_path(path, target_dir)
    ext = path.suffix.lower()
    if not ext:
        detail = f"UNSUPPORTED: {rel} - no extension"
        return (
            "unsupported",
            f"UNSUPPORTED: {rel} - No extension",
            detail if include_details else None,
        )
    if ext not in KNOWN_AUDIO_FORMATS:
        return "skipped", None, None
    if ext in EXPLICIT_UNSUPPORTED:
        detail = f"UNSUPPORTED: {rel} - explicitly unsupported"
        return (
            "unsupported",
            f"UNSUPPORTED: {rel} - Explicitly unsupported",
            detail if include_details else None,
        )
    if ext in LOSSY_FORMATS:
        detail = f"SUPPORTED: {rel} - lossy format ({ext})"
        return "supported", None, detail if include_details else None
    if ext in PCM_FORMATS:
        meta = _read_audio_metadata(path)
        sample_rate = meta.get("sample_rate")
        bit_depth = meta.get("bit_depth")
        flac_block_max = meta.get("flac_block_max")
        if sample_rate is None or bit_depth is None:
            detail = f"UNKNOWN: {rel} - PCM metadata missing"
            return (
                "unknown",
                f"UNKNOWN: {rel} - Missing PCM metadata",
                detail if include_details else None,
            )
        if ext == ".flac" and flac_block_max is None:
            detail = f"UNKNOWN: {rel} - FLAC block size missing"
            return (
                "unknown",
                f"UNKNOWN: {rel} - Missing FLAC block size",
                detail if include_details else None,
            )
        if ext == ".flac" and flac_block_max > FLAC_BLOCK_MAX_LIMIT:
            detail = (
                f"UNSUPPORTED: {rel} - FLAC block size {flac_block_max} > {FLAC_BLOCK_MAX_LIMIT}"
            )
            return (
                "unsupported",
                f"UNSUPPORTED: {rel} - FLAC block size exceeds {FLAC_BLOCK_MAX_LIMIT}",
                detail if include_details else None,
            )
        if sample_rate > 192000 or bit_depth > 24:
            detail = (
                f"UNSUPPORTED: {rel} - PCM sr={sample_rate} Hz, bd={bit_depth} bit"
            )
            return (
                "unsupported",
                "UNSUPPORTED: "
                f"{rel} - Exceeds PCM limits (sr {sample_rate} Hz, bd {bit_depth} bit)",
                detail if include_details else None,
            )
        detail = f"SUPPORTED: {rel} - PCM sr={sample_rate} Hz, bd={bit_depth} bit"
        return "supported", None, detail if include_details else None
    if ext in DSD_FORMATS:
        meta = _read_audio_metadata(path)
        sample_rate = meta.get("sample_rate")
        if sample_rate is None:
            detail = f"UNKNOWN: {rel} - DSD sample rate missing"
            return (
                "unknown",
                f"UNKNOWN: {rel} - Missing DSD sample rate",
                detail if include_details else None,
            )
        mapped = _map_dsd_multiple(sample_rate)
        if mapped is None:
            detail = f"UNSUPPORTED: {rel} - DSD sr={sample_rate} Hz unrecognized"
            return (
                "unsupported",
                f"UNSUPPORTED: {rel} - Unrecognized DSD rate ({sample_rate} Hz)",
                detail if include_details else None,
            )
        if mapped > 256:
            detail = f"UNSUPPORTED: {rel} - DSD{mapped} exceeds DSD256"
            return (
                "unsupported",
                f"UNSUPPORTED: {rel} - DSD{mapped} exceeds DSD256",
                detail if include_details else None,
            )
        detail = f"SUPPORTED: {rel} - DSD{mapped}"
        return "supported", None, detail if include_details else None

    detail = f"UNSUPPORTED: {rel} - format not listed"
    return (
        "unsupported",
        f"UNSUPPORTED: {rel} - Format not listed",
        detail if include_details else None,
    )


def _music_compatibility_check(target_dir, verbose=False, collect=False):
    use_curses = sys.stdin.isatty() and not collect
    if not use_curses and not collect:
        _print_screen_header("Music compatibility checker")

    counts = {
        "supported": 0,
        "unsupported": 0,
        "unknown": 0,
        "skipped": 0,
        "evaluated": 0,
    }
    issues = []
    details = []

    files = [
        path
        for path in target_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    ]
    total_files = len(files)

    if use_curses:
        import curses

        def _run(stdscr):
            _curses_setup(stdscr)
            for index, path in enumerate(files, start=1):
                stdscr.erase()
                height, width, header_lines = _curses_draw_header(
                    stdscr, "Music compatibility checker"
                )
                bar = _usage_bar(index, total_files, width=20)
                progress_line = f"Scanning {index}/{total_files} {bar}"
                stdscr.addnstr(header_lines, 0, progress_line.ljust(width), width)
                file_line = f"File: {_relative_path(path, target_dir)}"
                if header_lines + 1 < height - 1:
                    stdscr.addnstr(header_lines + 1, 0, file_line, width)
                stdscr.refresh()

                status, issue, detail = _evaluate_audio_file(
                    path, target_dir, include_details=verbose
                )
                if status == "skipped":
                    counts["skipped"] += 1
                else:
                    counts["evaluated"] += 1
                    counts[status] += 1
                    if issue:
                        issues.append(issue)
                    if detail:
                        details.append(detail)

        curses.wrapper(_run)
    else:
        for index, path in enumerate(files, start=1):
            if not collect:
                _print_progress("Scanning", index, total_files)
            status, issue, detail = _evaluate_audio_file(
                path, target_dir, include_details=verbose
            )
            if status == "skipped":
                counts["skipped"] += 1
            else:
                counts["evaluated"] += 1
                counts[status] += 1
                if issue:
                    issues.append(issue)
                if detail:
                    details.append(detail)

    lines = [
        "Compatibility results:",
        f"Audio files scanned: {counts['evaluated']}",
        f"Supported: {counts['supported']}",
        f"Unsupported: {counts['unsupported']}",
        f"Unknown: {counts['unknown']}",
        f"Skipped (non-audio): {counts['skipped']}",
    ]

    if issues:
        lines.append("")
        lines.append("Issues:")
        lines.extend(issues)
    else:
        lines.append("")
        lines.append("All scanned audio files are supported.")

    if verbose:
        lines.append("")
        lines.append("Details:")
        lines.extend(details)

    result = {
        "title": "Music compatibility checker",
        "status": "success",
        "metrics": [
            ("Audio files scanned", str(counts["evaluated"])),
            ("Supported", str(counts["supported"])),
            ("Unsupported", str(counts["unsupported"])),
            ("Unknown", str(counts["unknown"])),
            ("Skipped", str(counts["skipped"])),
        ],
        "details": [],
        "lists": {
            "Issues": issues[:200],
            "Details": details[:500] if verbose else [],
        },
    }

    if collect:
        return result

    if use_curses:
        _curses_message(lines, "Music compatibility checker")
    else:
        for line in lines:
            print(line)
    return result
