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
        Check if the metadata contains more than 32 "Unknown" tags.
        Returns a tuple: (is_compatible, reason_if_not)
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None or audio.tags is None:
                return True, ""
                
            # The 32 unknown tag limit specifically affects the device's Vorbis Comment
            # parser. MP3 files (ID3 tags) use completely different keys and firmware logic.
            if not isinstance(audio, mutagen.flac.FLAC):
                return True, ""

            unknown_count = 0
            for key in audio.tags.keys():
                if key.lower() not in self.known_tags:
                    unknown_count += 1

            if unknown_count > self.limit:
                return False, f"Snowsky Hardware Limit Exceeded: File contains {unknown_count} unknown tags, exceeding the device's hardcoded safety limit of {self.limit}. The device's parser will abort and crash. Click 'Fix Compatibility' to strip useless tracking tags and safely reduce the count."
            
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
