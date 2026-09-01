import logging
import mutagen
from pathlib import Path

logger = logging.getLogger(__name__)

class MetadataSanitizer:
    def __init__(self):
        self.known_tags = {"title", "artist", "album", "genre", "tracknumber", "albumartist", "discnumber"}
        self.bloat_prefixes = ("musicbrainz_", "tidal_")
        self.limit = 20

    def check_metadata(self, file_path: str | Path) -> tuple[bool, str]:
        """
        Check if the metadata contains too many unknown tags that would
        exceed the device's parser limits.
        
        For FLAC: checks Vorbis Comments against the 20-tag unknown limit.
        For ID3v2 formats (MP3, WAV, DSF): checks for excessive non-standard
        frames that push core tags outside the firmware's ~2-4KB read window.
        
        Returns a tuple: (is_compatible, reason_if_not)
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None or audio.tags is None:
                return True, ""
                
            # FLAC: Vorbis Comment parser has a hard unknown tag limit
            if isinstance(audio, mutagen.flac.FLAC):
                unknown_count = 0
                for key in audio.tags.keys():
                    if key.lower() not in self.known_tags:
                        unknown_count += 1

                if unknown_count > self.limit:
                    return False, f"Snowsky Hardware Limit Exceeded: File contains {unknown_count} unknown tags, exceeding the device's hardcoded safety limit of {self.limit}. The device's parser will abort and crash. Click 'Fix Compatibility' to strip useless tracking tags and safely reduce the count."
                
                return True, ""

            # ID3v2 formats: the firmware reads a fixed ~2-4KB window of the
            # ID3v2 header. Non-standard frames (TXXX, COMM, USLT, etc.) at
            # the start push core tags (TIT2, TPE1) outside this window.
            core_id3_frames = {"TIT2", "TPE1", "TALB", "TPE2", "TCON", "TRCK", "TPOS", "APIC"}
            if hasattr(audio.tags, "getall"):
                unknown_count = 0
                for frame_id in audio.tags.keys():
                    # Extract base frame ID (e.g. "TXXX:foo" -> "TXXX")
                    base_id = str(frame_id).split(":")[0].upper()
                    if base_id not in core_id3_frames:
                        unknown_count += 1

                if unknown_count > self.limit:
                    return False, f"Snowsky Hardware Limit Exceeded: File contains {unknown_count} non-standard ID3 frames, exceeding the device's safety limit of {self.limit}. Excess frames push core tags outside the firmware's read window, causing missing metadata on the device."

            return True, ""

        except Exception as e:
            logger.debug(f"Failed to check metadata for {file_path}: {e}")
            return True, ""


    def sanitize(self, file_path: str | Path, preserve_third_party_tags: bool = False) -> bool:
        """
        Safely strip bloat tags until the unknown tag count is below the limit.
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None or audio.tags is None:
                return False

            unknown_tags = []
            for key in audio.tags.keys():
                if key.lower() not in self.known_tags:
                    unknown_tags.append(key)

            if len(unknown_tags) <= self.limit:
                return True # Nothing to fix
            
            if preserve_third_party_tags:
                # Delicate bloat stripping: try to keep as many custom tags as possible
                # by only targeting known bloat (musicbrainz, tidal) and stripping just enough
                tags_to_delete = []
                for key in unknown_tags:
                    key_lower = key.lower()
                    if key_lower.startswith(self.bloat_prefixes):
                        tags_to_delete.append(key)

                needed_deletions = len(unknown_tags) - self.limit
                if needed_deletions > len(tags_to_delete):
                    for key in unknown_tags:
                        if key not in tags_to_delete and key.lower() != "lyrics":
                            tags_to_delete.append(key)
                            if len(tags_to_delete) >= needed_deletions:
                                break
                
                for key in tags_to_delete:
                    del audio[key]
            else:
                # Aggressively delete all unknown tags except 'lyrics'
                # to guarantee we don't accidentally leave exactly enough tags to hit the abort 
                # limit before the 'TITLE' tag is parsed (since Mutagen shuffles tag order on save).
                for key in unknown_tags:
                    if key.lower() != "lyrics":
                        del audio[key]

            audio.save()
            return True

        except Exception as e:
            logger.debug(f"Failed to sanitize metadata for {file_path}: {e}")
            return False
