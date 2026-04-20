# Snowsky Echo Mini Toolbox

[![Python 3.12](https://img.shields.io/badge/python-3.12%2B-3776AB)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-0A7E07)](#requirements)
[![UI](https://img.shields.io/badge/UI-PySide6-41CD52)](https://doc.qt.io/qtforpython-6/)
![Status](https://img.shields.io/badge/status-active%20development-2ea44f)

An easy desktop app to prepare your music folders and USB drives for the Snowsky Echo Mini.

![Overview](Overview.png)

## Table of Contents

- [Snowsky Echo Mini Toolbox](#snowsky-echo-mini-toolbox)
  - [Table of Contents](#table-of-contents)
  - [Main Features](#main-features)
  - [Requirements](#requirements)
  - [Important Notes](#important-notes)
  - [Installation](#installation)
  - [Build from source](#build-from-source)
  - [Compatibility Rules](#compatibility-rules)
  - [Contributing](#contributing)

## Main Features

- About Folder/Drive:
  - Shows drive or folder info like free space, file system, and permissions.
- Album Art:
  - Finds files with missing or incompatible album art.
  - Can fix incompatible album art in batch.
- Music Compatibility:
  - Checks your audio files and shows SUPPORTED, UNSUPPORTED, UNKNOWN, or SKIPPED.
- Lyrics Manager:
  - Scans embedded lyrics.
  - Can create .lrc lyric files and do LRCLIB bulk lookup.
- File Rename:
  - Suggests cleaner file names from metadata before you apply changes.
- File Cleanup:
  - Groups files by type so you can remove unwanted categories safely.
- Backup/Restore:
  - Creates ZIP backups, or copy/move your library to another location.
- Directory Browser:
  - Lets you inspect files and run right-click actions for single-file fixes.

## Requirements

- Python 3.12+
- macOS, Linux, or Windows

## Important Notes

- This program has the ability to modify your Snowsky Echo Mini Library, ALWAYS check to see if the program is making the intended changes on your data, before applying.
- Make use of the backup tools to protect your library from inadvertant changes.

## Installation

Locate the Releases page for this project and select the correct installation file for your system architecture.

## Build from source

This project is developed in Python, it's advised to use version 3.10.

1. Ensure that ffmpeg is installed (for ffprobe)
2. Clone the repository.
3. Create and activate a virtual environment.
4. Install dependencies.

macOS and Linux:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start the app:

```bash
python3 main.py
```

Start with a pre-selected target path:

```bash
python3 main.py --path /path/to/folder-or-drive
```

## Compatibility Rules

Full technical rules are in [COMPATIBILITY.md](COMPATIBILITY.md).

## Contributing

Contributions are welcome.

Basic flow:

1. Open an issue describing the change or bug.
1. Create a focused branch.
1. Keep pull requests small and include test or validation steps.
1. Update documentation when behavior changes.

If you add a new tab or workflow, include:

- What users will see and do
- How errors are handled
- Any dependency or platform notes
