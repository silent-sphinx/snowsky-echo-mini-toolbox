# Snowsky Echo Mini Firmware

The following information is derrived from reverse-engineering the 

## SOC/ Chipset

| Property | Value |
|---|---|
| **Vendor** | Rockchip |
| **SDK** | RKnano SDK 1.0 |
| **Firmware signature** | `RKnanoFW` |
| **SoC family** | Rockchip RKnano |
| **CPU architecture** | ARM Cortex-M3 / Thumb-2 |
| **Operating system** | RTOS |
| **DAC** | Cirrus Logic CS43131 × 2 |

## Audio Format Support

### Supported File Extensions

Two format registration tables were found in the firmware:

**Table 1** (media playback):
```
MP1 MP2 MP3 WMA WAV APE FLA AAC M4A OGG MP4 3GP DFF DSF
```

**Table 2** (with CUE support):
```
MP1 MP2 MP3 WMA WAV APE FLA AAC M4A OGG MP4 3GP DFF DSF CUE
```

### Decoded Format Support Matrix

| Format | Extension(s) | Codec Library | Max Spec (from spec sheet) |
|---|---|---|---|
| **MP3** | `.mp3` | `Lib:mp3_dec_lib` (libMAD-based) | MP3 standard |
| **FLAC** | `.fla` (3-char) / `.flac` | Custom FLAC decoder | 24-bit / 192 kHz |
| **WAV** | `.wav` | `pWAV_lib.c` | 24-bit / 192 kHz |
| **APE** (Monkey's Audio) | `.ape` | Custom APE decoder | 24-bit / 192 kHz |
| **AAC** | `.aac`, `.m4a` | `Lib:aac_dec_lib` | Standard AAC |
| **ALAC** | `.m4a` | ALAC decoder present | Likely 24-bit/192 kHz |
| **WMA** | `.wma` | `Lib:wma_dec_lib` | Standard WMA |
| **OGG Vorbis** | `.ogg` | Vorbis decoder | Standard |
| **DSD (DSF)** | `.dsf` | DSD decoder | DSD64/128/256 |
| **DSD (DSDIFF)** | `.dff` | DSD decoder | DSD64/128/256 |
| **MP4/3GP** | `.mp4`, `.3gp` | Via AAC container parser | AAC content |
| **CUE Sheet** | `.cue` | CUE parser | — |

### Explicitly Unsupported (from spec sheet)

- **SACD** (ISO format) — not supported
- **DTS** — not supported
- **NTFS** filesystem — not supported
- **Opus** — no strings found
- **LDAC / aptX** — no strings found (BT is SBC-only)

### FLAC

**FLAC blocksize limit:** 4608

### DSD

DSF files with a declared bit depth of 8 or higher will fail to play