# Snowsky Echo Mini - Master Format Compatibility Table

Based on reverse-engineering the RTOS firmware (`HIFIEC38.IMG`), the following table defines the exact hardware and software constraints for all supported media formats. Files exceeding these limits will fail to play, skip, or hard-crash the device.

## Audio Codecs

| Format | Extension | Bit Depth | Max Channels | Variants / Specific Constraints |
| :--- | :--- | :--- | :--- | :--- |
| **FLAC** | `.flac` | Up to 32-bit (24-bit spec) | 2 (Stereo) | **CRITICAL:** Max Blocksize is strictly **4608**. Files encoded with standard Hi-Res blocksizes (8192/16384) will silently fail. Multichannel (5.1) is explicitly rejected. |
| **WAV** | `.wav` | 8, 16, 24, 32-bit | 2 (Stereo) | **Supported:** Integer PCM (`0x01`), MS ADPCM (`0x02`), IMA ADPCM (`0x11`), Extensible (`0xFFFE`).<br>**UNSUPPORTED:** 32-bit IEEE Float (`0x03`) will fail to decode. |
| **DSD (DSF)** | `.dsf` | **1-bit ONLY** | 2 (Stereo) | **CRITICAL:** Firmware explicitly checks for `bit_depth < 8`. If a DSF file incorrectly declares an 8-bit or higher depth, it is rejected. |
| **DSD (DFF)** | `.dff` | 1-bit | 2 (Stereo) | DST (DSD Stream Transfer) compression is supported. Seeking in DFF files is known to be buggy/fail. |
| **WMA** | `.wma` | Up to 24-bit | 2 (Stereo)* | **Supported:** WMA v1, WMA v2, WMA Pro, WMA Lossless.<br>*(Note: WMA v2 parser explicitly accepts 6-channel 5.1 audio via a `0x3F` channel mask).* |
| **MP3** | `.mp3` | 16-bit | 2 (Stereo) | Supports MPEG-1 Audio Layers I, II, and III (MP1/MP2/MP3). |
| **AAC / M4A** | `.m4a`, `.aac` | Up to 24-bit | 2 (Stereo) | Full MP4/MOV container support. Demuxer only locks onto the **first** audio stream found in the file. ALAC is supported. |
| **APE** | `.ape` | Up to 24-bit | 2 (Stereo) | Standard Monkey's Audio support. |
| **OGG Vorbis** | `.ogg` | Up to 24-bit | 2 (Stereo) | Standard Ogg Vorbis support. |

---

## Metadata & Tags

| Tag Type | Supported Formats | Constraints & Bugs |
| :--- | :--- | :--- |
| **ID3v2** (MP3/WAV/DSF) | ID3v2.3, ID3v2.4, ID3v2.2 | **Fully Supported:** The ID3 parser correctly implements the ID3v2.4 synchsafe integer calculation, so both v2.3 and v2.4 tags are safe to use. |
| **ID3v2 Frame Limits** | Missing Artist/Title bug | **Read Window Bug:** The firmware only reads a small, fixed-size chunk of the ID3v2 header into SRAM (often ~2KB to 4KB). If a file contains massive unrecognized frames (like large `COMM` comments, `USLT` lyrics, or extensive `TXXX` custom tags) at the start of the header, the core tags (`TIT2`, `TPE1`) get pushed outside this read window. The player stops parsing and shows missing tags. |
| **Text Encoding** | UTF-16, ISO-8859-1 | **CRITICAL:** The device UI runs natively on UTF-16. There is **no UTF-8 decoder** in the firmware. UTF-8 encoded tags will render as corrupted garbage text on the screen. |
| **Album Art** (APIC/FLAC) | JPEG, PNG | **Memory Limit:** Album art is processed in 512-byte chunks without proper bounds checking. Embedded art larger than **~64KB** (or high resolutions) will overflow the SRAM buffer and crash playback. |
| **CUE Sheets** | `.cue` | **Buffer Overflow:** The parser increments tracks and writes 196 bytes per track into a fixed struct without bounds checking. Massive CUE sheets (> 99 tracks) will crash the OS. |
| **Other Formats** | APEv2, MP4 Atoms, Vorbis | Supported MP4 atoms: `ilst`, `udta`, `covr`, `aART`, `esds`. Supported WAV RIFF chunks: `LIST`, `IART`, `INAM`, `IPRD`, `IGNR`. |
| **Parsed Fields** | Title, Artist, Album, etc. | The firmware explicitly extracts strings for: **TITLE**, **ARTIST**, **ALBUM**, **ALBUMARTIST**, **GENRE**, **TRACKNUMBER**, and **Cover Art**. Other tags are ignored. |
| **Text Length** | Max 128 characters | **Safety Limit:** Extracted strings (like Title/Artist) are copied into fixed-size SRAM arrays. To prevent buffer overflows and UI corruption, text tags should be strictly capped at **128 characters**. |

## Totally Unsupported Formats

The firmware does **not** contain parsers or strings for the following formats. They will be ignored or skipped by the device:
- **NTFS** / **exFAT** (Requires FAT16/FAT32 for best compatibility)
- **Opus** (`.opus`)
- **SACD ISO** (`.iso`)
- **DTS** (`.dts`)
- **IEEE Float WAVs**
- **Multichannel FLACs**

---
*Created via Ghidra Firmware Analysis*
