"""Offline tests for exporting label tracks at their exact times.

``export`` wrote label files from GetInfo, which rounds times to six significant
digits: a chord ``quantize`` had put exactly on a beat at 100.68907563025209 was
exported as 100.689000, undoing in the versioned file what quantize had written
there. Export now reads the ``.aup3`` like quantize does (see
``read_label_tracks_precisely``) and writes 6 decimals of the exact times.
"""

import json
from pathlib import Path

from test_save_and_paths import _stub_open_project

import rebuildap
from rebuildap import audacity_funcs as af

BEAT = 100.68907563025209
TRACKS = [
    {"name": "song", "kind": "wave"},
    {"name": "parts", "kind": "label"},
    {"name": "chords", "kind": "label"},
]


def _world(monkeypatch, labels, precise=True):
    """GetInfo reports TRACKS; the label read returns ``labels`` (by name) and
    records the project file it was handed in the returned dict."""
    seen = {}
    monkeypatch.setattr(
        af.pa,
        "do",
        lambda cmd: json.dumps(TRACKS) + "\nBatchCommand finished: OK\n",
    )

    def read(source):
        seen["source"] = source
        return labels, precise

    monkeypatch.setattr(af, "read_label_tracks_precisely", read)
    return seen


def test_export_writes_six_decimals_of_the_exact_times(tmp_path, monkeypatch):
    _world(monkeypatch, {"parts": [], "chords": [(BEAT, BEAT, "C")]})

    af._write_via_getinfo([2], aup3_path=tmp_path / "song.aup3")

    assert (tmp_path / "chords_song.txt").read_text() == "100.689076\t100.689076\tC\n"


def test_export_reads_the_project_file_it_is_given(tmp_path, monkeypatch):
    seen = _world(monkeypatch, {"parts": [], "chords": [(BEAT, BEAT, "C")]})
    project = tmp_path / "song.aup3"

    af.export_label_tracks_via_getinfo(project, source=project)

    assert seen["source"] == project


def test_only_the_requested_tracks_are_written(tmp_path, monkeypatch):
    _world(monkeypatch, {"parts": [(1.0, 2.0, "intro")], "chords": [(BEAT, BEAT, "C")]})

    written = af._write_via_getinfo([2], aup3_path=tmp_path / "song.aup3")

    assert written == [("chords", tmp_path / "chords_song.txt")]
    assert not (tmp_path / "parts_song.txt").exists()


def test_exporting_a_named_project_reads_that_file(tmp_path, monkeypatch):
    aup3 = tmp_path / "song.aup3"
    aup3.write_bytes(b"x")
    monkeypatch.setattr(rebuildap, "_open_in_audacity", lambda path, verbose: None)
    calls = []
    monkeypatch.setattr(
        af,
        "export_label_tracks_via_getinfo",
        lambda *a, **k: calls.append((a, k)) or [],
    )

    rebuildap._export_labels(str(aup3))

    assert calls and calls[0][1]["source"] == aup3


def _no_arg_export(tmp_path, monkeypatch, selected, open_paths):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_bytes(b"x")
    _stub_open_project(monkeypatch, "song", selected, open_paths=open_paths)
    calls = []
    for name in (
        "export_selected_label_tracks_via_getinfo",
        "export_label_tracks_via_getinfo",
    ):
        monkeypatch.setattr(
            af, name, lambda *a, _n=name, **k: calls.append((_n, k)) or []
        )
    rebuildap._export_labels(verbose=False)
    return calls


def test_a_selected_track_export_reads_the_open_project_file(tmp_path, monkeypatch):
    open_file = Path("/Volumes/SSD/song/song.aup3")

    calls = _no_arg_export(tmp_path, monkeypatch, [2], [open_file, open_file])

    assert calls == [
        (
            "export_selected_label_tracks_via_getinfo",
            {"stem": "song", "source": open_file},
        )
    ]


def test_an_all_tracks_export_reads_the_open_project_file(tmp_path, monkeypatch):
    open_file = Path("/Volumes/SSD/song/song.aup3")

    calls = _no_arg_export(tmp_path, monkeypatch, [], [open_file])

    assert calls == [
        ("export_label_tracks_via_getinfo", {"stem": "song", "source": open_file})
    ]


# --- when the read fell back to GetInfo's rounding ------------------------------


def test_a_fallback_export_keeps_the_files_exact_times(tmp_path, monkeypatch):
    """GetInfo showed 100.689; the file knows 100.689076 and keeps it."""
    _world(
        monkeypatch, {"parts": [], "chords": [(100.689, 100.689, "C")]}, precise=False
    )
    out = tmp_path / "chords_song.txt"
    out.write_text("100.689076\t100.689076\tC\n")

    af._write_via_getinfo([2], aup3_path=tmp_path / "song.aup3")

    assert out.read_text() == "100.689076\t100.689076\tC\n"


def test_a_fallback_export_writes_getinfos_times_when_the_file_does_not_match(
    tmp_path, monkeypatch
):
    _world(
        monkeypatch, {"parts": [], "chords": [(100.689, 100.689, "C")]}, precise=False
    )
    out = tmp_path / "chords_song.txt"
    out.write_text("100.5\t100.5\tC\n")

    af._write_via_getinfo([2], aup3_path=tmp_path / "song.aup3")

    assert out.read_text() == "100.689000\t100.689000\tC\n"


def test_an_exact_export_writes_its_own_times(tmp_path, monkeypatch):
    """With an exact read the project is the truth, even where the file differs
    only below GetInfo's rounding (both show as 100.689)."""
    _world(monkeypatch, {"parts": [], "chords": [(BEAT, BEAT, "C")]}, precise=True)
    out = tmp_path / "chords_song.txt"
    out.write_text("100.689040\t100.689040\tC\n")

    af._write_via_getinfo([2], aup3_path=tmp_path / "song.aup3")

    assert out.read_text() == "100.689076\t100.689076\tC\n"
