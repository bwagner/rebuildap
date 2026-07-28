"""Tests for label-line normalization and the import path that feeds Nyquist.

Background: ``ImportLabels.ny`` (the non-interactive import path) requires each
line to have exactly three tab-separated fields (two tabs), ``start end text``.
Real label files in the corpus come in three shapes -- 1-column beat times,
2-column ``time<TAB>beatnumber``, and 3-column ``start<TAB>end<TAB>text`` -- and
the first two crash the plugin with a bare ``BatchCommand finished: Failed!``.

``normalize_label_line`` maps every known shape onto the two-tab canonical form
that both the importer and check mode's ``process_lines`` share. The import path
writes the normalized content to a throwaway temp file and never touches the
versioned source.
"""

from pathlib import Path

import pytest

from rebuildap import audacity_funcs as af
from rebuildap.utils import LabelFormatError, normalize_label_line

# --- the pure line normalizer ------------------------------------------------

# Canonical target: "start<TAB>end<TAB>text\n" -- always two tabs, text may be
# empty. Trailing zeros are cut so 6-decimal exports and 3-decimal sources agree.
# Times run through cut_trailing_zeros, so 0.470 -> 0.47 (numerically identical,
# and the same canonical form check mode compares against).
NORMALIZE_CASES = [
    # 1-column beat time -> point label, empty text
    ("0.470", "0.47\t0.47\t\n"),
    # 2-column time + beat number -> field 2 is the label TEXT (rule A), even
    # though "4" is numeric. DBNDownBeatTracker emits time<TAB>beatnumber.
    ("0.470\t4", "0.47\t0.47\t4\n"),
    # 2-column with a decimal field 2 is STILL text, not an end time (rule A),
    # and text is verbatim -- only *times* get trailing zeros trimmed.
    ("1.250\t3.500", "1.25\t1.25\t3.500\n"),
    # 3-column canonical -> unchanged apart from trailing-zero trim
    ("1.00\t2.00\tbar", "1\t2\tbar\n"),
    # already-two-tab empty text passes through
    ("0.470\t0.470\t", "0.47\t0.47\t\n"),
]


@pytest.mark.parametrize("raw, expected", NORMALIZE_CASES)
def test_normalize_label_line_maps_every_shape(raw, expected):
    assert normalize_label_line(raw) == expected


@pytest.mark.parametrize("raw, expected", NORMALIZE_CASES)
def test_normalized_line_has_exactly_two_tabs(raw, expected):
    # The whole point: two tabs is what ImportLabels.ny can parse.
    assert normalize_label_line(raw).count("\t") == 2


@pytest.mark.parametrize(
    "raw",
    [
        "hello\tworld",  # first field not a float
        "5.5\t6.6\t7.7\tignored",  # four fields
        "",  # blank
        "   ",  # whitespace only
    ],
)
def test_normalize_label_line_returns_none_for_unparseable(raw):
    assert normalize_label_line(raw) is None


def test_normalize_label_line_tolerates_trailing_newline():
    # Input from splitlines(keepends=True) carries the newline.
    assert normalize_label_line("0.470\n") == "0.47\t0.47\t\n"


def test_one_column_source_matches_empty_text_export():
    # A 1-column beat time and the exporter's empty-text point label
    # (start<TAB>end<TAB>) must normalize to the SAME thing, or check mode
    # would flag every 1-column beats project as permanently divergent.
    # The trailing tab must survive normalization for this to hold.
    source_line = normalize_label_line("0.480")
    export_line = normalize_label_line("0.480000\t0.480000\t")
    assert source_line == export_line == "0.48\t0.48\t\n"


# --- the file-level import normalizer + guard --------------------------------


def test_normalize_file_converts_single_column(tmp_path):
    src = tmp_path / "beats_song.txt"
    src.write_text("0.470\n1.120\n1.760\n")
    out = af.normalize_label_file_for_import(src)
    assert out == "0.47\t0.47\t\n1.12\t1.12\t\n1.76\t1.76\t\n"


def test_normalize_file_converts_two_column(tmp_path):
    src = tmp_path / "beats_song.txt"
    src.write_text("0.470\t4\n1.120\t1\n")
    out = af.normalize_label_file_for_import(src)
    assert out == "0.47\t0.47\t4\n1.12\t1.12\t1\n"


def test_normalize_file_leaves_source_on_disk_untouched(tmp_path):
    src = tmp_path / "beats_song.txt"
    original = "0.470\n1.120\n"
    src.write_text(original)
    af.normalize_label_file_for_import(src)
    assert src.read_text() == original


def test_normalize_file_raises_naming_file_and_line_on_bad_content(tmp_path):
    src = tmp_path / "beats_song.txt"
    src.write_text("0.470\n=== not a label ===\n1.760\n")
    with pytest.raises(LabelFormatError) as excinfo:
        af.normalize_label_file_for_import(src)
    msg = str(excinfo.value)
    assert "beats_song.txt" in msg
    assert "2" in msg  # the offending line number
    assert "not a label" in msg


# --- make_label_track_from_file feeds the normalized temp, not the source ----


def _capture_import(monkeypatch):
    """Stub the live Audacity calls; capture the content actually handed to
    ImportLabels and the fname it was read from."""
    captured = {}

    def fake_do(command):
        if command.startswith("ImportLabels:"):
            fname = command.split('fname="', 1)[1].rsplit('"', 1)[0]
            captured["fname"] = fname
            captured["content"] = Path(fname).read_text()
        return "OK"

    from contextlib import nullcontext

    monkeypatch.setattr(af.pa, "do", fake_do)
    monkeypatch.setattr(af, "save_selection", lambda: nullcontext())
    monkeypatch.setattr(af, "select_first_audio_track", lambda: None)
    monkeypatch.setattr(af, "get_track_count", lambda: 5)
    return captured


def test_import_feeds_normalized_content_not_the_raw_source(tmp_path, monkeypatch):
    captured = _capture_import(monkeypatch)
    src = tmp_path / "beats_song.txt"
    src.write_text("0.470\n1.120\n")

    af.make_label_track_from_file(src, "beats")

    assert captured["content"] == "0.47\t0.47\t\n1.12\t1.12\t\n"
    # It imported a temp file, not the versioned source.
    assert Path(captured["fname"]) != src


def test_import_removes_its_temp_file(tmp_path, monkeypatch):
    captured = _capture_import(monkeypatch)
    src = tmp_path / "beats_song.txt"
    src.write_text("0.470\n1.120\n")

    af.make_label_track_from_file(src, "beats")

    assert not Path(captured["fname"]).exists()


# --- pre-flight validation fails before Audacity is ever started -------------


def test_assert_label_files_importable_passes_on_good_files(tmp_path):
    audio = tmp_path / "song.opus"
    audio.touch()
    (tmp_path / "beats_song.txt").write_text("0.470\n1.120\n")
    (tmp_path / "parts_song.txt").write_text("0.0\t5.0\tintro\n")
    # No exception: all label files normalize cleanly.
    af.assert_label_files_importable(audio)


def test_assert_label_files_importable_raises_on_bad_file(tmp_path):
    audio = tmp_path / "song.opus"
    audio.touch()
    (tmp_path / "beats_song.txt").write_text("0.470\n=== junk ===\n")
    with pytest.raises(LabelFormatError) as excinfo:
        af.assert_label_files_importable(audio)
    assert "beats_song.txt" in str(excinfo.value)


def test_rebuild_bails_before_starting_audacity_on_bad_label(tmp_path, monkeypatch):
    audio = tmp_path / "song.opus"
    audio.write_bytes(b"not really audio")
    (tmp_path / "beats_song.txt").write_text("0.470\n=== junk ===\n")

    started = []
    monkeypatch.setattr(
        "rebuildap.ap.assert_audacity", lambda *a, **k: started.append(1)
    )
    monkeypatch.setattr(af, "is_audacity_project", lambda _f: False)
    monkeypatch.setattr(af, "aup3_path_for", lambda f: tmp_path / "song.aup3")

    import rebuildap as ra

    with pytest.raises(SystemExit) as excinfo:
        ra._build_project(str(audio), verbose=False, save=True)
    # Clean message naming the file, and Audacity was never started.
    assert "beats_song.txt" in str(excinfo.value)
    assert started == []
