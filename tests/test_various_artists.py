import unittest

from src.models.drive_data import TrackMetadata
from src.utils.various_artists import (
    FIELDS_ALBUM_ARTIST,
    FIELDS_ARTIST,
    FIELDS_BOTH,
    is_various_artist,
    tags_to_normalise,
)


def make_track(artist="Unknown Artist", album_artist="", extension=".mp3") -> TrackMetadata:
    return TrackMetadata(
        filepath=f"/music/song{extension}",
        filename=f"song{extension}",
        extension=extension,
        size_bytes=1,
        artist=artist,
        album_artist=album_artist,
    )


class IsVariousArtistTests(unittest.TestCase):
    def test_matches_known_variants(self):
        for value in ("Various", "Various Artists", "Multiple Artists", "VA", "V.A.", "  various   ARTISTS "):
            self.assertTrue(is_various_artist(value), value)

    def test_rejects_real_artists(self):
        for value in ("Various Artists feat. Bob", "Variousity", "Daft Punk", "", None):
            self.assertFalse(is_various_artist(value), value)


class TagsToNormaliseTests(unittest.TestCase):
    def test_both_fields(self):
        track = make_track(artist="various", album_artist="Multiple Artists")
        self.assertEqual(
            tags_to_normalise(track, FIELDS_BOTH, "Various Artists"),
            {"artist": "Various Artists", "albumartist": "Various Artists"},
        )

    def test_only_matching_field_changes(self):
        track = make_track(artist="Daft Punk", album_artist="VA")
        self.assertEqual(
            tags_to_normalise(track, FIELDS_BOTH, "Various Artists"),
            {"albumartist": "Various Artists"},
        )

    def test_field_selection_limits_scope(self):
        track = make_track(artist="Various", album_artist="Various")
        self.assertEqual(tags_to_normalise(track, FIELDS_ARTIST, "VA"), {"artist": "VA"})
        self.assertEqual(tags_to_normalise(track, FIELDS_ALBUM_ARTIST, "VA"), {"albumartist": "VA"})

    def test_exact_target_is_skipped_but_other_casing_is_not(self):
        self.assertEqual(tags_to_normalise(make_track(artist="Various Artists"), FIELDS_ARTIST, "Various Artists"), {})
        self.assertEqual(
            tags_to_normalise(make_track(artist="various artists"), FIELDS_ARTIST, "Various Artists"),
            {"artist": "Various Artists"},
        )

    def test_non_audio_files_are_ignored(self):
        self.assertEqual(tags_to_normalise(make_track(artist="Various", extension=".lrc"), FIELDS_BOTH, "X"), {})


if __name__ == "__main__":
    unittest.main()
