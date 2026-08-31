"""
Generates realistic demo music metadata for UI design evaluation.

Produces ~200 sample tracks spanning multiple genres, codecs, and artists
with realistic metadata fields and intentional gaps to demonstrate
missing-metadata filtering.
"""

from dataclasses import dataclass, field
import random


@dataclass
class TrackMetadata:
    """Represents a single audio track's metadata for table display."""
    file_path: str = ""
    file_name: str = ""
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    genre: str | None = None
    year: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    duration_seconds: int = 0
    codec: str = "flac"
    bitrate_kbps: int = 0
    sample_rate_hz: int = 44100
    bit_depth: int = 16
    channels: int = 2
    file_size_bytes: int = 0
    has_album_art: bool = False
    has_lyrics: bool = False


# ── Artist/Album Catalogue ──────────────────────────────────────────────────

_CATALOGUE = [
    {
        "artist": "Mitski",
        "albums": [
            ("Be the Cowboy", 2018, "Art Pop", [
                "Geyser", "Why Didn't You Stop Me?", "Old Friend",
                "A Pearl", "Lonesome Love", "Remember My Name",
                "Me and My Husband", "Come into the Water",
                "Nobody", "Pink in the Night", "A Horse Named Cold Air",
                "Washing Machine Heart", "Blue Light", "Two Slow Dancers",
            ]),
            ("Laurel Hell", 2022, "Synth Pop", [
                "Valentine, Texas", "Working for the Knife", "Stay Soft",
                "Everyone", "Heat Lightning", "The Only Heartbreaker",
                "Love Me More", "There's Nothing Left For You",
                "Should've Been Me", "I Guess", "That's Our Lamp",
            ]),
        ],
    },
    {
        "artist": "Chloe Slater",
        "albums": [
            ("Harriet", 2023, "Indie Rock", [
                "Harriet", "Sinking Ship", "Riot Youth",
                "Concrete Jungle", "Paper Tigers", "Midnight Oil",
                "Ghost Town", "Velvet Underground", "Neon Signs",
                "Last Train Home",
            ]),
            ("Fluorescent Dreams", 2024, "Dream Pop", [
                "Fluorescent", "Haze", "Underwater",
                "Silver Lining", "Moonrise", "Static",
                "Glass Houses", "Echo Chamber",
            ]),
        ],
    },
    {
        "artist": "Le Tigre",
        "albums": [
            ("Le Tigre", 1999, "Electroclash", [
                "Deceptacon", "Hot Topic", "What's Yr Take on Cassavetes?",
                "The The Empty", "Phanta", "Eau D'Bedroom Dancing",
                "Let's Run", "My My Metrocard", "Friendship Station",
                "Slideshow at Free University", "Dude, Yr So Crazy!",
            ]),
        ],
    },
    {
        "artist": "Japanese Breakfast",
        "albums": [
            ("Jubilee", 2021, "Indie Pop", [
                "Paprika", "Be Sweet", "Kokomo, IN",
                "Slide Tackle", "Posing in Bondage", "Sit",
                "Savage Good Boy", "In Hell", "Tactics",
                "Posing for Cars",
            ]),
        ],
    },
    {
        "artist": "Phoebe Bridgers",
        "albums": [
            ("Punisher", 2020, "Indie Folk", [
                "DVD Menu", "Garden Song", "Kyoto",
                "Punisher", "Halloween", "Chinese Satellite",
                "Moon Song", "Savior Complex", "ICU",
                "Graceland Too", "I Know the End",
            ]),
        ],
    },
    {
        "artist": "Radiohead",
        "albums": [
            ("OK Computer", 1997, "Alternative Rock", [
                "Airbag", "Paranoid Android", "Subterranean Homesick Alien",
                "Exit Music (For a Film)", "Let Down", "Karma Police",
                "Fitter Happier", "Electioneering", "Climbing Up the Walls",
                "No Surprises", "Lucky", "The Tourist",
            ]),
            ("In Rainbows", 2007, "Art Rock", [
                "15 Step", "Bodysnatchers", "Nude",
                "Weird Fishes/Arpeggi", "All I Need", "Faust Arp",
                "Reckoner", "House of Cards", "Jigsaw Falling into Place",
                "Videotape",
            ]),
        ],
    },
    {
        "artist": "Snail Mail",
        "albums": [
            ("Valentine", 2021, "Indie Rock", [
                "Valentine", "Ben Franklin", "Headlock",
                "Light Blue", "Forever (Sailing)", "Madonna",
                "c. et al.", "Glory", "Automate", "Mia",
            ]),
        ],
    },
    {
        "artist": "Sufjan Stevens",
        "albums": [
            ("Carrie & Lowell", 2015, "Folk", [
                "Death with Dignity", "Should Have Known Better",
                "All of Me Wants All of You", "Drawn to the Blood",
                "Eugene", "Fourth of July", "The Only Thing",
                "Carrie & Lowell", "John My Beloved",
                "No Shade in the Shadow of the Cross", "Blue Bucket of Gold",
            ]),
        ],
    },
    {
        "artist": "Beach House",
        "albums": [
            ("Depression Cherry", 2015, "Dream Pop", [
                "Levitation", "Sparks", "Space Song",
                "Beyond Love", "10:37", "PPP",
                "Wildflower", "Bluebird", "Days of Candy",
            ]),
        ],
    },
    {
        "artist": "Unknown Artist",
        "albums": [
            ("Unknown Album", None, None, [
                "Track 01", "Track 02", "Track 03",
                "audio_recording_2024", "voice_memo_final",
                "Untitled", "mixdown_v3",
            ]),
        ],
    },
]

_CODECS = [
    ("flac", 0, 44100, 16),
    ("flac", 0, 48000, 24),
    ("flac", 0, 96000, 24),
    ("mp3", 320, 44100, 16),
    ("mp3", 256, 44100, 16),
    ("mp3", 192, 44100, 16),
    ("mp3", 128, 44100, 16),
    ("aac", 256, 44100, 16),
    ("ogg", 192, 44100, 16),
    ("wav", 1411, 44100, 16),
    ("opus", 128, 48000, 16),
]


def generate_demo_tracks(count: int = 200) -> list[TrackMetadata]:
    """
    Generate a list of realistic demo track metadata.

    Some tracks intentionally have missing title/artist/album fields
    to demonstrate missing-metadata filtering.
    """
    random.seed(42)  # deterministic for consistent demo
    tracks: list[TrackMetadata] = []

    for entry in _CATALOGUE:
        artist = entry["artist"]
        for album_name, year, genre, song_titles in entry["albums"]:
            codec, bitrate, sample_rate, bit_depth = random.choice(_CODECS)
            ext = codec if codec != "aac" else "m4a"

            for i, title in enumerate(song_titles, start=1):
                duration = random.randint(120, 380)
                file_size = duration * (bitrate * 125 if bitrate else sample_rate * bit_depth * 2 // 8)

                # Build file path
                safe_artist = artist.replace(" ", "_")
                safe_album = album_name.replace(" ", "_") if album_name != "Unknown Album" else "Unknown"
                file_name = f"{i:02d}. {title}.{ext}"
                file_path = f"{safe_artist}/{safe_album}/{file_name}"

                track = TrackMetadata(
                    file_path=file_path,
                    file_name=file_name,
                    title=title,
                    artist=artist if artist != "Unknown Artist" else None,
                    album=album_name if album_name != "Unknown Album" else None,
                    album_artist=artist if artist != "Unknown Artist" else None,
                    genre=genre,
                    year=year,
                    track_number=i,
                    disc_number=1,
                    duration_seconds=duration,
                    codec=codec,
                    bitrate_kbps=bitrate if bitrate else (sample_rate * bit_depth * 2 // 1000),
                    sample_rate_hz=sample_rate,
                    bit_depth=bit_depth,
                    channels=2,
                    file_size_bytes=file_size,
                    has_album_art=random.random() > 0.15,
                    has_lyrics=random.random() > 0.6,
                )

                # Intentionally blank some metadata for ~15% of tracks
                if random.random() < 0.08:
                    track.title = None
                if random.random() < 0.06:
                    track.artist = None
                if random.random() < 0.06:
                    track.album = None

                tracks.append(track)

    # If we haven't reached the target count, duplicate with variation
    while len(tracks) < count:
        source = random.choice(tracks[:len(tracks) // 2])
        variant = TrackMetadata(
            file_path=source.file_path.replace(".", f"_copy{len(tracks)}.", 1),
            file_name=source.file_name,
            title=source.title,
            artist=source.artist,
            album=source.album,
            album_artist=source.album_artist,
            genre=source.genre,
            year=source.year,
            track_number=source.track_number,
            disc_number=source.disc_number,
            duration_seconds=source.duration_seconds + random.randint(-20, 20),
            codec=source.codec,
            bitrate_kbps=source.bitrate_kbps,
            sample_rate_hz=source.sample_rate_hz,
            bit_depth=source.bit_depth,
            channels=source.channels,
            file_size_bytes=source.file_size_bytes,
            has_album_art=source.has_album_art,
            has_lyrics=source.has_lyrics,
        )
        tracks.append(variant)

    return tracks[:count]
