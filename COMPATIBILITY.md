# Snowsky Echo Mini Compatibility Requirements

This document describes exactly how the media compatibility checker evaluates audio files for the Snowsky Echo Mini.

## Audio Compatibility Table

| Audio Format | File Extensions | Supported? | Sample Rate | Bit Depth | Supported Codecs | Unsupported Codecs | Comments |
| ------------ | --------------- | ---------- | ----------- | --------- | ---------------- | ------------------ | -------- |
| Free Lossless Audio Codec | .flac | OFFICIALLY SUPPORTED | ≤ 192 kHz | ≤ 24 bits | | | |
| Waveform Audio File Format | .wav | OFFICIALLY SUPPORTED | ≤ 192 kHz | ≤ 24 bits | | | |
| APE (Monkey's Audio) | .ape | OFFICIALLY SUPPORTED | ≤ 192 kHz | ≤ 24 bits | | | |
| Direct Stream Digital | .dsf, .dff | OFFICIALLY SUPPORTED | DSD64, DSD128, or DSD256 | | | | |
| MP3 | .mp3 | OFFICIALLY SUPPORTED | | | | | |
| OGG | .ogg | OFFICIALLY SUPPORTED | | | | | |
| M4A | .m4a, .m4b, .m4p | OFFICIALLY SUPPORTED | | | AAC, AAC-LC, HE-AAC, ALAC | FLAC, DTS, AC-3/EC-3 | |
| MP3 | .wma | OFFICIALLY SUPPORTED | | | | | |
| Super Audio CD | .scad, .iso | OFFICIALLY UNSUPPORTED | | | | | |
| Digital Theater Systems | .dts, .dtshd | OFFICIALLY UNSUPPORTED | | | | | |
| OPUS | .opus | UNSUPPORTED | | | | | .opus files don't appear in the player |
| Audio Interchange File Format | .aiff | UNSUPPORTED | | | | | .aiff files don't appear in the player |
| WavPack | .aiff | UNSUPPORTED | | | | | .wv files don't appear in the player |

## EQ Adjustment Requirements

* Built-in support for EQ adjustment of audio sources up to 16bit/192K

## Tag Encoding

* This device does not often work well with unusual encodings for tags, best to stick to UTF-16.
* ID3v2 UTF-8 text frames (`encoding 0x03`) render as garbage because the firmware has no UTF-8 decoder. The checker marks those MP3/WAV/DSF files LIMITED; Convert rewrites the frames as UTF-16 (`0x01`) and saves ID3v2.3, without re-encoding audio.

## Album Art Requirements

The device has specific requirements for embedded album artwork to be displayed correctly:

* **Format:** Must be JPEG (`image/jpeg`). PNG, GIF, BMP, and other formats are UNSUPPORTED.
* **JPEG Encoding:** Must be **Baseline (Non-progressive) JPEG**. Progressive JPEGs are UNSUPPORTED and will fail to display.
* **Resolution:** Must be **1000x1000 pixels or lower**. Resolutions exceeding 1000 pixels may display but will load slowly.

## File Name Compatibility

The device handles most file names well, but there are exceptions based on tested edge cases:

| Name | Failure Description | Media Plays? | Supported Visually? |
| ---- | ------------------- | ------------ | ------------------- |
| Emojis | File names containing emojis (standard, skin tones, zero-width joiners, flags, etc.) | ✅ | ❌ |
| Complex Asian Scripts | Specific scripts like Hindi (Devanagari), Bengali, Khmer, and Burmese are not supported. (Note: Thai, Chinese, Japanese, and Korean are supported). | ✅ | ❌ |
| Zalgo / Complex Diacritics | The text will render, but complex combining characters/diacritics are stripped and ignored. | ✅ | ⚠️ |
| Latin Extended / Other Unicode | Characters like `café`, Cyrillic, Greek, Arabic, Hebrew, and Math symbols work correctly. | ✅ | ✅ |
| Long Names/ Special Punctuation | Works within reasonable filesystem bounds. | ✅ | ✅ |

## Metadata (Tag) Compatibility

The device has a very simplistic internal metadata parser that can be easily confused by non-standard tags often injected by advanced library managers (like MusicBrainz Picard).

**Supported Tags:**

- TITLE (also matches Title)
- ARTIST (also matches Artist)
- ALBUM (also matches Album)
- ALBUMARTIST (also matches Album Artist)
- TRACKNUMBER (also matches Track)
- DISCNUMBER (also matches Discnumber)
- GENRE (also matches Genre)

**Vorbis comment order (FLAC / OGG):**

The firmware walks Vorbis comments in file order and copies values into a 128-character SRAM buffer. An oversized comment (commonly embedded `LYRICS` or `TIDAL_DATA`, thousands of characters) that appears *before* a core tag overflows that buffer.

- Before `ALBUM`: the player often hard-reboots.
- After `ALBUM` but before `ARTIST` / `ALBUMARTIST` / `TITLE` / `TRACKNUMBER`: the file may still play, but those later tags never land in the library, so albums fail to group.

Safe order is every firmware-parsed tag first (`TITLE`, `ARTIST`, `ALBUM`, `ALBUMARTIST`, `TRACKNUMBER`, `DISCNUMBER`, `GENRE`), with oversized values such as `LYRICS` last. The checker flags any oversized comment that appears before a core tag still waiting in the file. Convert reorders core tags to the front and, by default, deletes every tag the device does not read.
