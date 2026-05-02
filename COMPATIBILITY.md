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
