"""Offline tests for the ``-q`` quantize-in-place mode.

``rebuildap -q [BEATS_TRACK]`` quantizes the *selected* label track in the open
Audacity project to the grid of a *beats* label track already in that project,
re-imports the result at the original track's position, and re-exports the
versioned ``.txt``. Everything the mode decides -- which track is the target,
which is the reference, the track-move arithmetic that restores position, the
subprocess argv, and the CLI usage guards -- is pure logic and tested here.
Only the live pipe round-trip is left to the ``audacity``-marked suite.
"""

import subprocess
from pathlib import Path

import pytest

import rebuildap
from rebuildap import audacity_funcs as af


def _track(name, kind="label", selected=False):
    return {"name": name, "kind": kind, "selected": 1 if selected else 0}


# --- track-move arithmetic (restoring the original position) ----------------


def test_move_up_when_target_is_above_current():
    # A freshly imported track sits at the bottom (index 4); the track it
    # replaces was at index 1, so it must rise three rows.
    assert af._track_move_commands(4, 1) == [af.CMD_TRACK_MOVE_UP] * 3


def test_move_down_when_target_is_below_current():
    assert af._track_move_commands(1, 4) == [af.CMD_TRACK_MOVE_DOWN] * 3


def test_no_move_when_already_in_place():
    assert af._track_move_commands(3, 3) == []


# --- resolving target + reference from the track list -----------------------


def test_resolves_selected_target_and_named_reference():
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    target_i, target_name, ref_i, ref_name = af.resolve_quantize_targets(
        tracks, reference_name="beats"
    )
    assert (target_i, target_name) == (1, "chords")
    assert (ref_i, ref_name) == (2, "beats")


def test_autodetects_the_sole_beats_track_when_no_name_given():
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    _, _, ref_i, ref_name = af.resolve_quantize_targets(tracks, reference_name=None)
    assert (ref_i, ref_name) == (2, "beats")


def test_no_selected_label_track_is_an_error():
    tracks = [_track("song", kind="wave"), _track("beats")]
    with pytest.raises(af.QuantizeError, match="[Ss]elect"):
        af.resolve_quantize_targets(tracks, reference_name="beats")


def test_multiple_selected_label_tracks_is_an_error():
    tracks = [
        _track("chords", selected=True),
        _track("parts", selected=True),
        _track("beats"),
    ]
    with pytest.raises(af.QuantizeError, match="chords|parts"):
        af.resolve_quantize_targets(tracks, reference_name="beats")


def test_named_reference_absent_is_an_error():
    tracks = [_track("chords", selected=True), _track("beats")]
    with pytest.raises(af.QuantizeError, match="bars"):
        af.resolve_quantize_targets(tracks, reference_name="bars")


def test_autodetect_with_no_beats_track_is_an_error():
    tracks = [_track("chords", selected=True), _track("parts")]
    with pytest.raises(af.QuantizeError, match="beats|-q"):
        af.resolve_quantize_targets(tracks, reference_name=None)


def test_autodetect_with_multiple_beats_tracks_is_an_error():
    tracks = [
        _track("chords", selected=True),
        _track("beats"),
        _track("beats_alt"),
    ]
    with pytest.raises(af.QuantizeError, match="[Mm]ultiple|-q"):
        af.resolve_quantize_targets(tracks, reference_name=None)


def test_reference_cannot_be_the_target_itself():
    # The selected track *is* the only beats track -> nothing to quantize against.
    tracks = [_track("song", kind="wave"), _track("beats", selected=True)]
    with pytest.raises(af.QuantizeError, match="reference"):
        af.resolve_quantize_targets(tracks, reference_name=None)


# --- locating and invoking the sister quantize_labels.py script -------------


def test_quantize_command_quantizes_target_in_place_against_reference(tmp_path):
    script = tmp_path / "quantize_labels.py"
    ref = tmp_path / "ref.txt"
    target = tmp_path / "target.txt"
    argv = af.quantize_command(script, ref, target)
    # Runs the script directly (uv shebang); -i makes quantize_labels rewrite
    # the *target* (its second positional) to the *reference* grid (the first).
    assert argv == [str(script), "-i", str(ref), str(target)]


def test_locate_script_finds_it_on_path(monkeypatch, tmp_path):
    import shutil

    script = tmp_path / "quantize_labels.py"
    script.write_text("#!/usr/bin/env python\n")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(script) if name == "quantize_labels.py" else None,
    )
    assert af.locate_quantize_script() == script


def test_locate_script_accepts_the_extensionless_name(monkeypatch, tmp_path):
    import shutil

    script = tmp_path / "quantize_labels"
    monkeypatch.setattr(
        shutil, "which", lambda name: str(script) if name == "quantize_labels" else None
    )
    assert af.locate_quantize_script() == script


def test_locate_script_errors_clearly_when_not_on_path(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(af.QuantizeError) as excinfo:
        af.locate_quantize_script()
    # Names $PATH and points at where to get the script.
    assert "PATH" in str(excinfo.value)
    assert af.QUANTIZE_SCRIPT_URL in str(excinfo.value)


# --- orchestration order: remove old, import new, then reposition -----------


def test_quantize_swaps_track_in_the_safe_order(monkeypatch, tmp_path):
    """The dangerous invariant: remove the old track, import the quantized one,
    then move it back to the old index -- in that order."""
    events = []

    tracks_before = [
        _track("song", kind="wave"),
        _track("chords", selected=True),  # target at index 1
        _track("beats"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks_before)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"chords": "0.0\t0.0\ta\n", "beats": "0.1\t0.1\t\n"},
    )
    script = tmp_path / "quantize_labels.py"
    script.write_text("#!/usr/bin/env python\n")
    monkeypatch.setattr(af, "locate_quantize_script", lambda: script)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: events.append("quantize") or None
    )
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: events.append("remove"))
    monkeypatch.setattr(
        af,
        "make_label_track_from_file",
        lambda path, name=None: events.append(f"import:{name}"),
    )
    # After removal (3 -> 2 tracks) the re-import appends at the bottom (index 2).
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(
        af, "move_track_to", lambda frm, to: events.append(f"move:{frm}->{to}")
    )
    monkeypatch.setattr(af, "select_tracks", lambda idx: events.append(f"select:{idx}"))

    target_name, target_index, stem, content = af.quantize_selected_label_track(
        reference_name=None
    )

    assert target_name == "chords"
    assert target_index == 1
    assert stem == "song"
    # The quantized content is handed back canonicalized (6-decimal), from the
    # same bytes that were imported -- no read-back export from Audacity.
    assert content == af._format_track_txt([(0.0, 0.0, "a")])
    assert events == [
        "quantize",
        "remove",
        "import:chords",
        "move:2->1",
        "select:[1]",
    ]


# --- canonicalizing the quantized result for the versioned .txt -------------


def test_labels_from_txt_parses_three_field_lines_including_empty_text():
    parsed = af._labels_from_txt("0.0\t1.0\tverse\n2.0\t2.0\t\n")
    assert parsed == [(0.0, 1.0, "verse"), (2.0, 2.0, "")]


def test_labels_from_txt_skips_blank_lines():
    assert af._labels_from_txt("\n0.5\t0.5\tx\n\n") == [(0.5, 0.5, "x")]


def test_quantized_content_is_canonical_six_decimal(monkeypatch, tmp_path):
    """quantize_labels emits bare floats; the versioned .txt must come back in
    the same 6-decimal form every other export uses, so files stay uniform."""
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"chords": "x", "beats": "y"},
    )
    script = tmp_path / "quantize_labels.py"
    monkeypatch.setattr(af, "locate_quantize_script", lambda: script)

    # Fake the shell-out: write bare-float quantized output to the target temp
    # (its path is the last positional of the argv).
    def fake_run(argv, **kwargs):
        Path(argv[-1]).write_text("0.0\t1.0\tverse\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: None)
    monkeypatch.setattr(af, "make_label_track_from_file", lambda *a, **k: None)
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(af, "move_track_to", lambda *a: None)
    monkeypatch.setattr(af, "select_tracks", lambda *a: None)

    _, _, _, content = af.quantize_selected_label_track(reference_name="beats")
    assert content == "0.000000\t1.000000\tverse\n"


# --- persisting the quantized track to the versioned .txt -------------------


def _stub_quantize(monkeypatch, stem="song", name="chords", content="0.0\t1.0\tv\n"):
    import rebuildap as rb

    monkeypatch.setattr(rb, "prerequisites_met", lambda _v: True)
    monkeypatch.setattr(
        af,
        "quantize_selected_label_track",
        lambda ref, verbose: (name, 1, stem, content),
    )


def test_quantize_writes_the_versioned_txt_in_the_project_dir(
    monkeypatch, tmp_path, capsys
):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")  # cwd holds the project
    _stub_quantize(monkeypatch, stem="song", name="chords", content="0.0\t1.0\tv\n")

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    written = tmp_path / "chords_song.txt"
    assert written.read_text() == "0.0\t1.0\tv\n"
    assert str(written) in capsys.readouterr().out


def test_quantize_does_not_write_when_cwd_is_not_the_project_dir(
    monkeypatch, tmp_path, capsys
):
    """The in-project swap still happened, but with no .txt written the run says
    so instead of silently dropping it."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)  # empty: not the project dir
    _stub_quantize(monkeypatch, stem="song", name="chords")
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [Path("/elsewhere")])

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    assert not (tmp_path / "chords_song.txt").exists()
    out = capsys.readouterr().out
    assert "did not" in out.lower()


# --- CLI parsing and usage guards -------------------------------------------


def _captured_rebuild(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        rebuildap, "rebuild", lambda *a, **k: captured.update(kwargs=k, args=a)
    )
    return captured


def test_bare_q_flag_requests_autodetect(monkeypatch):
    import sys as _sys

    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr(_sys, "argv", ["rebuildap", "-q"])
    rebuildap.main()
    assert captured["kwargs"]["quantize"] is rebuildap._QUANTIZE_AUTODETECT


def test_q_flag_with_a_name_carries_the_track_name(monkeypatch):
    import sys as _sys

    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr(_sys, "argv", ["rebuildap", "-q", "beats"])
    rebuildap.main()
    assert captured["kwargs"]["quantize"] == "beats"


def test_no_q_flag_leaves_quantize_off(monkeypatch):
    import sys as _sys

    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr(_sys, "argv", ["rebuildap"])
    rebuildap.main()
    assert captured["kwargs"]["quantize"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["rebuildap", "song.opus", "-q"],
        ["rebuildap", "-q", "-c"],
        ["rebuildap", "-q", "-l"],
    ],
)
def test_quantize_rejects_a_filename_check_or_label(monkeypatch, argv):
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        rebuildap.main()
    assert excinfo.value.code != 0
