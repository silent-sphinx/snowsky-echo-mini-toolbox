"""
Global application constants and configurations.
"""

# Supported media format extensions recognized by the Snowsky Echo Mini firmware.
# Extracted from firmware reverse-engineering (Table 2).
SUPPORTED_MEDIA_EXTENSIONS = {
    ".mp1", ".mp2", ".mp3", ".wma", ".wav", ".ape", ".fla", ".flac", 
    ".aac", ".m4a", ".ogg", ".mp4", ".3gp", ".dff", ".dsf", ".cue"
}

# Maximum track limit supported by the Snowsky Echo Mini firmware hardware
MAX_TRACK_LIMIT = 8192
