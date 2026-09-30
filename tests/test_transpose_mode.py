"""Offline tests for the ``-t`` transpose-in-place mode.

``rebuildap -t N`` transposes the chords in the *selected* label track of the
open project by N half steps, scoped to the current time selection, and updates
the versioned ``.txt``. It rides the same spine as ``-q``
(:func:`resolve_selected_label_track`, :func:`resolve_selection_scope`,
:func:`replace_label_track`), so only what is specific to transposing is tested
here: which labels are in scope, what happens to text that is not a chord, the
accidental spelling, and the CLI guards.
"""

from pathlib import Path

import pytest

import rebuildap
from rebuildap import audacity_funcs as af
from rebuildap import audacity_present as ap


def _track(name, kind="label", selected=False):
    return {"name": name, "kind": kind, "selected": 1 if selected else 0}


def _labels(*rows):
    return list(rows)


# --- which labels are in scope, and what happens to non-chords ---------------


def test_whole_track_when_no_selection():
    new, skipped = af._transposed_labels(
        _labels((0.0, 1.0, "Am"), (2.0, 3.0, "Dm")), 2, True, None
    )
    assert new == [(0.0, 1.0, "Bm"), (2.0, 3.0, "Em")]
    assert skipped == []


def test_only_labels_starting_inside_the_selection_are_transposed():
    new, _ = af._transposed_labels(
        _labels((0.0, 1.0, "Am"), (2.0, 3.0, "Dm"), (9.0, 9.5, "Em")),
        2,
        True,
        (1.5, 4.0),
    )
    assert new == [(0.0, 1.0, "Am"), (2.0, 3.0, "Em"), (9.0, 9.5, "Em")]


def test_a_label_starting_inside_but_ending_outside_is_transposed_whole():
    """Straddles the far edge: the start is in, so the chord moves. Per *label* by
    start, deliberately unlike -q's per-boundary rule -- a chord belongs to its
    onset, so there is no half a label to transpose. This case is what
    distinguishes the start rule from an end rule."""
    new, _ = af._transposed_labels(_labels((6.0, 12.0, "Am")), 2, True, (5.0, 8.0))
    assert new == [(6.0, 12.0, "Bm")]


def test_a_label_starting_outside_but_ending_inside_is_left_alone():
    """Straddles the near edge: the start is out, so the chord stays -- even
    though the label overlaps the selection."""
    new, _ = af._transposed_labels(_labels((2.0, 6.0, "Am")), 2, True, (5.0, 8.0))
    assert new == [(2.0, 6.0, "Am")]


def test_a_label_spanning_the_whole_selection_is_left_alone():
    new, _ = af._transposed_labels(_labels((0.0, 9.0, "Am")), 2, True, (5.0, 8.0))
    assert new == [(0.0, 9.0, "Am")]


# --- rounded selection edges -------------------------------------------------
#
# The selection and the label times are two independently rounded views of the
# same instants: GetInfo and Nyquist each report ~6 significant digits and can
# disagree by one unit in the last place. Clicking a label track selects exactly
# first-label-start .. last-label-end, so those two labels sit *on* the edges and
# are the ones a strict comparison drops. Values below are measured, not invented:
# a selection set to 5.13097 was read back from Nyquist as 5.13098 (2026-07-29).


def test_first_label_survives_a_selection_start_rounded_up():
    """Nyquist reported the region start one ulp *above* the label it came from."""
    new, _ = af._transposed_labels(
        _labels((5.13097, 5.13097, "C")), 2, True, (5.13098, 20.5637)
    )
    assert new == [(5.13097, 5.13097, "D")]


def test_last_label_survives_a_selection_end_rounded_down():
    """Mirror case at the far edge. A point label has no width to absorb it."""
    new, _ = af._transposed_labels(
        _labels((20.5637, 20.5637, "F")), 2, True, (5.13097, 20.5636)
    )
    assert new == [(20.5637, 20.5637, "G")]


def test_a_label_genuinely_outside_is_still_left_alone():
    """The tolerance must not swallow a label that is really out of the region --
    otherwise it silently becomes a whole-track transpose."""
    new, _ = af._transposed_labels(
        _labels((4.0, 4.5, "C"), (25.0, 25.5, "C")), 2, True, (5.13098, 20.5636)
    )
    assert new == [(4.0, 4.5, "C"), (25.0, 25.5, "C")]


def test_tolerance_scales_with_magnitude():
    """Six significant digits means coarser absolute steps further out: ~1e-5 at
    5 s but ~1e-2 at 3600 s. A fixed epsilon would be too tight out here."""
    new, _ = af._transposed_labels(
        _labels((3600.01, 3600.01, "C")), 2, True, (3600.02, 4000.0)
    )
    assert new == [(3600.01, 3600.01, "D")]


def test_label_times_are_never_modified():
    new, _ = af._transposed_labels(_labels((1.25, 3.5, "Am")), 5, True, None)
    assert [(s, e) for s, e, _ in new] == [(1.25, 3.5)]


def test_non_chord_text_is_kept_and_reported():
    new, skipped = af._transposed_labels(
        _labels((0.0, 1.0, "Am"), (2.0, 3.0, "Guitar Solo")), 2, True, None
    )
    assert new == [(0.0, 1.0, "Bm"), (2.0, 3.0, "Guitar Solo")]
    assert skipped == ["Guitar Solo"]


def test_empty_text_is_not_reported_as_skipped():
    """An empty label is not a chord you failed to transpose."""
    _, skipped = af._transposed_labels(
        _labels((0.0, 1.0, ""), (2.0, 3.0, "   ")), 2, True, None
    )
    assert skipped == []


def test_out_of_scope_non_chords_are_not_reported():
    """Only labels actually attempted count as skipped."""
    _, skipped = af._transposed_labels(
        _labels((0.0, 1.0, "Guitar Solo"), (5.0, 6.0, "Am")), 2, True, (4.0, 7.0)
    )
    assert skipped == []


def test_section_prefixes_survive_while_their_chords_move():
    new, skipped = af._transposed_labels(
        _labels((0.0, 1.0, "Chorus1: Dj7"), (2.0, 3.0, "A: I let him slip away")),
        2,
        True,
        None,
    )
    assert new == [
        (0.0, 1.0, "Chorus1: Ej7"),
        (2.0, 3.0, "B: I let him slip away"),
    ]
    assert skipped == []


# --- accidental spelling ----------------------------------------------------


def test_flats_are_the_default_spelling():
    new, _ = af._transposed_labels(_labels((0.0, 1.0, "A")), 1, True, None)
    assert new == [(0.0, 1.0, "Bb")]


def test_sharps_when_asked():
    new, _ = af._transposed_labels(_labels((0.0, 1.0, "A")), 1, False, None)
    assert new == [(0.0, 1.0, "A#")]


def test_the_library_default_is_never_relied_on(monkeypatch):
    """The library defaults to sharps and rebuildap defaults to flats, so the
    flag must be passed explicitly at the call site -- otherwise a change to the
    library default would silently reflow the user's charts. See decisions.md
    2026-07-25 12:45."""
    seen = []

    def spy(text, semitones, prefer_flats):  # positional-or-keyword, no default
        seen.append(prefer_flats)
        return text

    monkeypatch.setattr(af, "transpose_label_text", spy)
    af._transposed_labels(_labels((0.0, 1.0, "Am")), 2, True, None)
    af._transposed_labels(_labels((0.0, 1.0, "Am")), 2, False, None)
    assert seen == [True, False]


# --- the orchestrator, on the shared spine ----------------------------------


def _orchestrator(monkeypatch, current, selection=(0.0, 0.0)):
    """Wire up transpose_selected_label_track over fakes; returns the events list."""
    events = []
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)
    monkeypatch.setattr(
        af,
        "read_label_tracks_precisely",
        lambda aup3_path: ({"chords": af._labels_from_txt(current)}, True),
    )
    monkeypatch.setattr(af, "read_time_selection", lambda: selection)
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: events.append("remove"))
    monkeypatch.setattr(
        af,
        "make_label_track_from_file",
        lambda path, name=None: events.append(f"import:{name}"),
    )
    monkeypatch.setattr(af, "get_track_count", lambda: 3)
    monkeypatch.setattr(
        af, "move_track_to", lambda frm, to: events.append(f"move:{frm}->{to}")
    )
    monkeypatch.setattr(af, "select_tracks", lambda idx: events.append(f"select:{idx}"))
    return events


def test_transpose_swaps_the_track_via_the_shared_spine(monkeypatch):
    current = af._format_track_txt([(0.0, 1.0, "Am")])
    events = _orchestrator(monkeypatch, current)

    name, index, content, changed, skipped, _ = af.transpose_selected_label_track(2)

    assert (name, index, changed) == ("chords", 1, True)
    assert content == af._format_track_txt([(0.0, 1.0, "Bm")])
    assert skipped == []
    assert events == ["remove", "import:chords", "move:2->1", "select:[1]"]


def test_the_orchestrator_defaults_to_flats(monkeypatch):
    """A -> Bb, not A#. Uses a chord whose two spellings differ, unlike Am -> Bm."""
    _orchestrator(monkeypatch, af._format_track_txt([(0.0, 1.0, "A")]))
    _, _, content, _, _, _ = af.transpose_selected_label_track(1)
    assert content == af._format_track_txt([(0.0, 1.0, "Bb")])


def test_the_orchestrator_honours_sharps(monkeypatch):
    _orchestrator(monkeypatch, af._format_track_txt([(0.0, 1.0, "A")]))
    _, _, content, _, _, _ = af.transpose_selected_label_track(1, prefer_flats=False)
    assert content == af._format_track_txt([(0.0, 1.0, "A#")])


def test_no_chords_leaves_the_project_untouched(monkeypatch):
    current = af._format_track_txt([(0.0, 1.0, "Guitar Solo")])
    events = _orchestrator(monkeypatch, current)

    _, _, content, changed, skipped, _ = af.transpose_selected_label_track(2)

    assert changed is False
    assert events == [], "a track with no chords must not touch the project"
    assert content == current
    assert skipped == ["Guitar Solo"]


def test_transposing_to_the_same_spelling_skips_the_swap(monkeypatch):
    """Already-flat content transposed by 0 is a no-op, like -q's already-quantized."""
    current = af._format_track_txt([(0.0, 1.0, "Bb")])
    events = _orchestrator(monkeypatch, current)

    _, _, _, changed, _, _ = af.transpose_selected_label_track(0)

    assert changed is False
    assert events == []


def test_force_whole_track_skips_the_selection_read(monkeypatch):
    current = af._format_track_txt([(0.0, 1.0, "Am")])
    _orchestrator(monkeypatch, current)

    def fail():
        raise AssertionError("read_time_selection must not be called with -f")

    monkeypatch.setattr(af, "read_time_selection", fail)
    _, _, content, _, _, _ = af.transpose_selected_label_track(2, whole_track=True)
    assert content == af._format_track_txt([(0.0, 1.0, "Bm")])


def test_selection_read_error_propagates(monkeypatch):
    _orchestrator(monkeypatch, af._format_track_txt([(0.0, 1.0, "Am")]))

    def boom():
        raise af.SelectionReadError("no accessor")

    monkeypatch.setattr(af, "read_time_selection", boom)
    with pytest.raises(af.SelectionReadError):
        af.transpose_selected_label_track(2)


def test_no_selected_label_track_is_an_error(monkeypatch):
    monkeypatch.setattr(af, "get_tracks", lambda: [_track("chords")])
    with pytest.raises(af.LabelTrackError, match="[Ss]elect"):
        af.transpose_selected_label_track(2)


# --- the transpose command --------------------------------------------------


def _dispatched(monkeypatch, argv):
    """Run main() on `argv` with the transpose entry point stubbed out."""
    captured = {}
    monkeypatch.setattr(
        rebuildap,
        "_transpose_open_project",
        lambda *a, **k: captured.update(args=a, kwargs=k),
    )
    monkeypatch.setattr("sys.argv", argv)
    rebuildap.main()
    return captured


def test_transpose_carries_the_semitone_count(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "transpose", "2"])
    assert captured["kwargs"]["semitones"] == 2


def test_negative_semitones_are_accepted(monkeypatch):
    """`transpose -2` must transpose down, not be read as an unknown option.

    argparse allows a leading-minus token as a positional only while no option
    string looks like a negative number; this pins that nothing ever adds one.
    """
    captured = _dispatched(monkeypatch, ["rebuildap", "transpose", "-2"])
    assert captured["kwargs"]["semitones"] == -2


def test_semitones_are_required(monkeypatch):
    """Transposing by nothing is not a default worth having."""
    monkeypatch.setattr("sys.argv", ["rebuildap", "transpose"])
    with pytest.raises(SystemExit) as excinfo:
        rebuildap.main()
    assert excinfo.value.code != 0


def test_sharps_flag_is_passed_through(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "transpose", "2", "-s"])
    assert captured["kwargs"]["sharps"] is True


def test_transpose_with_force_is_allowed(monkeypatch):
    captured = _dispatched(monkeypatch, ["rebuildap", "transpose", "2", "-f"])
    assert captured["kwargs"]["semitones"] == 2
    assert captured["kwargs"]["force"] is True


def test_sharps_belongs_to_transpose_alone(monkeypatch):
    """-s used to need a guard saying it only applies with -t. It now exists
    only under transpose, so no other command can be given it."""
    for argv in (
        ["rebuildap", "export", "-s"],
        ["rebuildap", "quantize", "-s"],
        ["rebuildap", "check", "-s"],
    ):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as excinfo:
            rebuildap.main()
        assert excinfo.value.code != 0


# --- persisting the transposed track to the versioned .txt ------------------


def _stub_transpose(
    monkeypatch,
    stem="song",
    name="chords",
    content="Bb\n",
    changed=True,
    skipped=(),
    open_paths=(),
    seen=None,
    precise=True,
):
    """Fake the live layer for _transpose_open_project, recording the arguments it
    was called with so the CLI-to-orchestrator wiring can be asserted.
    ``open_paths`` is what Audacity holds open; ``seen`` (a dict) receives the
    ``aup3_path`` transpose was handed."""
    calls = []
    monkeypatch.setattr(rebuildap, "prerequisites_met", lambda _command: True)
    monkeypatch.setattr(af, "open_project_stem", lambda *a, **k: stem)
    monkeypatch.setattr(ap, "open_project_paths", lambda: list(open_paths))

    def fake(
        semitones, prefer_flats=True, verbose=False, whole_track=False, aup3_path=None
    ):
        if seen is not None:
            seen["aup3_path"] = aup3_path
        calls.append(
            {
                "semitones": semitones,
                "prefer_flats": prefer_flats,
                "whole_track": whole_track,
            }
        )
        return (name, 1, content, changed, list(skipped), precise)

    monkeypatch.setattr(af, "transpose_selected_label_track", fake)
    return calls


def test_transpose_writes_the_versioned_txt_in_the_project_dir(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    rebuildap._transpose_open_project(2, verbose=False)

    written = tmp_path / "chords_song.txt"
    assert written.read_text() == "Bb\n"
    assert calls == [{"semitones": 2, "prefer_flats": True, "whole_track": False}]
    assert str(written) in capsys.readouterr().out


def test_transpose_passes_sharps_through_to_the_orchestrator(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    rebuildap._transpose_open_project(2, sharps=True, verbose=False)

    assert calls[0]["prefer_flats"] is False


def test_transpose_force_requests_the_whole_track(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    rebuildap._transpose_open_project(2, force=True, verbose=False)

    assert calls[0]["whole_track"] is True


def test_transpose_exits_nonzero_when_there_is_nothing_to_transpose(monkeypatch):
    seen = []
    monkeypatch.setattr(
        rebuildap, "prerequisites_met", lambda command: seen.append(command) or False
    )
    monkeypatch.setattr(
        af,
        "transpose_selected_label_track",
        lambda *a, **k: pytest.fail("must not transpose"),
    )

    with pytest.raises(SystemExit) as excinfo:
        rebuildap._transpose_open_project(2, verbose=False)

    assert excinfo.value.code not in (0, None)
    assert seen == ["transpose"]


def test_transpose_refuses_before_mutating_when_project_dir_unknown(
    monkeypatch, tmp_path
):
    """Resolved before the project is touched, so an unwritable location is a
    clean refusal rather than a transposed-but-unpersisted half-state - and a
    non-zero exit, so a hotkey run is not reported "ok"."""
    monkeypatch.chdir(tmp_path)  # cwd does not hold the project
    calls = _stub_transpose(monkeypatch)
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])

    with pytest.raises(SystemExit, match="Open Recent"):
        rebuildap._transpose_open_project(2, verbose=False)

    assert calls == [], "must not transpose when the .txt cannot be written"


def test_transpose_refuses_before_mutating_when_the_project_is_ambiguous(
    monkeypatch, tmp_path
):
    """Same refusal as -q: naming the .txt after the wrong open project would
    transpose one project's chords into another project's source of truth."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    def ambiguous():
        raise af.ProjectIdentityError("Several projects are open: song, song_G.")

    monkeypatch.setattr(af, "open_project_stem", ambiguous)

    with pytest.raises(SystemExit, match="song_G"):
        rebuildap._transpose_open_project(2, verbose=False)
    assert calls == [], "must not transpose when the target project is unknown"


def test_selection_read_failure_exits_pointing_at_force(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    monkeypatch.setattr(rebuildap, "prerequisites_met", lambda _command: True)
    monkeypatch.setattr(af, "open_project_stem", lambda *a, **k: "song")
    monkeypatch.setattr(ap, "open_project_paths", lambda: [])

    def boom(*a, **k):
        raise af.SelectionReadError("no accessor")

    monkeypatch.setattr(af, "transpose_selected_label_track", boom)
    with pytest.raises(SystemExit, match="-f"):
        rebuildap._transpose_open_project(2, verbose=False)


# --- outcome reporting ------------------------------------------------------


def _outcome(capsys, **kwargs):
    defaults = dict(
        target_name="chords",
        out_path="/tmp/chords_song.txt",
        semitones=2,
        prefer_flats=True,
        changed=True,
        wrote=True,
        skipped=[],
    )
    defaults.update(kwargs)
    rebuildap._report_transpose_outcome(**defaults)
    return capsys.readouterr().out


def test_outcome_names_the_spelling_used(capsys):
    """Required by decisions.md 2026-07-25 12:45: a chart must never come back in
    an unexpected spelling silently."""
    assert "flat" in _outcome(capsys).lower()
    assert "sharp" in _outcome(capsys, prefer_flats=False).lower()


def test_outcome_names_the_semitone_count_with_a_sign(capsys):
    assert "+2" in _outcome(capsys, semitones=2)
    assert "-2" in _outcome(capsys, semitones=-2)


def test_outcome_reports_labels_left_alone(capsys):
    out = _outcome(capsys, skipped=["Guitar Solo", "Verse1"])
    assert "Guitar Solo" in out
    assert "Verse1" in out


def test_outcome_caps_a_long_skipped_list(capsys):
    out = _outcome(capsys, skipped=[f"text{i}" for i in range(40)])
    assert "text0" in out
    assert "more" in out
    assert "text39" not in out, "a 40-item list must not be dumped in full"


def test_outcome_when_nothing_was_a_chord(capsys):
    out = _outcome(capsys, changed=False, wrote=False, skipped=["Verse1"])
    assert "no chords" in out.lower() or "nothing to do" in out.lower()


# --- exact label times ----------------------------------------------------------
#
# transpose never moves a label, but it re-imports the track, and through GetInfo
# every time came back rounded to six significant digits - moving each chord in
# scope by up to half a millisecond off its beat.

BEAT = 100.68907563025209


def _exact_orchestrator(monkeypatch, precise=True, seen=None):
    """transpose_selected_label_track over a chord sitting exactly on BEAT; the
    import records the file it was handed in ``seen["imported"]``."""
    seen = {} if seen is None else seen
    tracks = [_track("song", kind="wave"), _track("chords", selected=True)]
    monkeypatch.setattr(af, "get_tracks", lambda: tracks)

    def read(aup3_path):
        seen["aup3_path"] = aup3_path
        return {"chords": [(BEAT, BEAT, "C")]}, precise

    monkeypatch.setattr(af, "read_label_tracks_precisely", read)
    monkeypatch.setattr(af, "read_time_selection", lambda: (0.0, 0.0))
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: None)

    def fake_import(path, name=None):
        seen["imported"] = Path(path).read_text()

    monkeypatch.setattr(af, "make_label_track_from_file", fake_import)
    monkeypatch.setattr(af, "get_track_count", lambda: 2)
    monkeypatch.setattr(af, "move_track_to", lambda *a: None)
    monkeypatch.setattr(af, "_flush_to_project_file", lambda idx: None)
    monkeypatch.setattr(af, "select_tracks", lambda *a: None)
    return seen


def test_transpose_reimports_every_time_bit_exact(monkeypatch):
    seen = _exact_orchestrator(monkeypatch)

    _, _, content, changed, _, _ = af.transpose_selected_label_track(2)

    assert changed is True
    assert af._labels_from_txt(seen["imported"]) == [(BEAT, BEAT, "D")]
    # The versioned file keeps Audacity's own 6-decimal export format.
    assert content == "100.689076\t100.689076\tD\n"


def test_transpose_passes_the_project_file_to_the_label_read(monkeypatch):
    seen = _exact_orchestrator(monkeypatch)
    project = Path("/x/song.aup3")

    af.transpose_selected_label_track(2, aup3_path=project)

    assert seen["aup3_path"] == project


@pytest.mark.parametrize("precise", [True, False])
def test_transpose_says_whether_its_label_read_was_exact(monkeypatch, precise):
    _exact_orchestrator(monkeypatch, precise=precise)
    assert af.transpose_selected_label_track(2)[-1] is precise


def _transpose_in_cwd(monkeypatch, tmp_path, existing=None, **stub):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    out = tmp_path / "chords_song.txt"
    if existing is not None:
        out.write_text(existing)
    seen = {}
    _stub_transpose(monkeypatch, stem="song", seen=seen, **stub)
    rebuildap._transpose_open_project(2, verbose=False)
    return seen, out


def test_transpose_is_handed_the_open_file_of_the_project(monkeypatch, tmp_path):
    open_file = Path("/Volumes/SSD/song/song.aup3")
    seen, _ = _transpose_in_cwd(
        monkeypatch, tmp_path, open_paths=[open_file, open_file]
    )
    assert seen["aup3_path"] == open_file


def test_a_fallback_transpose_keeps_the_files_exact_times(monkeypatch, tmp_path):
    """GetInfo showed 100.689; the file knows 100.689076. Only the text is news."""
    _, out = _transpose_in_cwd(
        monkeypatch,
        tmp_path,
        existing="100.689076\t100.689076\tC\n",
        content="100.689000\t100.689000\tD\n",
        precise=False,
    )
    assert out.read_text() == "100.689076\t100.689076\tD\n"


@pytest.mark.parametrize(
    "existing",
    [
        "100.5\t100.5\tC\n",  # a time that does not round to what GetInfo showed
        "100.689076\t100.689076\tC\n5.0\t5.0\tE\n",  # another label
    ],
)
def test_a_fallback_transpose_takes_getinfos_times_when_the_file_does_not_match(
    monkeypatch, tmp_path, existing
):
    _, out = _transpose_in_cwd(
        monkeypatch,
        tmp_path,
        existing=existing,
        content="100.689000\t100.689000\tD\n",
        precise=False,
    )
    assert out.read_text() == "100.689000\t100.689000\tD\n"


def test_an_exact_transpose_writes_its_own_times(monkeypatch, tmp_path):
    """With an exact read the orchestrator's times are the truth, file or not."""
    _, out = _transpose_in_cwd(
        monkeypatch,
        tmp_path,
        existing="100.689076\t100.689076\tC\n",
        content="100.689000\t100.689000\tD\n",
        precise=True,
    )
    assert out.read_text() == "100.689000\t100.689000\tD\n"
