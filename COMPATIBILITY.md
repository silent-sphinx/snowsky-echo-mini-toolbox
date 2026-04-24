# Snowsky Echo Mini Compatibility Requirements

This document describes exactly how the media compatibility checker evaluates audio files for the Snowsky Echo Mini.

## Supported Formats

### Lossy (always supported)

These formats are always marked **SUPPORTED** without sample-rate or bit-depth validation:

- `mp3`
- `ogg`
- `m4a`
- `wma`

### PCM (validated)

These formats are supported only when they meet the PCM limits below:

- `wav`
- `flac`
- `ape`

**PCM limits**:

- **Sample rate**: must be $\le 192{,}000$ Hz (192 kHz)
- **Bit depth**: must be $\le 24$ bits

If either value exceeds the limits, the file is **UNSUPPORTED**. If required metadata is missing, the file is **UNKNOWN** even though the extension is supported.

### DSD (validated)

These formats are supported only when their DSD rates match an allowed multiple:

- `dsf`
- `dff`

**DSD limits**:

- The sample rate must map to one of **DSD64**, **DSD128**, or **DSD256**.
- Mapping is calculated by comparing the sample rate to base frequencies of **44.1 kHz** or **48 kHz** and finding a ratio that is within 0.5 of an allowed DSD multiple.
- If no allowed multiple is recognized or if the mapped multiple is higher than DSD256, the file is **UNSUPPORTED**.
- If the sample rate is missing, the file is **UNKNOWN**.

## Explicitly Unsupported Formats

These are always marked **UNSUPPORTED**:

- `dts`
- `dtshd`
- `sacd`
- `iso`

## Other Known Audio Extensions

The checker recognizes these extensions as audio for scanning, but they are **not supported** and are marked **UNSUPPORTED** with a reason that indicates the extension is recognized audio but not in the supported list:

- `aac`
- `aif`
- `aifc`
- `aiff`
- `alac`
- `m4b`
- `m4p`
- `mka`
- `mp1`
- `mp2`
- `opus`
- `oga`
- `wv`

## File Scanning Rules

- The tool scans files recursively under the target directory.
- Files starting with a dot (for example `.DS_Store` or `._Track.flac`) are ignored.
- Files with no extension are **UNSUPPORTED**.
- Files with extensions outside the known audio lists are counted as skipped and are not evaluated.

## Metadata Sources and Limitations

The checker relies on metadata to evaluate PCM and DSD limits:

- **Primary**: `mutagen` (recommended, most accurate)
- **WAV fallback**: Python `wave` module (WAV only)
- **Secondary**: `ffprobe` (if available)

If metadata cannot be read, PCM/DSD files are marked **UNKNOWN**. Lossy formats do not require metadata to be supported.
