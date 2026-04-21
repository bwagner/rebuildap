"""Unit tests for the pure helpers backing export-labels-via-GetInfo.

These helpers operate only on strings and Python data; they do not talk to
Audacity. Integration functions (those that actually call ``pa.do``) live in
``rebuildap.audacity_funcs`` and are not exercised here.
"""

from rebuildap import _maybe_write_divergent_export
from rebuildap.audacity_funcs import (
    _derive_label_filename,
    _format_label_line,
    _format_track_txt,
    _label_track_names_by_idx,
    _parse_labels_response,
    _parse_tracks_response,
    _strip_ok_marker,
)


class TestStripOkMarker:
    def test_correct_spelling(self):
        raw = "payload\nBatchCommand finished: OK\n"
        assert _strip_ok_marker(raw) == "payload"

    def test_typo_spelling(self):
        raw = "payload\nBatchCommand finshed: OK\n"
        assert _strip_ok_marker(raw) == "payload"

    def test_no_marker_returns_raw(self):
        raw = "payload without marker"
        assert _strip_ok_marker(raw) == raw

    def test_keeps_leading_newline_inside_payload(self):
        # GetInfo responses start with '\n[ ...]'
        raw = "\n[1,2,3]\nBatchCommand finished: OK\n"
        assert _strip_ok_marker(raw) == "\n[1,2,3]"


class TestParseLabelsResponse:
    def test_single_track_single_label(self):
        raw = '\n[ [1, [[0.83, 9.79, "intro"]]] ]\nBatchCommand finished: OK\n'
        result = _parse_labels_response(raw)
        assert result == {1: [(0.83, 9.79, "intro")]}

    def test_multiple_tracks(self):
        raw = (
            '\n[ [1, [[0.83, 9.79, "intro"]]],\n'
            '  [2, [[1.35, 2.83, "C7"]]] ]\nBatchCommand finished: OK\n'
        )
        result = _parse_labels_response(raw)
        assert result == {
            1: [(0.83, 9.79, "intro")],
            2: [(1.35, 2.83, "C7")],
        }

    def test_empty_label_text_preserved(self):
        raw = '\n[ [1, [[0.3, 0.3, ""]]] ]\nBatchCommand finished: OK\n'
        result = _parse_labels_response(raw)
        assert result == {1: [(0.3, 0.3, "")]}

    def test_integer_valued_times_become_floats(self):
        # JSON trims trailing zeros: 92.0 → 92. Our dict should still be floats.
        raw = '\n[ [1, [[92, 94, "x"]]] ]\nBatchCommand finished: OK\n'
        result = _parse_labels_response(raw)
        assert result == {1: [(92.0, 94.0, "x")]}
        assert isinstance(result[1][0][0], float)


class TestParseTracksResponse:
    def test_mixed_audio_and_label(self):
        raw = (
            '\n[ { "name":"song", "kind":"wave", "start":0, "end":100 },\n'
            '  { "name":"chords", "kind":"label" } ]\n'
            "BatchCommand finished: OK\n"
        )
        result = _parse_tracks_response(raw)
        assert len(result) == 2
        assert result[0]["kind"] == "wave"
        assert result[1]["kind"] == "label"
        assert result[1]["name"] == "chords"


class TestLabelTrackNamesByIdx:
    def test_label_only_get_0_based_indices(self):
        # The GetInfo Tracks response indexes tracks 0-based; label tracks are
        # a subset of those indices. The helper returns the mapping from
        # original 0-based track index → name, filtered to kind='label'.
        tracks = [
            {"name": "song", "kind": "wave"},
            {"name": "parts", "kind": "label"},
            {"name": "chords", "kind": "label"},
            {"name": "bars", "kind": "label"},
        ]
        assert _label_track_names_by_idx(tracks) == {1: "parts", 2: "chords", 3: "bars"}


class TestFormatLabelLine:
    def test_region_label(self):
        assert _format_label_line(0.83, 9.79, "intro") == "0.830000\t9.790000\tintro\n"

    def test_point_label_empty_text(self):
        # Audacity's ExportLabels: keeps the trailing tab even for empty text.
        assert _format_label_line(125.69, 125.69, "") == "125.690000\t125.690000\t\n"

    def test_six_decimal_precision(self):
        assert (
            _format_label_line(125.437458, 126.671804, "C7#9")
            == "125.437458\t126.671804\tC7#9\n"
        )

    def test_integer_value_pads_to_six_decimals(self):
        assert _format_label_line(92, 94, "x") == "92.000000\t94.000000\tx\n"


class TestFormatTrackTxt:
    def test_multiple_labels_concatenated(self):
        labels = [(0.83, 9.79, "intro"), (64.44, 92.0, "bridge")]
        assert _format_track_txt(labels) == (
            "0.830000\t9.790000\tintro\n64.440000\t92.000000\tbridge\n"
        )

    def test_empty_track(self):
        assert _format_track_txt([]) == ""


class TestDeriveLabelFilename:
    def test_standard(self):
        assert (
            _derive_label_filename("chords", "blues_brothers_20_respect")
            == "chords_blues_brothers_20_respect.txt"
        )


class TestMaybeWriteDivergentExport:
    def test_writes_when_contents_differ(self, tmp_path):
        versioned = tmp_path / "chords_song.txt"
        versioned.write_text("0.830000\t9.790000\tintro\n")
        fresh = "0.83\t9.79\tintro_changed\n"
        out_path = _maybe_write_divergent_export(
            expected_content=fresh,
            label_file=versioned,
            short_name="chords",
            out_dir=tmp_path,
        )
        assert out_path == tmp_path / "chords.txt"
        assert out_path.read_text() == fresh

    def test_no_write_when_normalized_identical(self, tmp_path):
        # 6-decimal on disk vs 3-decimal from GetInfo — but process_lines
        # normalizes both to the same cut-trailing-zeros form.
        versioned = tmp_path / "chords_song.txt"
        versioned.write_text("0.830000\t9.790000\tintro\n")
        fresh = "0.83\t9.79\tintro\n"
        out_path = _maybe_write_divergent_export(
            expected_content=fresh,
            label_file=versioned,
            short_name="chords",
            out_dir=tmp_path,
        )
        assert out_path is None
        assert not (tmp_path / "chords.txt").exists()

    def test_precision_difference_that_actually_differs_numerically(self, tmp_path):
        # If the normalized values really differ (e.g. 6th-decimal truncation
        # on a sub-beat label), it counts as a real diff and must be written.
        versioned = tmp_path / "guit_song.txt"
        versioned.write_text("125.437458\t126.671804\tC7#9\n")
        fresh = "125.437\t126.672\tC7#9\n"
        out_path = _maybe_write_divergent_export(
            expected_content=fresh,
            label_file=versioned,
            short_name="guit",
            out_dir=tmp_path,
        )
        assert out_path == tmp_path / "guit.txt"
        assert out_path.read_text() == fresh
