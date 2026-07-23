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
import types
from pathlib import Path

import pytest

import rebuildap
from rebuildap import audacity_funcs as af


def _run_result(stderr="", stdout=""):
    """Stand-in for the CompletedProcess quantize_selected_label_track reads."""
    return types.SimpleNamespace(stderr=stderr, stdout=stdout)


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
        # 'chords' currently off the grid at 0.12; quantizing will move it.
        lambda: {
            "chords": af._format_track_txt([(0.12, 0.12, "a")]),
            "beats": af._format_track_txt([(0.0, 0.0, "")]),
        },
    )
    script = tmp_path / "quantize_labels.py"
    script.write_text("#!/usr/bin/env python\n")
    monkeypatch.setattr(af, "locate_quantize_script", lambda: script)

    def fake_run(argv, **k):
        events.append("quantize")
        Path(argv[-1]).write_text("0.0\t0.0\ta\n")  # snapped 0.12 -> 0.0
        return _run_result()

    monkeypatch.setattr(subprocess, "run", fake_run)
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

    target_name, target_index, stem, content, changed = (
        af.quantize_selected_label_track(reference_name=None)
    )

    assert (target_name, target_index, stem, changed) == ("chords", 1, "song", True)
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


def test_quantize_skips_the_swap_when_already_quantized(monkeypatch, tmp_path):
    """An already-on-grid track: no remove/import/move, project left untouched,
    and changed is False."""
    events = []
    current = af._format_track_txt([(0.0, 1.0, "a")])  # already on the grid
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"chords": current, "beats": af._format_track_txt([(0.0, 0.0, "")])},
    )
    monkeypatch.setattr(af, "locate_quantize_script", lambda: tmp_path / "q.py")
    # quantize reports "no change": leaves the target temp exactly as written.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _run_result())
    for name in ("remove_selected_tracks",):
        monkeypatch.setattr(af, name, lambda: events.append("remove"))
    monkeypatch.setattr(
        af, "make_label_track_from_file", lambda *a, **k: events.append("import")
    )
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(af, "move_track_to", lambda *a: events.append("move"))
    monkeypatch.setattr(af, "select_tracks", lambda *a: events.append("select"))

    _, _, _, content, changed = af.quantize_selected_label_track(reference_name="beats")

    assert changed is False
    assert events == [], "already-quantized track must not touch the project"
    assert content == current


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
        return _run_result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: None)
    monkeypatch.setattr(af, "make_label_track_from_file", lambda *a, **k: None)
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(af, "move_track_to", lambda *a: None)
    monkeypatch.setattr(af, "select_tracks", lambda *a: None)

    _, _, _, content, _ = af.quantize_selected_label_track(reference_name="beats")
    assert content == "0.000000\t1.000000\tverse\n"


# --- quantize_labels output is captured, surfaced only under -v / on failure -


def _stub_orchestrator_env(monkeypatch, tmp_path, run):
    """Fake everything quantize_selected_label_track touches except ``run``."""
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)
    monkeypatch.setattr(
        af,
        "get_label_tracks_content_via_getinfo",
        lambda: {"chords": "0.0\t0.0\ta\n", "beats": "0.1\t0.1\t\n"},
    )
    monkeypatch.setattr(af, "locate_quantize_script", lambda: tmp_path / "q.py")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: None)
    monkeypatch.setattr(af, "make_label_track_from_file", lambda *a, **k: None)
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(af, "move_track_to", lambda *a: None)
    monkeypatch.setattr(af, "select_tracks", lambda *a: None)


def test_quantize_labels_summary_is_hidden_without_verbose(
    monkeypatch, tmp_path, capsys
):
    _stub_orchestrator_env(
        monkeypatch,
        tmp_path,
        lambda *a, **k: _run_result(stderr="Total adjustment: 0\n"),
    )
    af.quantize_selected_label_track(reference_name="beats", verbose=False)
    assert "Total adjustment" not in capsys.readouterr().err


def test_quantize_labels_summary_is_shown_under_verbose(monkeypatch, tmp_path, capsys):
    _stub_orchestrator_env(
        monkeypatch,
        tmp_path,
        lambda *a, **k: _run_result(stderr="Total adjustment: 0\n"),
    )
    af.quantize_selected_label_track(reference_name="beats", verbose=True)
    assert "Total adjustment" in capsys.readouterr().err


def test_quantize_labels_failure_becomes_a_quantize_error(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "q.py", stderr="line 3: not a label")

    _stub_orchestrator_env(monkeypatch, tmp_path, boom)
    with pytest.raises(af.QuantizeError, match="line 3: not a label"):
        af.quantize_selected_label_track(reference_name="beats")


# --- resolving where -q writes the versioned .txt ---------------------------


def test_resolve_quantize_dir_prefers_cwd_when_it_holds_the_project(
    monkeypatch, tmp_path
):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    assert rb._resolve_quantize_dir("song") == tmp_path


def test_resolve_quantize_dir_uses_the_sole_recent_project_dir(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)  # cwd does not hold the project
    proj = tmp_path / "proj"
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [proj])
    assert rb._resolve_quantize_dir("song") == proj


def test_resolve_quantize_dir_refuses_when_ambiguous(monkeypatch, tmp_path, capsys):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        af, "find_recent_project_dirs", lambda _s: [Path("/a"), Path("/b")]
    )
    assert rb._resolve_quantize_dir("song") is None
    err = capsys.readouterr().err
    assert "/a" in err and "/b" in err


def test_resolve_quantize_dir_refuses_when_unknown(monkeypatch, tmp_path, capsys):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])
    assert rb._resolve_quantize_dir("song") is None
    assert "not in Audacity's Open Recent" in capsys.readouterr().err


# --- persisting the quantized track to the versioned .txt -------------------


def _stub_quantize(
    monkeypatch, stem="song", name="chords", content="0.0\t1.0\tv\n", changed=True
):
    """Fake the live layer for _quantize_open_project. Returns a list recording
    whether the (mutating) quantize step ran, so refusal tests can assert it did
    not."""
    import rebuildap as rb

    calls = []
    monkeypatch.setattr(rb, "prerequisites_met", lambda _v: True)
    monkeypatch.setattr(af, "open_project_wave_stem", lambda *a, **k: stem)

    def fake_quantize(ref, verbose):
        calls.append("quantized")
        return (name, 1, stem, content, changed)

    monkeypatch.setattr(af, "quantize_selected_label_track", fake_quantize)
    return calls


def test_quantize_writes_the_versioned_txt_in_cwd_when_it_holds_the_project(
    monkeypatch, tmp_path, capsys
):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")  # cwd holds the project
    calls = _stub_quantize(monkeypatch, stem="song", name="chords", content="C\n")

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    written = tmp_path / "chords_song.txt"
    assert written.read_text() == "C\n"
    assert calls == ["quantized"]
    assert str(written) in capsys.readouterr().out


def test_quantize_writes_to_the_discovered_project_dir_from_any_cwd(
    monkeypatch, tmp_path, capsys
):
    """The whole point: run from anywhere, the .txt lands in the project's own
    directory (found via Open Recent), not cwd."""
    import rebuildap as rb

    cwd = tmp_path / "elsewhere"
    proj = tmp_path / "batch01" / "song"
    cwd.mkdir()
    proj.mkdir(parents=True)
    monkeypatch.chdir(cwd)  # not the project dir
    calls = _stub_quantize(monkeypatch, stem="song", name="chords", content="C\n")
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [proj])

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    assert (proj / "chords_song.txt").read_text() == "C\n"
    assert not (cwd / "chords_song.txt").exists()
    assert calls == ["quantized"]


def test_quantize_refuses_before_mutating_when_project_dir_unknown(
    monkeypatch, tmp_path, capsys
):
    """No writable directory -> refuse *before* touching the project, so there is
    no quantized-but-unpersisted half-state."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)  # not the project dir
    calls = _stub_quantize(monkeypatch, stem="song", name="chords")
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    assert calls == [], "must not quantize when it cannot persist the result"
    assert not (tmp_path / "chords_song.txt").exists()
    assert "Could not locate" in capsys.readouterr().err


def test_quantize_does_not_rewrite_an_already_current_file(
    monkeypatch, tmp_path, capsys
):
    """Already-quantized track + up-to-date file -> nothing to do, file untouched
    (not even reformatted)."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    existing = tmp_path / "chords_song.txt"
    existing.write_text("0.0\t1.0\tv\n")  # bare floats, equivalent to canonical
    _stub_quantize(
        monkeypatch, name="chords", content="0.000000\t1.000000\tv\n", changed=False
    )

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    assert existing.read_text() == "0.0\t1.0\tv\n", "equivalent file must be untouched"
    assert "already quantized; nothing to do" in capsys.readouterr().out


def test_quantize_updates_a_stale_file_even_if_the_track_was_already_quantized(
    monkeypatch, tmp_path, capsys
):
    """Track already on grid, but its versioned file is stale -> write it anyway."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    existing = tmp_path / "chords_song.txt"
    existing.write_text("9.9\t9.9\tstale\n")
    _stub_quantize(
        monkeypatch, name="chords", content="0.000000\t1.000000\tv\n", changed=False
    )

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)

    assert existing.read_text() == "0.000000\t1.000000\tv\n"
    assert "was already quantized; updated its file" in capsys.readouterr().out


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
