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
from rebuildap import audacity_present as ap
from rebuildap.utils import normalize_label_line


def _run_result(stderr="", stdout=""):
    """Stand-in for the CompletedProcess quantize_selected_label_track reads."""
    return types.SimpleNamespace(stderr=stderr, stdout=stdout)


def _track(name, kind="label", selected=False):
    return {"name": name, "kind": kind, "selected": 1 if selected else 0}


def _fake_label_source(monkeypatch, contents):
    """Fake the label read quantize makes, from ``.txt`` content per track name."""
    monkeypatch.setattr(
        af,
        "read_label_tracks_precisely",
        lambda aup3_path: (
            {n: af._labels_from_txt(c) for n, c in contents.items()},
            True,
        ),
    )


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
    target_i, target_name, ref_i, ref_name, passed_over = af.resolve_quantize_targets(
        tracks, reference_name="beats"
    )
    assert (target_i, target_name) == (1, "chords")
    assert (ref_i, ref_name) == (2, "beats")
    assert passed_over == []


def test_autodetects_the_sole_beats_track_when_no_name_given():
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    _, _, ref_i, ref_name, passed_over = af.resolve_quantize_targets(
        tracks, reference_name=None
    )
    assert (ref_i, ref_name) == (2, "beats")
    assert passed_over == [], "a sole beats track involves no choice"


def test_no_selected_label_track_is_an_error():
    tracks = [_track("song", kind="wave"), _track("beats")]
    with pytest.raises(af.LabelTrackError, match="[Ss]elect"):
        af.resolve_quantize_targets(tracks, reference_name="beats")


def test_multiple_selected_label_tracks_is_an_error():
    tracks = [
        _track("chords", selected=True),
        _track("parts", selected=True),
        _track("beats"),
    ]
    with pytest.raises(af.LabelTrackError, match="chords|parts"):
        af.resolve_quantize_targets(tracks, reference_name="beats")


def test_named_reference_absent_is_an_error():
    tracks = [_track("chords", selected=True), _track("beats")]
    with pytest.raises(af.QuantizeError, match="bars"):
        af.resolve_quantize_targets(tracks, reference_name="bars")


def test_autodetect_with_no_beats_track_is_an_error():
    tracks = [_track("chords", selected=True), _track("parts")]
    with pytest.raises(af.QuantizeError, match="beats|-q"):
        af.resolve_quantize_targets(tracks, reference_name=None)


def test_autodetect_with_several_beats_tracks_uses_the_nearest_below_the_target():
    # Beats tracks are a quantizing technicality kept under the tracks that
    # matter, so the one directly beneath wins. 'beats_above' is *closer* by
    # distance (1 row vs 2), which is what rules out "nearest either way"; and
    # 'beats_far' rules out "bottommost".
    tracks = [
        _track("song", kind="wave"),
        _track("beats_above"),
        _track("chords", selected=True),
        _track("parts"),
        _track("beats_near"),
        _track("beats_far"),
    ]
    _, _, ref_i, ref_name, passed_over = af.resolve_quantize_targets(
        tracks, reference_name=None
    )
    assert (ref_i, ref_name) == (4, "beats_near")
    assert passed_over == ["beats_above", "beats_far"]


def test_autodetect_ignores_non_label_tracks_named_like_beats():
    # An audio track called 'beats' is not a grid to snap to.
    tracks = [
        _track("chords", selected=True),
        _track("beats_audio", kind="wave"),
        _track("beats"),
        _track("beats_alt"),
    ]
    _, _, ref_i, ref_name, _ = af.resolve_quantize_targets(tracks, reference_name=None)
    assert (ref_i, ref_name) == (2, "beats")


def test_autodetect_with_several_beats_tracks_none_below_is_an_error():
    # The layout breaks the convention; refuse visibly rather than fall back to
    # one above, and name the candidates.
    tracks = [
        _track("beats"),
        _track("beats_alt"),
        _track("chords", selected=True),
    ]
    with pytest.raises(af.QuantizeError, match="below 'chords'") as excinfo:
        af.resolve_quantize_targets(tracks, reference_name=None)
    assert "beats_alt" in str(excinfo.value)


def test_a_sole_beats_track_above_the_target_is_still_used():
    # The nearest-below rule only settles a choice among several; one beats track
    # is unambiguous wherever it sits.
    tracks = [_track("beats"), _track("chords", selected=True)]
    _, _, ref_i, ref_name, passed_over = af.resolve_quantize_targets(
        tracks, reference_name=None
    )
    assert (ref_i, ref_name) == (0, "beats")
    assert passed_over == []


def test_a_named_reference_wins_over_the_nearest_below():
    tracks = [
        _track("chords", selected=True),
        _track("beats"),
        _track("beats_alt"),
    ]
    _, _, ref_i, ref_name, passed_over = af.resolve_quantize_targets(
        tracks, reference_name="beats_alt"
    )
    assert (ref_i, ref_name) == (2, "beats_alt")
    assert passed_over == [], "naming the track is not a choice made for the user"


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
    # 'chords' currently off the grid at 0.12; quantizing will move it.
    _fake_label_source(
        monkeypatch,
        {
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

    target_name, target_index, _ref, content, changed, _ = (
        af.quantize_selected_label_track(reference_name=None)
    )

    assert (target_name, target_index, changed) == ("chords", 1, True)
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
    _fake_label_source(
        monkeypatch,
        {"chords": current, "beats": af._format_track_txt([(0.0, 0.0, "")])},
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

    _, _, _, content, changed, _ = af.quantize_selected_label_track(
        reference_name="beats"
    )

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


def test_scope_tolerates_selection_edges_rounded_off_the_boundary():
    """The selection and the label times are independently rounded to ~6
    significant digits, so an edge can land one ulp off the boundary it was taken
    from -- measured: a region set to 5.13097 read back as 5.13098. Without
    tolerance the first and last boundaries of a click-selected label track are
    silently left unquantized."""
    orig = [(5.13097, 5.13097, "first"), (20.5637, 20.5637, "last")]
    quant = [(5.2, 5.2, "first"), (20.6, 20.6, "last")]
    # region reported one ulp *inside* at both ends
    assert af._scope_to_selection(orig, quant, (5.13098, 20.5636)) == [
        (5.2, 5.2, "first"),
        (20.6, 20.6, "last"),
    ]


def test_scope_tolerance_does_not_reach_a_genuinely_outside_boundary():
    orig = [(1.0, 1.0, "before"), (30.0, 30.0, "after")]
    quant = [(1.5, 1.5, "before"), (30.5, 30.5, "after")]
    assert af._scope_to_selection(orig, quant, (5.13098, 20.5636)) == [
        (1.0, 1.0, "before"),
        (30.0, 30.0, "after"),
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
    _fake_label_source(
        monkeypatch,
        {"chords": current, "beats": af._format_track_txt([(0.0, 0.0, "")])},
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


def test_quantize_returns_the_reference_it_snapped_to(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.1\t2.1\ta\n")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))

    _, _, reference_name, _, _, _ = af.quantize_selected_label_track(
        reference_name=None
    )

    assert reference_name == "beats"


def test_choosing_among_several_beats_tracks_is_announced_on_stderr(
    monkeypatch, tmp_path, capsys
):
    """The hotkey alerts on a successful run that wrote to stderr, so a choice made
    for the user must land there - naming what was used and what was passed over."""
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.1\t2.1\ta\n")
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
        _track("beats_alt"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)
    grid = af._format_track_txt([(0.0, 0.0, "")])
    _fake_label_source(
        monkeypatch, {"chords": current, "beats": grid, "beats_alt": grid}
    )
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))

    af.quantize_selected_label_track(reference_name=None)

    err = capsys.readouterr().err
    assert "'beats'" in err
    assert "beats_alt" in err


def test_a_sole_beats_track_is_not_announced(monkeypatch, tmp_path, capsys):
    """No choice, no note: otherwise every hotkey quantize would raise an alert."""
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.1\t2.1\ta\n")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))

    af.quantize_selected_label_track(reference_name=None)

    assert capsys.readouterr().err == ""


def test_only_boundaries_inside_the_selection_are_snapped(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a"), (5.0, 6.0, "b")])
    # quantize_labels would snap everything; the temp holds the fully-snapped form.
    quantized = "1.1\t2.1\ta\n5.1\t6.1\tb\n"
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, quantized)
    # Selection [4.0, 7.0]: only 'b' is inside -> only its boundaries move.
    monkeypatch.setattr(af, "read_time_selection", lambda: (4.0, 7.0))

    _, _, _, content, changed, _ = af.quantize_selected_label_track(
        reference_name="beats"
    )

    assert changed is True
    assert content == af._format_track_txt([(1.0, 2.0, "a"), (5.1, 6.1, "b")])


def test_no_region_quantizes_the_whole_track(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a"), (5.0, 6.0, "b")])
    quantized = "1.1\t2.1\ta\n5.1\t6.1\tb\n"
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, quantized)
    monkeypatch.setattr(af, "read_time_selection", lambda: (3.0, 3.0))  # cursor only

    _, _, _, content, _, _ = af.quantize_selected_label_track(reference_name="beats")

    assert content == af._format_track_txt([(1.1, 2.1, "a"), (5.1, 6.1, "b")])


def test_force_whole_track_skips_the_selection_read(monkeypatch, tmp_path):
    current = af._format_track_txt([(1.0, 2.0, "a")])
    _orchestrator_with_quantized(monkeypatch, tmp_path, current, "1.1\t2.1\ta\n")

    def boom():
        raise AssertionError("read_time_selection must not be called with -f")

    monkeypatch.setattr(af, "read_time_selection", boom)

    _, _, _, content, _, _ = af.quantize_selected_label_track(
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
    _fake_label_source(
        monkeypatch, {"chords": "9.0\t9.0\told\n", "beats": "0.0\t0.0\t\n"}
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

    _, _, _, content, _, _ = af.quantize_selected_label_track(reference_name="beats")
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


def _nothing_open(monkeypatch):
    """No .aup3 held open: an unsaved project, or a process that cannot be read."""
    monkeypatch.setattr(ap, "open_project_paths", lambda: [])


def test_resolve_project_dir_uses_the_open_files_directory(monkeypatch, tmp_path):
    """The 2026-09-30 incident's directory half: a project opened by script is in
    no Open Recent list, but Audacity holds its file open, which names the place."""
    import rebuildap as rb

    def forbidden(_stem):
        raise AssertionError("Open Recent consulted although the open file is known")

    monkeypatch.chdir(tmp_path)
    proj = Path("/scratch/rb_probe")
    monkeypatch.setattr(ap, "open_project_paths", lambda: [proj / "rb_probe.aup3"])
    monkeypatch.setattr(af, "find_recent_project_dirs", forbidden)
    assert rb._resolve_project_dir("rb_probe", "-q") == proj


def test_resolve_project_dir_open_copy_wins_over_cwd(monkeypatch, tmp_path):
    """Run from batch01/<song> while the same-stem copy on another disk is the one
    open: the open copy is the project being changed, so its directory is where
    the label file belongs - not batch01's, the other project's source of truth."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")  # cwd holds a same-stem project
    copy = Path("/Volumes/SSD/song")
    monkeypatch.setattr(ap, "open_project_paths", lambda: [copy / "song.aup3"])
    assert rb._resolve_project_dir("song", "-q") == copy


def test_resolve_project_dir_refuses_when_both_copies_are_open(monkeypatch, tmp_path):
    # A set of open paths, not a window-to-path map: with both open, the frontmost
    # window's stem cannot say which one it is - even when cwd holds one of them.
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    a, b = tmp_path, Path("/b/song")
    monkeypatch.setattr(
        ap, "open_project_paths", lambda: [a / "song.aup3", b / "song.aup3"]
    )
    with pytest.raises(SystemExit) as excinfo:
        rb._resolve_project_dir("song", "-q")
    assert str(a) in str(excinfo.value) and str(b) in str(excinfo.value)


def test_resolve_project_dir_ignores_open_projects_with_another_stem(
    monkeypatch, tmp_path
):
    # An open 'other.aup3' is not this project and must not answer for it.
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ap, "open_project_paths", lambda: [Path("/a/other.aup3")])
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])
    with pytest.raises(SystemExit):
        rb._resolve_project_dir("song", "-q")


# With nothing held open (an unsaved project), the older routes still apply.


def test_resolve_project_dir_prefers_cwd_when_it_holds_the_project(
    monkeypatch, tmp_path
):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    _nothing_open(monkeypatch)
    (tmp_path / "song.aup3").write_text("")
    assert rb._resolve_project_dir("song", "-q") == tmp_path


def test_resolve_project_dir_uses_the_sole_recent_project_dir(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)  # cwd does not hold the project
    _nothing_open(monkeypatch)
    proj = tmp_path / "proj"
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [proj])
    assert rb._resolve_project_dir("song", "-q") == proj


def test_resolve_project_dir_refuses_when_ambiguous(monkeypatch, tmp_path):
    """Refusing is a failure, not a quiet success: the hotkey titles a run by its
    exit status, and an exit-0 refusal read as "ok" live (2026-09-14)."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    _nothing_open(monkeypatch)
    monkeypatch.setattr(
        af, "find_recent_project_dirs", lambda _s: [Path("/a"), Path("/b")]
    )
    with pytest.raises(SystemExit) as excinfo:
        rb._resolve_project_dir("song", "-q")
    message = str(excinfo.value)
    assert "/a" in message and "/b" in message


def test_resolve_project_dir_refuses_when_unknown(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    _nothing_open(monkeypatch)
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])
    with pytest.raises(SystemExit, match="not in Audacity's Open Recent"):
        rb._resolve_project_dir("song", "-q")


# --- persisting the quantized track to the versioned .txt -------------------


def _stub_quantize(
    monkeypatch,
    stem="song",
    name="chords",
    content="0.0\t1.0\tv\n",
    changed=True,
    reference="beats",
    open_paths=(),
    seen=None,
    precise=True,
):
    """Fake the live layer for _quantize_open_project. Returns a list recording
    whether the (mutating) quantize step ran, so refusal tests can assert it did
    not. ``open_paths`` is what Audacity holds open; ``seen`` (a dict) receives
    the ``aup3_path`` quantize was handed."""
    import rebuildap as rb

    calls = []
    monkeypatch.setattr(rb, "prerequisites_met", lambda _command: True)
    monkeypatch.setattr(af, "open_project_stem", lambda *a, **k: stem)
    monkeypatch.setattr(ap, "open_project_paths", lambda: list(open_paths))

    def fake_quantize(ref, verbose, whole_track=False, aup3_path=None):
        calls.append(f"quantized:whole={whole_track}")
        if seen is not None:
            seen["aup3_path"] = aup3_path
        return (name, 1, reference, content, changed, precise)

    monkeypatch.setattr(af, "quantize_selected_label_track", fake_quantize)
    return calls


def test_quantize_writes_the_versioned_txt_in_cwd_when_it_holds_the_project(
    monkeypatch, tmp_path, capsys
):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")  # cwd holds the project
    calls = _stub_quantize(monkeypatch, stem="song", name="chords", content="C\n")

    rb._quantize_open_project(None, verbose=False)

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

    rb._quantize_open_project(None, verbose=False)

    assert (proj / "chords_song.txt").read_text() == "C\n"
    assert not (cwd / "chords_song.txt").exists()
    assert calls == ["quantized:whole=False"]


def test_quantize_refuses_before_mutating_when_project_dir_unknown(
    monkeypatch, tmp_path
):
    """No writable directory -> refuse *before* touching the project, so there is
    no quantized-but-unpersisted half-state - and exit non-zero, since nothing
    the user asked for happened."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)  # not the project dir
    calls = _stub_quantize(monkeypatch, stem="song", name="chords")
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])

    with pytest.raises(SystemExit, match="Could not locate"):
        rb._quantize_open_project(None, verbose=False)

    assert calls == [], "must not quantize when it cannot persist the result"
    assert not (tmp_path / "chords_song.txt").exists()


def test_quantize_refuses_before_mutating_when_the_project_is_ambiguous(
    monkeypatch, tmp_path
):
    """Two projects open -> no way to know whose .txt this is; refuse rather than
    quantize into the wrong project's source of truth."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_quantize(monkeypatch, stem="song", name="chords")

    def ambiguous():
        raise af.ProjectIdentityError("Several projects are open: song, song_G.")

    monkeypatch.setattr(af, "open_project_stem", ambiguous)

    with pytest.raises(SystemExit) as excinfo:
        rb._quantize_open_project(None, verbose=False)
    assert "song_G" in str(excinfo.value)
    assert calls == [], "must not quantize when the target project is unknown"


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

    rb._quantize_open_project(None, verbose=False)

    assert existing.read_text() == "0.0\t1.0\tv\n", "equivalent file must be untouched"
    assert "already quantized to 'beats'; nothing to do" in capsys.readouterr().out


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

    rb._quantize_open_project(None, verbose=False)

    assert existing.read_text() == "0.000000\t1.000000\tv\n"
    assert (
        "was already quantized to 'beats'; updated its file" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "changed, wrote",
    [(True, True), (False, False), (False, True), (True, False)],
)
def test_every_quantize_outcome_names_the_reference_track(
    capsys, tmp_path, changed, wrote
):
    """Snapping to the wrong grid looks plausible at a glance, so every outcome says
    which grid it was - whether or not a choice among several was made."""
    import rebuildap as rb

    rb._report_quantize_outcome(
        "chords", "beats_half", tmp_path / "chords_song.txt", changed, wrote
    )

    out = capsys.readouterr().out
    assert "'chords'" in out
    assert "'beats_half'" in out


def test_quantize_exits_nonzero_when_there_is_nothing_to_quantize(monkeypatch):
    """An empty project, no label tracks, no Audacity: the command changed nothing
    it was asked to change, so it fails - the hotkey titles exit 0 "ok"."""
    import rebuildap as rb

    seen = []
    monkeypatch.setattr(
        rb, "prerequisites_met", lambda command: seen.append(command) or False
    )
    monkeypatch.setattr(
        af,
        "quantize_selected_label_track",
        lambda *a, **k: pytest.fail("must not quantize"),
    )

    with pytest.raises(SystemExit) as excinfo:
        rb._quantize_open_project(None, verbose=False)

    assert excinfo.value.code not in (0, None)
    assert seen == ["quantize"]


def test_quantize_force_flag_requests_whole_track(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_quantize(monkeypatch, name="chords", content="C\n")

    rb._quantize_open_project(None, verbose=False, force=True)

    assert calls == ["quantized:whole=True"]


def test_selection_read_failure_exits_pointing_at_force(monkeypatch, tmp_path):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    monkeypatch.setattr(rb, "prerequisites_met", lambda _command: True)
    monkeypatch.setattr(af, "open_project_stem", lambda *a, **k: "song")
    monkeypatch.setattr(ap, "open_project_paths", lambda: [])

    def boom(ref, verbose, whole_track=False, aup3_path=None):
        raise af.SelectionReadError("Nyquist did not report the selection")

    monkeypatch.setattr(af, "quantize_selected_label_track", boom)

    with pytest.raises(SystemExit) as excinfo:
        rb._quantize_open_project(None, verbose=False)
    assert "-f" in str(excinfo.value)


# --- the quantize command ---------------------------------------------------


def _dispatched(monkeypatch, argv):
    """Run main() on `argv` with the quantize entry point stubbed out."""
    import sys as _sys

    captured = {}
    monkeypatch.setattr(
        rebuildap,
        "_quantize_open_project",
        lambda *a, **k: captured.update(kwargs=k, args=a),
    )
    monkeypatch.setattr(_sys, "argv", argv)
    rebuildap.main()
    return captured


def test_quantize_without_a_track_name_requests_autodetect(monkeypatch):
    """No name given -> None, which is what tells the beats track to be found.

    This is where the _QUANTIZE_AUTODETECT sentinel went: it existed only to
    tell a bare `-q` from no `-q` at all, and running the command *is* now that
    distinction.
    """
    captured = _dispatched(monkeypatch, ["rebuildap", "quantize"])
    assert captured["kwargs"]["beats_track"] is None


def test_quantize_with_a_name_carries_the_track_name(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "quantize", "beats"])
    assert captured["kwargs"]["beats_track"] == "beats"


def test_quantize_with_force_passes_both(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "quantize", "-f"])
    assert captured["kwargs"]["beats_track"] is None
    assert captured["kwargs"]["force"] is True


def test_quantize_takes_no_second_positional(monkeypatch):
    """One reference track, not a list -- an extra word is a typo, not a track."""
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["rebuildap", "quantize", "beats", "extra"])
    with pytest.raises(SystemExit) as excinfo:
        rebuildap.main()
    assert excinfo.value.code != 0


# --- exact label times (GetInfo rounds to 1 ms at 100 s) ----------------------
#
# Bar 59 of a real project: the beat sits at 100.68907563025209, GetInfo shows
# 100.689, and a chord quantized through GetInfo landed at 100.689 -- 3.3 samples
# early, and every later run called it "already quantized".

BEAT = 100.68907563025209


@pytest.mark.parametrize("t", [BEAT, 1.234e-07, 1e-06, 0.0, 5.0, 3600.123456789012])
def test_exact_times_are_written_positionally_and_read_back_identical(t):
    """The label import rejects scientific notation, and repr() would use it."""
    text = af._format_exact_time(t)
    assert "e" not in text.lower()
    assert float(text) == t
    line = normalize_label_line(f"{text}\t{text}\tx\n")
    assert line is not None
    assert float(line.split("\t")[0]) == t


def _exact_world(monkeypatch, tmp_path, chords, quantized_out, seen):
    """quantize_selected_label_track against exact times: 'chords' selected,
    'beats' holding BEAT; the faked quantize_labels records the files it was
    handed and writes ``quantized_out``; the import records the file it got."""
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)

    def read(aup3_path):
        seen["aup3_path"] = aup3_path
        return {"chords": chords, "beats": [(BEAT, BEAT, "")]}, True

    monkeypatch.setattr(af, "read_label_tracks_precisely", read)
    monkeypatch.setattr(af, "locate_quantize_script", lambda: tmp_path / "q.py")
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))  # whole track

    def fake_run(argv, **kwargs):
        seen["reference_file"] = Path(argv[-2]).read_text()
        seen["target_file"] = Path(argv[-1]).read_text()
        Path(argv[-1]).write_text(quantized_out)
        return _run_result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: None)

    def fake_import(path, name=None):
        seen["imported"] = Path(path).read_text()

    monkeypatch.setattr(af, "make_label_track_from_file", fake_import)
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(af, "move_track_to", lambda *a: None)
    monkeypatch.setattr(af, "select_tracks", lambda *a: None)


# quantize_labels.py writes the reference times it snapped to with repr().
SNAPPED_TO_BEAT = f"{BEAT!r}\t{BEAT!r}\tC\n"


def test_quantize_labels_is_handed_full_digit_times(monkeypatch, tmp_path):
    seen = {}
    _exact_world(
        monkeypatch, tmp_path, [(100.689, 100.689, "C")], SNAPPED_TO_BEAT, seen
    )

    af.quantize_selected_label_track(reference_name="beats")

    assert "100.68907563025209" in seen["reference_file"]


def test_a_chord_a_few_samples_early_is_moved_exactly_onto_the_beat(
    monkeypatch, tmp_path
):
    """The bug: 100.689 against a beat at 100.68907563025209 (75 us, 3.3 samples)."""
    seen = {}
    _exact_world(
        monkeypatch, tmp_path, [(100.689, 100.689, "C")], SNAPPED_TO_BEAT, seen
    )

    _, _, _, content, changed, _ = af.quantize_selected_label_track(
        reference_name="beats"
    )

    assert changed is True
    imported = af._labels_from_txt(seen["imported"])
    assert imported == [(BEAT, BEAT, "C")], "the project gets the beat bit-exact"
    # The versioned file keeps Audacity's own 6-decimal export format.
    assert content == "100.689076\t100.689076\tC\n"


def test_within_a_microsecond_of_the_beat_counts_as_already_quantized(
    monkeypatch, tmp_path
):
    """A label written from a 6-decimal file sits up to 0.5 us off the beat, and
    must not be re-imported on every run."""
    seen = {}
    on_grid_to_six_decimals = [(100.689076, 100.689076, "C")]  # 0.37 us off
    _exact_world(monkeypatch, tmp_path, on_grid_to_six_decimals, SNAPPED_TO_BEAT, seen)

    _, _, _, content, changed, _ = af.quantize_selected_label_track(
        reference_name="beats"
    )

    assert changed is False
    assert "imported" not in seen, "an already-quantized track is left alone"
    assert content == "100.689076\t100.689076\tC\n"


def test_two_microseconds_off_the_beat_is_not_on_the_grid(monkeypatch, tmp_path):
    """The tolerance covers 6-decimal rounding, nothing coarser."""
    seen = {}
    off = BEAT + 2e-6
    _exact_world(monkeypatch, tmp_path, [(off, off, "C")], SNAPPED_TO_BEAT, seen)

    _, _, _, _, changed, _ = af.quantize_selected_label_track(reference_name="beats")

    assert changed is True
    assert af._labels_from_txt(seen["imported"]) == [(BEAT, BEAT, "C")]


def test_the_project_file_path_reaches_the_label_read(monkeypatch, tmp_path):
    seen = {}
    _exact_world(
        monkeypatch, tmp_path, [(100.689, 100.689, "C")], SNAPPED_TO_BEAT, seen
    )
    project = tmp_path / "song.aup3"

    af.quantize_selected_label_track(reference_name="beats", aup3_path=project)

    assert seen["aup3_path"] == project


# --- handing quantize the open project's file ---------------------------------


def _copy_dir(tmp_path):
    """A same-stem copy's directory beside cwd, real so the label file can land."""
    copy = tmp_path / "copy"
    copy.mkdir()
    return copy


def _quantize_in_cwd(monkeypatch, tmp_path, open_paths):
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    seen = {}
    _stub_quantize(monkeypatch, stem="song", open_paths=open_paths, seen=seen)
    rb._quantize_open_project(None, verbose=False)
    return seen["aup3_path"]


def test_quantize_is_handed_the_open_file_of_the_project(monkeypatch, tmp_path):
    """The file Audacity holds open, not one derived from cwd: a same-stem copy
    elsewhere may be the one in the window."""
    open_file = _copy_dir(tmp_path) / "song.aup3"
    other = Path("/elsewhere/other.aup3")

    assert _quantize_in_cwd(monkeypatch, tmp_path, [other, open_file]) == open_file


def test_quantize_is_handed_no_file_when_none_of_the_stem_is_open(
    monkeypatch, tmp_path
):
    """Nothing known: the label read falls back to GetInfo and says so."""
    other = Path("/elsewhere/other.aup3")

    assert _quantize_in_cwd(monkeypatch, tmp_path, [other]) is None


def test_quantize_refuses_untouched_when_two_of_the_stem_are_open(
    monkeypatch, tmp_path
):
    """Which one is in the front window is unknowable, so neither is guessed -
    and the refusal comes before the project is changed."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    calls = _stub_quantize(
        monkeypatch,
        stem="song",
        open_paths=[Path("/a/song.aup3"), Path("/b/song.aup3")],
    )
    with pytest.raises(SystemExit):
        rb._quantize_open_project(None, verbose=False)
    assert calls == []


def test_quantize_is_handed_the_file_even_when_audacity_holds_it_open_twice(
    monkeypatch, tmp_path
):
    """Measured 2026-09-30: open_project_paths() listed the one open .aup3 twice
    (Audacity holds two handles on it). The same path twice is one file, not two
    copies."""
    open_file = _copy_dir(tmp_path) / "song.aup3"

    assert _quantize_in_cwd(monkeypatch, tmp_path, [open_file, open_file]) == open_file


# --- the versioned file when the label read was rounded (GetInfo fallback) ------
#
# Measured live 2026-09-30: a fallback run rewrote an exact 100.689076 with
# GetInfo's 100.689000 and reported "was already quantized; updated its file".
# A rounded read cannot tell those apart, so it must not "correct" the file.


def test_a_rounded_read_leaves_a_file_that_rounds_to_it_alone(tmp_path):
    import rebuildap as rb

    out = tmp_path / "chords_song.txt"
    out.write_text("100.689076\t100.689076\tC\n")

    wrote = rb._write_if_divergent(out, "100.689000\t100.689000\tC\n", rounded=True)

    assert wrote is False
    assert out.read_text() == "100.689076\t100.689076\tC\n"


def test_an_exact_read_writes_the_same_difference(tmp_path):
    """75 us is a real move when the read was exact - the bar-59 fix itself."""
    import rebuildap as rb

    out = tmp_path / "chords_song.txt"
    out.write_text("100.689000\t100.689000\tC\n")

    wrote = rb._write_if_divergent(out, "100.689076\t100.689076\tC\n", rounded=False)

    assert wrote is True
    assert out.read_text() == "100.689076\t100.689076\tC\n"


def test_a_rounded_read_still_writes_a_change_it_can_see(tmp_path):
    import rebuildap as rb

    out = tmp_path / "chords_song.txt"
    out.write_text("100.5\t100.5\tC\n")

    assert rb._write_if_divergent(out, "100.689\t100.689\tC\n", rounded=True) is True
    assert out.read_text() == "100.689\t100.689\tC\n"


def test_a_rounded_read_matches_rounding_by_significant_digits_not_decimals(
    tmp_path,
):
    """Past 1000 s six significant digits are 2 decimals: 1234.567891 shows as
    1234.57, 2.1 ms away, and is still the same label."""
    import rebuildap as rb

    out = tmp_path / "chords_song.txt"
    out.write_text("1234.567891\t1234.567891\tC\n")

    assert rb._write_if_divergent(out, "1234.57\t1234.57\tC\n", rounded=True) is False


@pytest.mark.parametrize(
    "incoming",
    [
        "100.689000\t100.689000\tD\n",  # other text
        "100.689000\t100.689000\tC\n5.0\t5.0\tE\n",  # another label
    ],
)
def test_a_rounded_read_still_writes_other_differences(tmp_path, incoming):
    import rebuildap as rb

    out = tmp_path / "chords_song.txt"
    out.write_text("100.689076\t100.689076\tC\n")

    assert rb._write_if_divergent(out, incoming, rounded=True) is True


def test_quantize_says_whether_its_label_read_was_exact(monkeypatch, tmp_path):
    seen = {}
    _exact_world(
        monkeypatch, tmp_path, [(100.689, 100.689, "C")], SNAPPED_TO_BEAT, seen
    )
    labels = {"chords": [(100.689, 100.689, "C")], "beats": [(BEAT, BEAT, "")]}

    monkeypatch.setattr(af, "read_label_tracks_precisely", lambda p: (labels, True))
    assert af.quantize_selected_label_track(reference_name="beats")[-1] is True

    monkeypatch.setattr(af, "read_label_tracks_precisely", lambda p: (labels, False))
    assert af.quantize_selected_label_track(reference_name="beats")[-1] is False


def test_a_fallback_quantize_leaves_an_exact_file_alone(monkeypatch, tmp_path, capsys):
    """End to end through the CLI: the file keeps its exact times."""
    import rebuildap as rb

    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    exact = "100.689076\t100.689076\tC\n"
    (tmp_path / "chords_song.txt").write_text(exact)
    _stub_quantize(
        monkeypatch,
        stem="song",
        content="100.689000\t100.689000\tC\n",
        changed=False,
        precise=False,
    )

    rb._quantize_open_project(None, verbose=False)

    assert (tmp_path / "chords_song.txt").read_text() == exact
    assert "nothing to do" in capsys.readouterr().out
