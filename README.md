<div align="center">

# Snowsky Echo Mini Toolbox

**Get your music library playing properly on the Snowsky Echo Mini, without the guesswork.**

[![Latest release](https://img.shields.io/github/v/release/silent-sphinx/snowsky-echo-mini-toolbox?display_name=tag&label=latest%20release&color=2ea44f)](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/silent-sphinx/snowsky-echo-mini-toolbox/total?label=downloads&color=0A7E07)](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/releases)
[![Build](https://img.shields.io/github/actions/workflow/status/silent-sphinx/snowsky-echo-mini-toolbox/release.yml?label=build)](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/actions/workflows/release.yml)
[![Open issues](https://img.shields.io/github/issues/silent-sphinx/snowsky-echo-mini-toolbox?color=orange)](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/issues)

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-0A7E07)](#2-download-the-app)
[![Unofficial](https://img.shields.io/badge/status-unofficial%20community%20tool-8957e5)](#about-this-project)

[Get started](#get-started) · [Features](#features) · [Compatibility rules](COMPATIBILITY.md) · [Troubleshooting](#troubleshooting) · [Contributing](#contributing)

![Overview of the Snowsky Echo Mini Toolbox interface](Overview.png)

</div>

The Snowsky Echo Mini is a fantastic little player, but its affordable hardware brings real limits: only certain codecs play, album art has to be encoded a very specific way, and unusual tags can confuse the on-device browser. Tracks that work everywhere else can silently refuse to appear.

This toolbox points at a folder or USB drive, scans everything in it, and tells you exactly what the device will reject, then fixes it for you. Convert incompatible audio, repair or download album art, tidy metadata, and get lyrics in place, all without touching a command line.

> [!NOTE]
> This is the `v2` branch, a ground-up rewrite of the app. The version published on the [Releases page](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/releases/latest) is built from `main` and is what you should install today. See [Rewrite status](#rewrite-status) for what has landed in the rewrite so far.

## Get started

### 1. Back up your music first

> [!WARNING]
> This program edits music files on your device in place. Conversions, tag edits, and artwork fixes overwrite real data. Always keep a second copy of your library before running a fix. The built-in Backup/Restore tools can create one for you.

### 2. Download the app

Grab the file for your system from the [latest release](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/releases/latest). No Python, no ffmpeg install, nothing else to set up. Everything is bundled.

| Your system | Download | Install |
| --- | --- | --- |
| macOS (Apple Silicon) | `...macOS-arm64.dmg` | [Extra step required](#macos-first-launch) |
| macOS (Intel) | `...macOS-x64.dmg` | [Extra step required](#macos-first-launch) |
| Windows (64-bit) | `...Windows-Setup.exe` | [Please read first](#windows-smart-app-control) |
| Linux (amd64, Debian/Ubuntu) | `..._amd64.deb` | `sudo apt install ./<file>.deb` |
| Linux (amd64, portable) | `...linux-amd64.tar.gz` | [Extra libraries required](#linux-portable-tarball) |

### 3. Point it at your music

Launch the app and pick a target when prompted: either your Echo Mini's USB drive or any folder on your computer. The toolbox scans it once, then every tab works from that single scan.

From there, work through the tabs that flag problems. Each one shows you what it found before it changes anything.

## Features

- **Drive information**: capacity, file counts, and a breakdown of what is actually on the device.
- **Music compatibility**: deep `ffprobe` analysis of every track against the device's real firmware limits, with one-click conversion of anything unsupported.
- **File browser**: inspect any file's properties, tags, embedded artwork, and lyrics side by side.
- **Metadata browser**: edit and normalise audio tags in bulk, including the non-standard tags that confuse the device's parser.
- **Album art**: find artwork the device cannot display (wrong format, progressive JPEG, oversized), auto-fix it, or download replacements from MusicBrainz and the Cover Art Archive.
- **Lyrics manager**: convert embedded lyrics into device-readable `.lrc` files and fetch missing ones from LRCLIB.
- **File rename**: propose cleaner filenames from metadata, with a preview before anything is applied.
- **File cleanup**: group files by type so unwanted categories can be removed safely.
- **Backup & restore**: create ZIP backups, or copy and move your library elsewhere.

Artwork and lyrics lookups are the only features that use the network, and only when you start them.

### Rewrite status

The `v2` rewrite is being rebuilt tab by tab. Everything below already ships in the current release; this table tracks the rewrite itself.

| Area | `v2` status |
| --- | --- |
| Drive information | Implemented |
| Music compatibility & conversion | Implemented |
| File browser | Implemented |
| Metadata browser | Implemented |
| Album art (fix & download) | Implemented |
| Lyrics manager | Placeholder tab, not yet ported |
| File rename, cleanup, backup & restore | Not yet ported |

## Device compatibility

[COMPATIBILITY.md](COMPATIBILITY.md) documents every rule the compatibility checker applies: supported codecs, sample rates, bit depths, album art encoding, filename edge cases, and which tags the device actually reads.

The findings behind those rules, gathered by reverse-engineering the device firmware, are in [SNOWSKY-FIRMWARE.md](SNOWSKY-FIRMWARE.md).

## Troubleshooting

### macOS first launch

Release builds are unsigned, so Gatekeeper blocks them. The usual symptom is the icon bouncing in the Dock forever without a window appearing.

1. **Delete any old copy first.** If you are upgrading, drag the existing `Snowsky Echo Mini Toolbox.app` from `/Applications` to the Trash rather than overwriting it. Replacing in place causes path-level cache conflicts.
2. **Clear the quarantine flag** on the downloaded DMG before mounting it (adjust the filename to match your download):

   ```bash
   xattr -cr ~/Downloads/snowsky-echo-mini-toolbox-macOS-arm64.dmg
   ```

3. **Mount and install.** Double-click the cleared `.dmg` and drag the app into `/Applications`.
4. **Open it.** The app should now launch normally.

### Windows Smart App Control

Windows Smart App Control blocks executables from small developers, with no per-app override. Turning SAC off is not something we recommend, so while the `.exe` installer is still published, the more reliable route on an affected machine is [running from source](#running-from-source).

We know this is inconvenient. Microsoft's tightening restrictions on unsigned executables make small community tools genuinely hard to distribute, and we are looking into longer-term signing options.

### Linux portable tarball

The `.deb` package pulls in what it needs automatically. For the portable `.tar.gz`, install the Qt runtime libraries yourself:

```bash
sudo apt-get update && sudo apt-get install -y libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1
```

### Nothing here matches your problem

Open an [issue](https://github.com/silent-sphinx/snowsky-echo-mini-toolbox/issues) with your OS, the release you installed, and what you were doing when it went wrong.

## Running from source

Useful if you are developing, or if Smart App Control is blocking the Windows installer.

**Requirements**

- Python 3.12 or newer (3.12 recommended)
- `ffmpeg` and `ffprobe` available on your `PATH`. Release builds bundle these, source checkouts do not.

Install ffmpeg with `winget install -e --id Gyan.FFmpeg` on Windows, `brew install ffmpeg` on macOS, or `sudo apt-get install -y ffmpeg` on Debian/Ubuntu.

**Setup**

```bash
git clone https://github.com/silent-sphinx/snowsky-echo-mini-toolbox.git
cd snowsky-echo-mini-toolbox
```

macOS and Linux:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

Windows PowerShell:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\venv\Scripts\python main.py
```

If `pip install -r requirements.txt` fails, check that the active interpreter really is Python 3.12. On Windows, the `py -3.12` launcher is the easiest way to be sure.

## Contributing

Contributions are welcome.

1. Open an issue describing the change or bug.
2. Work on a focused branch.
3. Keep pull requests small, and include test or validation steps.
4. Update the documentation when behaviour changes.

If you are adding a new tab or workflow, describe what users will see and do, how errors are surfaced, and any dependency or platform notes.

Found a security issue? Please follow [SECURITY.md](SECURITY.md) and report it privately rather than opening a public issue.

## About this project

An unofficial, community-built tool. It is not affiliated with, endorsed by, or supported by Snowsky or its manufacturer.
