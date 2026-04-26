# Snowsky Echo Mini Toolbox

Desktop toolbox for checking and maintaining music folders and removable drives used with the Snowsky Echo Mini.

![Overview](Overview.png)

## What The App Does

The current launcher opens a PySide6 desktop app with these tabs:

- About Folder/Drive: shows target path details (filesystem, disk usage, permissions, modified time, top-level file/folder counts)
- Album Art: scans embedded artwork, marks files as Compatible/Incompatible/Missing, and can rewrite incompatible embedded art as JPEG non-progressive
- Music Compatibility: scans files and classifies them as SUPPORTED, UNSUPPORTED, UNKNOWN, or SKIPPED, with reason and metadata columns
- File Cleanup: scans file-type breakdown (Audio, Image, Video, Document, Archive, Playlist, Subtitle, Executable, Hidden, Other) and removes selected categories
- Directory Browser: enabled when a removable drive is selected, shows folder sizes, presents rich metadata, and includes right-click Fix Album Art

## Installation

### Requirements

- Python 3.10+
- macOS, Linux, or Windows

### Setup

1. Clone or download this repository.
1. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

1. Install dependencies:

```bash
pip install -r requirements.txt
```

Dependencies listed in requirements.txt:

- mutagen
- pydub
- PySide6

## Run

Start the GUI:

```bash
python3 main.py
```

Optional: start with an initial target path pre-filled:

```bash
python3 main.py --path /path/to/folder-or-drive
```

Short flag:

```bash
python3 main.py -p /path/to/folder-or-drive
```

## Audio Compatibility Rules

The scanner validates formats using the rules in [COMPATIBILITY.md](COMPATIBILITY.md).

Quick summary:

- Always supported lossy: MP3, OGG, M4A, WMA
- PCM formats (WAV, FLAC, APE): must be <= 192000 Hz and <= 24-bit
- FLAC also checks max block size
- DSD formats (DSF, DFF): supports DSD64, DSD128, DSD256
- Known unsupported audio formats are reported as UNSUPPORTED
- Non-audio files are SKIPPED

