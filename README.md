# Snowsky Echo Mini Toolbox

[![Python 3.12](https://img.shields.io/badge/python-3.12%2B-3776AB)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-0A7E07)](#requirements)
[![UI](https://img.shields.io/badge/UI-PySide6-41CD52)](https://doc.qt.io/qtforpython-6/)
![Status](https://img.shields.io/badge/status-active%20development-2ea44f)

An easy (unofficial) desktop app to prepare your music folders and USB drives for the Snowsky Echo Mini.

![Overview](Overview.png)

## Has the tool failed to fix your media?

Has your music file failed to be located by Snowsky Echo Mini Toolbox? Does it say it’s compatible but still not work, or has the file conversion failed?
Please help the community by submitting a bug report—your feedback helps improve the tool!

**GitHub:** *Issues > New Issue > Incompatible Music Format*

## Table of Contents

- [Snowsky Echo Mini Toolbox](#snowsky-echo-mini-toolbox)
  - [Has the tool failed to fix your media?](#has-the-tool-failed-to-fix-your-media)
  - [Table of Contents](#table-of-contents)
  - [Main Features](#main-features)
  - [Important Notes](#important-notes)
  - [Installation](#installation)
    - [Required Dependencies (Linux Only)](#required-dependencies-linux-only)
    - [Supported Operating Systems](#supported-operating-systems)
      - [Notice for Windows Users](#notice-for-windows-users)
        - [Setup Instructions](#setup-instructions)
  - [Build from source](#build-from-source)
  - [Compatibility Rules](#compatibility-rules)
  - [Contributing](#contributing)

## Main Features

- Main Menu:
  - A welcome dashboard with quick access tiles to all available tools.
- Music Compatibility:
  - Checks if your media is compatible with the Snowsky Echo Mini.
  - Automatically convert your incompatible media to a compatible audio format.
- About Folder/Drive:
  - Identify drive incompatibility issues
- Album Art:
  - Finds files with missing or incompatible album art.
  - Convert incompatible media automatically
- Lyrics Manager:
  - Scans embedded lyrics.
  - Can create .lrc lyric files and do LRCLIB bulk lookup.
- Metadata Manager:
  - Edit audio tags and properties in bulk.
- File Rename:
  - Suggests cleaner file names from metadata before you apply changes.
- File Cleanup:
  - Groups files by type so you can remove unwanted categories safely.
- Backup/Restore:
  - Creates ZIP backups, or copy/move your library to another location.

## Important Notes

- This program has the ability to modify your Snowsky Echo Mini Library, ALWAYS check to see if the program is making the intended changes on your data, before applying.
- Make use of the backup tools to protect your library from inadvertant changes.

## Installation

Locate the Releases page for this project and select the correct installation file for your system architecture.

### Required Dependencies (Linux Only)

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1
```

### Supported Operating Systems

The following operating systems & architectures are offically supported.

| Operating System | Architecture | Format |
| ---- | ---- | ---- |
| Windows | 64-bit | Installation (.exe) |
| Linux   | AMD64 (64-bit) | .tar.gz & .deb |
| MacOS | ARM64 (Apple Silicon) | .dmg |

#### Notice for Windows Users

Unfortunately, Windows Smart App Control blocks executables from small developers with no user override. Disabling SAC is not recommended, so whilst the `.exe` remains available, it is strongly recommended to clone the repository and run the program via Python directly.

##### Setup Instructions

1. Install ffmpeg via winget: `winget install -e --id Gyan.FFmpeg`
   — or download it manually from https://github.com/BtbN/FFmpeg-Builds/releases
2. Ensure Python 3.12 is installed
3. Clone the repository: `git clone https://github.com/silent-sphinx/snowsky-echo-mini-toolbox.git`
   — or download the `.zip` from the GitHub page
4. Create a virtual environment and run the program:

```bash
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\venv\Scripts\python main.py
```

We apologise for the inconvenience. Microsoft's increasing restrictions on unsigned executables make it genuinely difficult to distribute small community tools easily, and we're actively looking into longer-term solutions.

## Build from source

This project is developed in Python 3.12+, and using Python 3.12 is recommended.

Note: If `pip install -r requirements.txt` fails, ensure your active Python interpreter is
Python 3.12. On Windows, the Python launcher can help (e.g. `py -3.12 -m venv venv`).

1. Ensure that ffmpeg is installed (for ffmpeg/ffprobe)
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
py -3.12 -m venv venv
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
