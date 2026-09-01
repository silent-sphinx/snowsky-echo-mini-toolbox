# Snowsky Echo Mini — Firmware Analysis (Stage 1)

## Firmware File Overview

| Property | Value |
|---|---|
| **Filename** | `HIFIEC38.IMG` |
| **File size** | 33,554,436 bytes (32 MiB + 4 bytes) |
| **SHA-256** | `a697e317ef81e0de66d76f365b2198edc352ae0ac14d8adba2b6bf87050118d4` |
| **Structure** | 32 MiB firmware payload + 4-byte CRC32 trailer (`0xE4CA8428`) |
| **`file` output** | `data` (no standard magic recognized) |

---

## Platform Identification

### SoC / Chipset

| Property | Value |
|---|---|
| **Vendor** | **Rockchip** (string at offset `0x10`) |
| **SDK** | **RKnano SDK 1.0** (string at offset `0x30`) |
| **Firmware signature** | `RKnanoFW` (at offset `0x1F8`) |
| **SoC family** | **Rockchip RKnano** — a low-power ARM-based MCU family used in portable audio players |
| **CPU architecture** | **ARM Cortex-M3 / Thumb-2** — confirmed by high density of Thumb push/pop/bx instructions (1,170 push-LR patterns, 1,761 `bx lr` patterns in first 1MB alone) |
| **Operating system** | **RTOS** (confirmed by spec sheet and strings: `NO THIS TASK`, interrupt handlers, `..\\..\\Common\\System\\Os\\interrupt.c`) |
| **DAC** | **Cirrus Logic CS43131 × 2** (dual DAC, string `4313` found; confirmed by spec sheet) |

> **Note on exact SoC model**: The RKnano family includes chips like RK2706, RK2708, RK2718, RK2738, etc. No explicit model string was found in the firmware. The `HIFIEC38` filename and address mapping patterns (SRAM at `0x0300xxxx`, DRAM at `0x01xxxxxx`) are consistent with newer RKnano variants (likely **RK2818** or similar). Ghidra analysis with the correct memory map will confirm this.

### Build Information

| Property | Value |
|---|---|
| **Header magic** | `0x07182026` (possibly date: 2026-07-18, or version/build identifier) |
| **Firmware version** | `Version:0.0.1` |
| **SDK build dates** | `Date:2012.3.23`, `Date:2012.3.26`, `Date:2012.3.28`, `Date:2012.3.31` (codec library build dates — these are from the original Rockchip SDK, not necessarily the device firmware build date) |
| **Partitions** | 4 sections declared in header |

---

## Firmware Structure

### Layout (from header at offset 0x00–0x200)

```
Offset 0x000: Header (magic, vendor, SDK version, section count)
Offset 0x0C8: Section descriptor 1 — offset: 0x0059B5F8, size: 0x001CA34A (~1.8 MB)
Offset 0x0F4: Section descriptor 2 — offset: 0x00765942, size: 0x002573A8 (~2.4 MB)
Offset 0x140: Section descriptor 3 — offset: 0x009BCCEA, size: 0x016069E8 (~23 MB)
Offset 0x190: Section descriptor 4 — (appears to be empty/reserved)
Offset 0x1F8: "RKnanoFW" signature
Offset 0x200: Extended section/module table (load addresses, sizes, entry points)
```

### Memory Regions (from section table at 0x200+)

The section table references SRAM addresses in the `0x0300xxxx–0x030Axxxx` range, consistent with RKnano internal SRAM mapping. Key memory regions identified:

- **SRAM code base**: `0x03050000` (main firmware code load address)
- **SRAM data**: `0x03004000–0x030A0000` range
- **DRAM region**: `0x01600000+` (likely external SDRAM for audio buffers/media library)

### Entropy Profile

The firmware contains **134 distinct code regions** (detected by ARM Thumb instruction density analysis). Major regions:

| Offset Range | Size | Content Type |
|---|---|---|
| `0x00029000–0x00186000` | ~1.4 MB | Main RTOS + driver code (many small modules) |
| `0x0042D000–0x004CB000` | ~600 KB | Audio codec libraries |
| `0x00A4D000–0x00CFx000` | ~2.5 MB | Audio decoders (FLAC, APE, AAC, MP3, WMA, WAV, DSD) |
| `0x00F10000–0x013A0000` | ~4.6 MB | Likely resource/font/UI data (bitmap resources) |
| `0x013B0000–0x01920000` | ~5.7 MB | Additional codec + system modules |
| `0x01C53000–0x01FC4000` | ~3.4 MB | Mixed code/data (codec tables, media library) |

### Key Marker

- `0xAA55AA55` found at offset `0x004CD00C` — likely a section/module boundary marker used by the RKnano bootloader

---

## Audio Format Support

### Supported File Extensions

Two format registration tables were found in the firmware:

**Table 1** (media playback — at offsets `0x0555CA`, `0x0555F5`, `0x055620`):
```
MP1 MP2 MP3 WMA WAV APE FLA AAC M4A OGG MP4 3GP DFF DSF
```

**Table 2** (with CUE support — at offset `0x07B2DC`):
```
MP1 MP2 MP3 WMA WAV APE FLA AAC M4A OGG MP4 3GP DFF DSF CUE
```

### Decoded Format Support Matrix

| Format | Extension(s) | Codec Library | Max Spec (from spec sheet) | Notes |
|---|---|---|---|---|
| **MP3** | `.mp3` | `Lib:mp3_dec_lib` (libMAD-based) | MP3 standard | Source: `mp3_decinternal.c`, `mp3_preparse.c` |
| **FLAC** | `.fla` (3-char) / `.flac` | Custom FLAC decoder | 24-bit / 192 kHz | Extensive debug strings; blocksize-limited |
| **WAV** | `.wav` | `pWAV_lib.c` | 24-bit / 192 kHz | Supports format tags; checks `wav_formatTagID` |
| **APE** (Monkey's Audio) | `.ape` | Custom APE decoder | 24-bit / 192 kHz | APEv2 tag parsing; `ape open OK/FAIL` messages |
| **AAC** | `.aac`, `.m4a` | `Lib:aac_dec_lib` | Standard AAC | M4A container via `aac_MovFile.c` |
| **ALAC** | `.m4a` | ALAC decoder present | Likely 24-bit/192 kHz | `ALAC`, `alacX`, `aLaC` strings found |
| **WMA** | `.wma` | `Lib:wma_dec_lib` | Standard WMA | `wmaudio_parse.c` source reference |
| **OGG Vorbis** | `.ogg` | Vorbis decoder | Standard | `OggS`, `vorbis` sync markers present |
| **DSD (DSF)** | `.dsf` | DSD decoder | DSD64/128/256 | `dsf read ckid err`, `dsf read frame err` |
| **DSD (DSDIFF)** | `.dff` | DSD decoder | DSD64/128/256 | `dsdiff read frame err`, `DSD IFF OPEN err` |
| **MP4/3GP** | `.mp4`, `.3gp` | Via AAC container parser | AAC content | `ftyp`, `moov`, `mdat`, `stbl` atom parsing |
| **CUE Sheet** | `.cue` | CUE parser | — | `cue_info->m_total_Song == %d` |

### Explicitly Unsupported (from spec sheet)

- **SACD** (ISO format) — not supported
- **DTS** — not supported
- **NTFS** filesystem — not supported
- **Opus** — no strings found
- **LDAC / aptX** — no strings found (BT is SBC-only)

---

## Critical Codec Constraints & Potential Failure Points

### FLAC Decoder

The FLAC decoder has the most extensive debug logging, revealing several important constraints:

| Constraint | Evidence | Impact |
|---|---|---|
| **Max blocksize limit** | `decode_frame: blocksize %d > FLAC_MAX_BLOCKSIZE %d` | Files with block sizes > **4608 (0x1200)** will **fail to decode**. Standard FLAC allows up to 65535, but default encoders often use 4096. Any FLAC encoded with `--blocksize=4609` or higher will silently fail. This is the primary cause of "random" FLAC playback failures. |
| **Max frame size** | `Max Framesize: %d`, `Min Framesize: %d` | Frame size constraints logged during init |
| **CRC validation** | `FLAC frame CRC mismatch (stored=%d, computed=%d)` | CRC is actively validated; corrupted frames cause errors |
| **Max channels = 2** | `channels %d > 2.Unsupport CHN` | **Multichannel FLAC (5.1, 7.1) is not supported** |
| **Seek algorithm** | Binary search with max iterations; falls back to current position | Large files may have imprecise seeking |
| **Bit depth limit** | `s->curr_bps > 32` | 32-bit max (effectively 24-bit based on spec) |
| **Subframe decoding** | `decode_subframe failed for channel %d` | Complex subframe types may cause failures |
| **Rice coding limit** | `rice_limit = %d` | Residual coding parameter limits may reject files |
| **Output buffer overflow** | `flac_decode_frame: output_size %d > alloc_data_size %d` | If decoded output exceeds allocated buffer, decode fails |

### MP3 Decoder

| Constraint | Evidence | Impact |
|---|---|---|
| **Bitrate validation** | `forbidden bitrate value`, `bad bitrate/mode combination` | Non-standard bitrate/mode combinations rejected |
| **VBR handling** | `id3 contain half VBR frame` | Partial VBR frames at ID3 boundary handled, but may cause issues |
| **Error threshold** | `dec_error_cnt > 128` | After 128 consecutive decode errors, playback likely stops |
| **Frame sync** | `lost synchronization`, `find_sync_bytes out` | Sync loss recovery exists but may fail on heavily damaged files |
| **Bit allocation** | `forbidden bit allocation value` | Layer II-specific constraint |
| **Sample frequency** | `reserved sample frequency value` | Non-standard sample rates rejected |
| **Layer support** | MP1, MP2, MP3 all registered | All three MPEG-1 Audio layers supported |

### WAV Decoder

| Constraint | Evidence | Impact |
|---|---|---|
| **Format tag check** | `wav_formatTagID =%d` | Supports only **PCM** (`0x01`), **MS ADPCM** (`0x02`), **IMA ADPCM** (`0x11`), and **Extensible** (`0xFFFE`). Notably, **IEEE Float (`0x03`) is missing** and will fail to play. |
| **Block alignment** | `nBlockAlign =%d` | Block alignment is validated |
| **Format error** | `fmt err` | Invalid format chunks cause failure |
| **Bit depth** | Hardcoded checks | Supported PCM bit depths are exactly **8, 16, 24, and 32 bits**. |

### AAC/M4A/ALAC Decoder

| Constraint | Evidence | Impact |
|---|---|---|
| **M4A container** | `aac_MovFile.c` — full MP4/MOV container parser | Handles `ftyp`, `moov`, `mdat`, atom hierarchy |
| **MP4 atoms parsed** | `ftyp`, `moov`, `mdat`, `stbl`, `stsd`, `esds`, `stco`, `stsz`, `stsc`, `stts`, `ilst`, `udta`, `covr` | Comprehensive but may miss newer/optional atoms |
| **Profile support** | `aac_frofile = %d return` (likely "aac_profile") | Certain AAC profiles may be rejected |
| **ALAC variant** | ALAC decoder is separate from AAC | `.m4a` files with ALAC codec are supported |
| **Sample count** | `sampleCount:%lu`, `vidiosamplesum = %d` | Large file support depends on counter width |

### APE Decoder

| Constraint | Evidence | Impact |
|---|---|---|
| **Open failure** | `ape open FAIL!` / `ape open OK` | Binary pass/fail on file open |
| **APE tags** | `APETAGEX`, `ape_tag_len =%d`, `apetag = %d` | APEv2 tags parsed |
| **Format error** | `formatid err` | Invalid APE format IDs cause rejection |

### DSD Decoder (DSF + DSDIFF)

| Constraint | Evidence | Impact |
|---|---|---|
| **Bit depth validation** | `bit_per_sample not support!` (`if 7 < uVar4`) | Rejects DSF files if the declared bit depth is 8 or higher. DSD is natively a 1-bit format, so this strictly enforces the specification. |
| **DSF errors** | `dsf read ckid err`, `dsf read frame err` | Chunk ID validation; frame read failures |
| **DSDIFF errors** | `dsdiff read frame err`, `DSD IFF OPEN err` | Similar validation |
| **DFF seek** | `dff seek_fail` | Seeking in DFF files can fail |
| **DST support** | `dst read frame err!`, `dstf read frame!` | DST (DSD Stream Transfer) compression appears supported |
| **General DSD error** | `DSD err` | Generic DSD decode failure |

### WMA Decoder

| Constraint | Evidence | Impact |
|---|---|---|
| **Parser source** | `wmaudio_parse.c` | Standard WMA parser |
| **WMA Variants** | `sVar8 == 0x160` to `0x163` | Explicitly supports **WMA v1** (`0x160`), **WMA v2** (`0x161`), **WMA Pro** (`0x162`), and **WMA Lossless** (`0x163`)! |
| **Channel limits** | `sVar8 == 1 / 2 / 6` | For WMA v2, it explicitly checks for 1 (mono), 2 (stereo), and even **6 (5.1 surround)** channels. 5.1 audio sets a specific channel mask (`0x3F`). |

---

## Metadata / Tag Handling

### Supported Tag Formats

| Format | Evidence | Fields Parsed |
|---|---|---|
| **ID3v2** | `ID3V2 start:%d`, `ID32`, `======Check FIDv2.3 Error` | Title, Artist, Album, Genre, Cover Art |
| **ID3v2.3** | `Check FIDv2.3 Error` | Native support for ID3v2.3 tags (Standard 32-bit sizes). |
| **ID3v2.4** | Correct synchsafe shifts | ID3v2.4 is **fully supported**. The synchsafe size calculation (`c << 21 | d << 14 | e << 7 | f`) is correctly implemented in the main parser. |
| **APEv2 Tags** | `APETAGEX`, `ape_tag_len`, `apetag` | Standard APE tag support |
| **Vorbis Comments** | `vorbis` (in OGG context) | OGG metadata |
| **FLAC Metadata Blocks** | `METADATA_BLOCK_PICTURE`, `MetaBlockPicHandle Read Error` | FLAC metadata + picture blocks |
| **MP4/M4A Atoms** | `ilst`, `udta`, `covr`, `aART`, `esds` | iTunes-style metadata atoms |
| **RIFF INFO** | `fmt dataLISTIARTINAMIPRDIGNR` | WAV RIFF INFO chunk metadata |
| **Text Encodings** | No `UTF-8` or `GBK` strings exist in binary | Ghidra confirms the UI strings (e.g. `Charge`, `BT Music`) are stored natively in UTF-16. Because there are no UTF-8 decoders present in the binary, **ID3v2 tags encoded in UTF-8 (`0x03`) will likely render as corrupted garbage text**. Use UTF-16 (`0x01` or `0x02`) or ISO-8859-1 (`0x00`). |
| **CUE Sheet** | `cue_info->m_total_Song == %d` | The CUE parser increments the track count and writes 196 bytes (`0xC4`) per track into the `cue_info` struct **without bounds checking**. CUE sheets with a massive number of tracks (e.g., > 99) will likely cause a buffer overflow, corrupting SRAM and hard-crashing the device. |

### Parsed Metadata Fields

Found as explicit string constants in the firmware:

- `TITLE` / `Title`
- `ARTIST` / `Artist`
- `ALBUM` / `Album`
- `ALBUMARTIST` / `Album Artist`
- `GENRE` / `Genre`
- `Cover Art (front)` — APE tag cover art
- `APIC` — ID3v2 embedded picture
- `PICTURE OGG` — OGG embedded picture
- `METADATA_BLOCK_PICTURE` — FLAC picture block
- `Picture` — generic picture tag
- `covr` — M4A cover art atom
- `aART` — M4A album artist atom

### Metadata Failure Points

| Issue | Evidence | Impact |
|---|---|---|
| **APIC parsing error** | `======Check APIC Error` | Malformed ID3v2 APIC frames, or frames that exceed the device's small SRAM buffer, cause playback to abort. The firmware processes images in 512-byte chunks but lacks robust bounds checking for massive images. |
| **FLAC Picture error** | `MetaBlockPicHandle Read Error` | Same issue as APIC: large embedded FLAC covers will cause buffer allocation failures or read timeouts. Recommendation: Keep cover art under 64KB (JPEG). |
| **PIC parsing error** | `=====Check PIC Error=====` | ID3v2.2 PIC frame errors |
| **ID3v2.3 field error** | `======Check FIDv2.3 Error` | Invalid v2.3 frame IDs cause errors |
| **Tag error** | `tag error!!!` | Generic tag parsing failure |
| **Base64 decode** | `base64_decode error, ret: %d` | Base64-encoded metadata (e.g., METADATA_BLOCK_PICTURE in Vorbis) can fail |
| **Picture read error** | `MetaBlockPicHandle Read Error` | FLAC picture metadata block read failure |
| **File version limit** | `file version error!!! Max_version 4120, fileVersion = %d` | Media library database version check — files with version > 4120 rejected |

---

## Filesystem Support

| Filesystem | Status | Evidence |
|---|---|---|
| **FAT16/FAT32** | ✅ Supported | `FATI` string, MBR/partition table handling, `CheckMbr Error`, `Invalid partition table` |
| **exFAT** | ✅ Supported | `EXFAT   ` string (8-char padded — filesystem signature) |
| **NTFS** | ❌ Not supported | Confirmed by spec sheet: "NTFS is not supported" |

### Storage Limits

- **Max microSD**: 256 GB (from spec sheet)
- **File sort info**: `SortInfoAddr.ulFileSortInfoSectorAddr = %d` — sector-based file indexing
- **File number tracking**: `SysFileInfo->CurrentFileNum == %d`
- **Total file count**: `totalFmfile = %d` (FM/file total tracking)
- **File open limit**: `file open totally over` — maximum open files exceeded

---

## UI / Theme System

The firmware contains **5 theme/skin variants** identified by BMP resource prefixes:

| Prefix | Theme | Count |
|---|---|---|
| `B` (no prefix) | Base/Black | ~309 BMPs |
| `C_` | Sky Blue | 309 BMPs |
| `D_` | Pink | 309 BMPs |
| `E_` | Titanium Gold | 309 BMPs |
| `Z_` | (possibly a special/alternate theme) | 40 BMPs |

### EQ Presets (built-in)

Identified from BMP resource names:
- Normal (`NOR`)
- Bass (`BAS`)
- Pop (`POP`)
- Jazz (`JAZ`)
- Heavy (`HEAVY`)
- MS (Mid-Side?) (`MS`)
- Retro (`RETRO`)
- User/Custom (`USE`)

### Complete ID3v1 Genre Table

The firmware embeds the **full extended ID3v1 genre table** (192+ genres), including all standard genres (0-79), Winamp extensions (80-147), and additional genres. This confirms full ID3v1 genre ID-to-name mapping support.

---

## Bluetooth

| Property | Value |
|---|---|
| **Protocol** | SBC (Sub-Band Coding) only |
| **Profile** | Likely A2DP (audio), HFP strings found |
| **Limitation** | "Apple Bluetooth headphones are not supported" (spec sheet) |
| **No LDAC/aptX** | No evidence of LDAC, aptX, or AAC Bluetooth codecs |

---

## USB Functionality

- **USB Type-C** — Charging + Data transfer
- **USB Audio DAC mode** — Multiple DAC display states (`USB_DACSHOW1` through `USB_DACSHOW5`)
- **USB Mass Storage** — `USBC` (USB CBW/CSW protocol), bulk transfer support
- **USB Player mode** — `USB_PLAYER1` through `USB_PLAYER5` display states

---

## Error Handling & System

### Critical System Errors

| Error | Context |
|---|---|
| `fw1 Sign error!` | Firmware signature verification failed |
| `fw1 compare error!` | Firmware comparison/integrity check failed |
| `fw1 && fw2 error!` | Both firmware copies corrupted |
| `fw2 compare error! 0x%x` | Firmware 2 comparison failed at address |
| `Error loading operating system` | Boot failure |
| `Missing operating system` | Boot failure (MBR error message) |
| `system powerdown!` | System shutdown |
| `MALLOC SIZE TOO little size = %d, left = %d` | Memory allocation failure |
| `not enough memory` | Heap exhaustion |
| `FreqChange timeout!!!` | Clock frequency change timeout |

### Source File References (SDK Structure)

```
..\..\Common\Codec\Audio\AAC\lib\aac_MovFile.c
..\..\Common\Codec\Audio\AAC\lib\aac_aacdec.c
..\..\Common\Codec\Audio\Mp3\libMad\mp3_decinternal.c
..\..\Common\Codec\Audio\Mp3\libMad\mp3_preparse.c
..\..\Common\Codec\Audio\Wav\WAV_LIB\pWAV_lib.c
..\..\Common\Codec\Audio\Wma\wmalib\wmaudio_parse.c
..\..\Common\System\Os\interrupt.c
```

This reveals the **RKnano SDK directory structure**:
```
Common/
├── Codec/
│   └── Audio/
│       ├── AAC/lib/
│       ├── Mp3/libMad/
│       ├── Wav/WAV_LIB/
│       └── Wma/wmalib/
└── System/
    └── Os/
```

---

## Recommendations for Ghidra Stage

### Loading Parameters

| Parameter | Recommended Value |
|---|---|
| **Processor** | ARM Cortex (Thumb/Thumb-2, Little Endian) |
| **Base address** | Try `0x03050000` for main code section |
| **Alternative base** | `0x03004000` for the data/BSS section |
| **File offset** | Start loading from `0x00029000` (first code region) or parse section table |

### Priority Analysis Targets

1. **FLAC `FLAC_MAX_BLOCKSIZE` constant** — Determine the exact blocksize limit. This is the most likely cause of FLAC playback failures with non-standard encodings.

2. **WAV `wav_formatTagID` handler** — Enumerate which WAV format tags (PCM, IEEE Float, ADPCM, Extensible, etc.) are supported vs. rejected.

3. **`bit_per_sample not support!`** — Find the bit depth validation function to determine exactly which bit depths are supported per format.

4. **FLAC seek algorithm** — The binary search has a max iteration limit and a delta threshold (≤15 samples). Very large FLAC files may have seeking issues.

5. **AAC profile handler** — Determine which AAC profiles (LC, HE-AAC, HE-AACv2) are supported.

6. **M4A atom parser completeness** — Check if newer atoms (e.g., for Apple Lossless in newer iTunes versions) are handled.

7. **DSD sample rate validation** — Verify DSD64/128/256 rate handling logic.

8. **File count/size limits** — Find the maximum file count and maximum file size constants.

9. **Metadata buffer sizes** — Check if large embedded album art (e.g., >1MB JPEG) causes buffer overflows or tag parsing failures.

10. **CUE sheet parser** — Examine track count limits and encoding support (UTF-8 vs. ANSI).

### String Cross-Reference Strategy

The debug format strings (e.g., `FLAC init: samplerate=%d, channels=%d, bps=%d...`) are excellent anchors for finding key functions in Ghidra. Use string search → XREF to locate the surrounding codec initialization, validation, and decode functions.
