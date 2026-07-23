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
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))  # whole track

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
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))  # whole track
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


# --- reading the time selection via Nyquist ---------------------------------


def test_nyquist_selection_command_is_the_nil_return_form(tmp_path):
    """Must use the side-effect/nil-return form (no value returned), or Audacity
    pops a modal Message dialog that wedges the pipe. Writes to the given path."""
    path = tmp_path / "sel.txt"
    cmd = af._nyquist_write_selection_command(path)
    assert str(path) in cmd
    assert "(get (quote *selection*) (quote start))" in cmd
    assert "(get (quote *selection*) (quote end))" in cmd
    assert cmd.strip().endswith('(close fp))" Version="3"')  # nil-return
    assert "format nil" not in cmd  # a returned value would trigger the dialog


def test_parse_selection_file_reads_two_floats(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("1.70712\n4.44846\n")
    assert af._parse_selection_file(p) == (1.70712, 4.44846)


def test_parse_selection_file_orders_the_bounds(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("4.0\n1.0\n")
    assert af._parse_selection_file(p) == (1.0, 4.0)


def test_parse_selection_file_rejects_non_numeric(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("NIL\nNIL\n")  # accessor missing on this Audacity
    with pytest.raises(af.SelectionReadError):
        af._parse_selection_file(p)


def test_parse_selection_file_rejects_missing_file(tmp_path):
    with pytest.raises(af.SelectionReadError):
        af._parse_selection_file(tmp_path / "never_written.txt")


def test_read_time_selection_ignores_the_failed_status(monkeypatch):
    """Nyquist reports Failed! (no audio result) but the file write happened; the
    reader must ignore the exception and read the file."""
    import pyaudacity as pa

    def fake_do(cmd, timeout):
        # Simulate Nyquist: extract the path, write the bounds, then "fail".
        start = cmd.index('open \\"') + len('open \\"')
        end = cmd.index('\\"', start)
        Path(cmd[start:end]).write_text("2.5\n9.0\n")
        raise pa.PyAudacityException("BatchCommand finished: Failed!")

    monkeypatch.setattr(af, "_pa_do_timed", fake_do)
    assert af.read_time_selection() == (2.5, 9.0)


# --- per-boundary selection scoping -----------------------------------------


def test_scope_keeps_quantized_only_for_boundaries_inside_the_selection():
    orig = [(1.0, 2.0, "a"), (5.0, 6.0, "b"), (2.5, 7.0, "c")]
    quant = [(1.1, 2.1, "a"), (5.1, 6.1, "b"), (2.6, 7.1, "c")]
    # selection [2.0, 6.5]:
    #   a: start 1.0 out -> keep orig; end 2.0 in  -> keep quant
    #   b: both in       -> both quant
    #   c: start 2.5 in  -> quant;    end 7.0 out  -> keep orig
    assert af._scope_to_selection(orig, quant, (2.0, 6.5)) == [
        (1.0, 2.1, "a"),
        (5.1, 6.1, "b"),
        (2.6, 7.0, "c"),
    ]


def test_scope_point_label_inside_and_outside():
    orig = [(3.0, 3.0, "p"), (8.0, 8.0, "q")]
    quant = [(3.2, 3.2, "p"), (8.2, 8.2, "q")]
    assert af._scope_to_selection(orig, quant, (2.0, 4.0)) == [
        (3.2, 3.2, "p"),  # inside -> quantized
        (8.0, 8.0, "q"),  # outside -> original
    ]


# --- selection scoping wired through the orchestrator -----------------------


def _orchestrator_with_quantized(monkeypatch, tmp_path, current, quantized_out):
    """Set up quantize_selected_label_track's world: 'chords' selected with
    ``current`` content; the faked shell-out writes ``quantized_out`` to the temp."""
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
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: (Path(argv[-1]).write_text(quantized_out), _run_result())[1],
    )
    for fn in ("remove_selected_tracks",):
        monkeypatch.setattr(af, fn, lambda: None)
    monkeypatch.setattr(af, "make_label_track_from_file", lambda *a, **k: None)
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(af, "move_track_to", lambda *a: None)
    monkeypatch.setattr(af, "select_tracks", lambda *a: None)


def test_only_boundaries_inside_the_selection_are_snapped(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a"), (5.0, 6.0, "b")])
    # quantize_labels would snap everything; the temp holds the fully-snapped form.
    quantized = "1.1\t2.1\ta\n5.1\t6.1\tb\n"
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, quantized)
    # Selection [4.0, 7.0]: only 'b' is inside -> only its boundaries move.
    monkeypatch.setattr(af, "read_time_selection", lambda: (4.0, 7.0))

    _, _, _, content, changed = af.quantize_selected_label_track(reference_name="beats")

    assert changed is True
    assert content == af._format_track_txt([(1.0, 2.0, "a"), (5.1, 6.1, "b")])


def test_no_region_quantizes_the_whole_track(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a"), (5.0, 6.0, "b")])
    quantized = "1.1\t2.1\ta\n5.1\t6.1\tb\n"
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, quantized)
    monkeypatch.setattr(af, "read_time_selection", lambda: (3.0, 3.0))  # cursor only

    _, _, _, content, _ = af.quantize_selected_label_track(reference_name="beats")

    assert content == af._format_track_txt([(1.1, 2.1, "a"), (5.1, 6.1, "b")])


def test_force_whole_track_skips_the_selection_read(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.1\t2.1\ta\n")

    def boom():
        raise AssertionError("read_time_selection must not be called with -f")

    monkeypatch.setattr(af, "read_time_selection", boom)

    _, _, _, content, _ = af.quantize_selected_label_track(
        reference_name="beats", whole_track=True
    )
    assert content == af._format_track_txt([(1.1, 2.1, "a")])


def test_selection_read_error_propagates(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.1\t2.1\ta\n")

    def boom():
        raise af.SelectionReadError("no accessor")

    monkeypatch.setattr(af, "read_time_selection", boom)
    with pytest.raises(af.SelectionReadError):
        af.quantize_selected_label_track(reference_name="beats")


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
        lambda: {"chords": "9.0\t9.0\told\n", "beats": "y"},
    )
    script = tmp_path / "quantize_labels.py"
    monkeypatch.setattr(af, "locate_quantize_script", lambda: script)
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))  # whole track

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


# --- the -v summary: scoped to what was applied, not quantize_labels' figures --


def test_applied_adjustment_counts_only_moved_boundaries():
    orig = [(1.0, 2.0, "a"), (5.0, 6.0, "b")]
    final = [(1.0, 2.5, "a"), (5.0, 6.0, "b")]  # only a.end moved, by 0.5
    assert af._applied_adjustment(orig, final) == (1, 0.5)


def test_verbose_summary_names_the_selection_and_applied_change(
    monkeypatch, tmp_path, capsys
):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    # quantize_labels would snap to 1.5/2.5; selection [0.5, 3.0] covers both.
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.5\t2.5\ta\n")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.5, 3.0))

    af.quantize_selected_label_track(reference_name="beats", verbose=True)

    err = capsys.readouterr().err
    assert "in selection [0.500, 3.000]" in err
    assert "Quantized 2 boundaries" in err
    # quantize_labels' own whole-track figure must not bleed through.
    assert "Total adjustment" not in err


def test_verbose_summary_omits_selection_for_whole_track(monkeypatch, tmp_path, capsys):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.5\t2.5\ta\n")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))  # whole track

    af.quantize_selected_label_track(reference_name="beats", verbose=True)

    err = capsys.readouterr().err
    assert "Quantized 2 boundaries" in err
    assert "selection" not in err


def test_no_verbose_summary_without_the_flag(monkeypatch, tmp_path, capsys):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.5\t2.5\ta\n")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.5, 3.0))

    af.quantize_selected_label_track(reference_name="beats", verbose=False)

    assert "Quantized" not in capsys.readouterr().err


def test_quantize_labels_failure_becomes_a_quantize_error(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "unused")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "q.py", stderr="line 3: not a label")

    monkeypatch.setattr(subprocess, "run", boom)
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

    def fake_quantize(ref, verbose, whole_track=False):
        calls.append(f"quantized:whole={whole_track}")
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
    assert calls == ["quantized:whole=False"]
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
    assert calls == ["quantized:whole=False"]


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


def test_quantize_force_flag_requests_whole_track(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_quantize(monkeypatch, name="chords", content="C\n")

    rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False, force=True)

    assert calls == ["quantized:whole=True"]


def test_selection_read_failure_exits_pointing_at_force(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    monkeypatch.setattr(rb, "prerequisites_met", lambda _v: True)
    monkeypatch.setattr(af, "open_project_wave_stem", lambda *a, **k: "song")

    def boom(ref, verbose, whole_track=False):
        raise af.SelectionReadError("Nyquist did not report the selection")

    monkeypatch.setattr(af, "quantize_selected_label_track", boom)

    with pytest.raises(SystemExit) as excinfo:
        rb.rebuild(quantize=rb._QUANTIZE_AUTODETECT, verbose=False)
    assert "-f" in str(excinfo.value)


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


def test_q_with_force_is_allowed_and_passes_both(monkeypatch):
    """-q -f is valid (force = whole track); the guard must not reject it."""
    import sys as _sys

    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr(_sys, "argv", ["rebuildap", "-q", "-f"])
    rebuildap.main()
    assert captured["kwargs"]["quantize"] is rebuildap._QUANTIZE_AUTODETECT
    assert captured["kwargs"]["force"] is True


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
