"""
Utility to safely write metadata to audio files.
"""

import mutagen
from mutagen.id3 import ID3NoHeaderError, ID3
from mutagen.easyid3 import EasyID3
from typing import Dict, Tuple

def save_metadata(filepath: str, tags: Dict[str, str]) -> Tuple[bool, str]:
    """
    Saves the provided dictionary of tags to the audio file.
    Supports MP3 via EasyID3 and FLAC natively via mutagen.
    
    Returns:
        (success: bool, error_message: str)
    """
    try:
        audio = mutagen.File(filepath, easy=False)
        if audio is None:
            return False, "Unsupported or corrupt audio file."
            
        is_mp3 = filepath.lower().endswith(".mp3")
        
        if is_mp3:
            # Try to load EasyID3 to handle standard text frames automatically
            try:
                easy = EasyID3(filepath)
            except ID3NoHeaderError:
                ID3().save(filepath, v2_version=3)
                easy = EasyID3(filepath)
            except Exception:
                easy = None
                
            if easy is not None:
                # We only write supported EasyID3 keys for MP3 to prevent frame corruption
                valid_easy_keys = easy.valid_keys.keys()
                for key, val in tags.items():
                    k_lower = key.lower()
                    if k_lower in valid_easy_keys:
                        easy[k_lower] = [val]
                easy.save(filepath, v2_version=3)
                
                # We could potentially handle raw TXXX frames via ID3 here if needed,
                # but EasyID3 handles standard tags perfectly.
                return True, ""
                
        # Handle FLAC / OGG and other native dictionary-like tag structures
        if audio.tags is not None:
            for key, val in tags.items():
                audio.tags[key] = [val]
            audio.save()
            return True, ""
            
        return False, "No tag structure found in file."
        
    except Exception as e:
        return False, f"Failed to save metadata: {str(e)}"
