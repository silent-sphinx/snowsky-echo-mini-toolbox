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

## Album Art Requirements

The device has specific requirements for embedded album artwork to be displayed correctly:

* **Format:** Must be JPEG (`image/jpeg`). PNG, GIF, BMP, and other formats are UNSUPPORTED.
* **JPEG Encoding:** Must be **Baseline (Non-progressive) JPEG**. Progressive JPEGs are UNSUPPORTED and will fail to display.
* **Resolution:** Must be **1000x1000 pixels or lower**. Resolutions exceeding 1000 pixels may display but will load slowly.

## File Name Compatibility

The device handles most file names well, but there are exceptions based on tested edge cases:

* **Emojis:** UNSUPPORTED. File names containing emojis (standard, skin tones, zero-width joiners, flags, etc.) do not work.
* **Complex Asian Scripts:** UNSUPPORTED. Specific scripts like Hindi (Devanagari), Bengali, Khmer, and Burmese are not supported. (Note: Thai, Chinese, Japanese, and Korean are supported).
* **Zalgo / Complex Diacritics:** RENDERS AS STANDARD TEXT. The text will render, but complex combining characters/diacritics are stripped and ignored.
* **Latin Extended / Other Unicode:** SUPPORTED. Characters like `café`, Cyrillic, Greek, Arabic, Hebrew, and Math symbols work correctly.
* **Long Names / Special Punctuation:** SUPPORTED. Works within reasonable filesystem bounds.
