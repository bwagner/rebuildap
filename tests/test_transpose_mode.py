"""Offline tests for the ``-t`` transpose-in-place mode.

``rebuildap -t N`` transposes the chords in the *selected* label track of the
open project by N half steps, scoped to the current time selection, and updates
the versioned ``.txt``. It rides the same spine as ``-q``
(:func:`resolve_selected_label_track`, :func:`resolve_selection_scope`,
:func:`replace_label_track`), so only what is specific to transposing is tested
here: which labels are in scope, what happens to text that is not a chord, the
accidental spelling, and the CLI guards.
"""

import pytest

import rebuildap
from rebuildap import audacity_funcs as af


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
        af, "get_label_tracks_content_via_getinfo", lambda: {"chords": current}
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

    name, index, content, changed, skipped = af.transpose_selected_label_track(2)

    assert (name, index, changed) == ("chords", 1, True)
    assert content == af._format_track_txt([(0.0, 1.0, "Bm")])
    assert skipped == []
    assert events == ["remove", "import:chords", "move:2->1", "select:[1]"]


def test_the_orchestrator_defaults_to_flats(monkeypatch):
    """A -> Bb, not A#. Uses a chord whose two spellings differ, unlike Am -> Bm."""
    _orchestrator(monkeypatch, af._format_track_txt([(0.0, 1.0, "A")]))
    _, _, content, _, _ = af.transpose_selected_label_track(1)
    assert content == af._format_track_txt([(0.0, 1.0, "Bb")])


def test_the_orchestrator_honours_sharps(monkeypatch):
    _orchestrator(monkeypatch, af._format_track_txt([(0.0, 1.0, "A")]))
    _, _, content, _, _ = af.transpose_selected_label_track(1, prefer_flats=False)
    assert content == af._format_track_txt([(0.0, 1.0, "A#")])


def test_no_chords_leaves_the_project_untouched(monkeypatch):
    current = af._format_track_txt([(0.0, 1.0, "Guitar Solo")])
    events = _orchestrator(monkeypatch, current)

    _, _, content, changed, skipped = af.transpose_selected_label_track(2)

    assert changed is False
    assert events == [], "a track with no chords must not touch the project"
    assert content == current
    assert skipped == ["Guitar Solo"]


def test_transposing_to_the_same_spelling_skips_the_swap(monkeypatch):
    """Already-flat content transposed by 0 is a no-op, like -q's already-quantized."""
    current = af._format_track_txt([(0.0, 1.0, "Bb")])
    events = _orchestrator(monkeypatch, current)

    _, _, _, changed, _ = af.transpose_selected_label_track(0)

    assert changed is False
    assert events == []


def test_force_whole_track_skips_the_selection_read(monkeypatch):
    current = af._format_track_txt([(0.0, 1.0, "Am")])
    _orchestrator(monkeypatch, current)

    def fail():
        raise AssertionError("read_time_selection must not be called with -f")

    monkeypatch.setattr(af, "read_time_selection", fail)
    _, _, content, _, _ = af.transpose_selected_label_track(2, whole_track=True)
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


# --- CLI parsing and guards -------------------------------------------------


def _captured_rebuild(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        rebuildap, "rebuild", lambda *a, **k: captured.update(args=a, kwargs=k)
    )
    return captured


def test_t_flag_carries_the_semitone_count(monkeypatch):
    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr("sys.argv", ["rebuildap", "-t", "2"])
    rebuildap.main()
    assert captured["kwargs"]["transpose"] == 2


def test_negative_semitones_are_accepted(monkeypatch):
    """`-t -2` must transpose down, not be read as an unknown option."""
    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr("sys.argv", ["rebuildap", "-t", "-2"])
    rebuildap.main()
    assert captured["kwargs"]["transpose"] == -2


def test_no_t_flag_leaves_transpose_off(monkeypatch):
    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr("sys.argv", ["rebuildap"])
    rebuildap.main()
    assert captured["kwargs"]["transpose"] is None


def test_sharps_flag_is_passed_through(monkeypatch):
    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr("sys.argv", ["rebuildap", "-t", "2", "-s"])
    rebuildap.main()
    assert captured["kwargs"]["sharps"] is True


def test_t_with_force_is_allowed(monkeypatch):
    captured = _captured_rebuild(monkeypatch)
    monkeypatch.setattr("sys.argv", ["rebuildap", "-t", "2", "-f"])
    rebuildap.main()
    assert captured["kwargs"]["transpose"] == 2
    assert captured["kwargs"]["force"] is True


@pytest.mark.parametrize(
    "argv",
    [
        ["rebuildap", "-t", "2", "song.opus"],
        ["rebuildap", "-t", "2", "-c"],
        ["rebuildap", "-t", "2", "-l"],
    ],
)
def test_transpose_rejects_a_filename_check_or_label(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit):
        rebuildap.main()


def test_transpose_and_quantize_together_are_rejected(monkeypatch):
    """One transform per run -- they would fight over the same track."""
    monkeypatch.setattr("sys.argv", ["rebuildap", "-t", "2", "-q"])
    with pytest.raises(SystemExit):
        rebuildap.main()


def test_sharps_without_transpose_is_rejected(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rebuildap", "-s"])
    with pytest.raises(SystemExit):
        rebuildap.main()


# --- persisting the transposed track to the versioned .txt ------------------


def _stub_transpose(
    monkeypatch, stem="song", name="chords", content="Bb\n", changed=True, skipped=()
):
    """Fake the live layer for _transpose_open_project, recording the arguments it
    was called with so the CLI-to-orchestrator wiring can be asserted."""
    calls = []
    monkeypatch.setattr(rebuildap, "prerequisites_met", lambda: True)
    monkeypatch.setattr(af, "open_project_stem", lambda *a, **k: stem)

    def fake(semitones, prefer_flats=True, verbose=False, whole_track=False):
        calls.append(
            {
                "semitones": semitones,
                "prefer_flats": prefer_flats,
                "whole_track": whole_track,
            }
        )
        return (name, 1, content, changed, list(skipped))

    monkeypatch.setattr(af, "transpose_selected_label_track", fake)
    return calls


def test_transpose_writes_the_versioned_txt_in_the_project_dir(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    rebuildap.rebuild(transpose=2)

    written = tmp_path / "chords_song.txt"
    assert written.read_text() == "Bb\n"
    assert calls == [{"semitones": 2, "prefer_flats": True, "whole_track": False}]
    assert str(written) in capsys.readouterr().out


def test_transpose_passes_sharps_through_to_the_orchestrator(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    rebuildap.rebuild(transpose=2, sharps=True)

    assert calls[0]["prefer_flats"] is False


def test_transpose_force_requests_the_whole_track(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    calls = _stub_transpose(monkeypatch)

    rebuildap.rebuild(transpose=2, force=True)

    assert calls[0]["whole_track"] is True


def test_transpose_refuses_before_mutating_when_project_dir_unknown(
    monkeypatch, tmp_path, capsys
):
    """Resolved before the project is touched, so an unwritable location is a
    clean refusal rather than a transposed-but-unpersisted half-state."""
    monkeypatch.chdir(tmp_path)  # cwd does not hold the project
    calls = _stub_transpose(monkeypatch)
    monkeypatch.setattr(af, "find_recent_project_dirs", lambda _s: [])

    rebuildap.rebuild(transpose=2)

    assert calls == [], "must not transpose when the .txt cannot be written"
    assert "Open Recent" in capsys.readouterr().err


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
        rebuildap.rebuild(transpose=2)
    assert calls == [], "must not transpose when the target project is unknown"


def test_selection_read_failure_exits_pointing_at_force(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.aup3").write_text("")
    monkeypatch.setattr(rebuildap, "prerequisites_met", lambda: True)
    monkeypatch.setattr(af, "open_project_stem", lambda *a, **k: "song")

    def boom(*a, **k):
        raise af.SelectionReadError("no accessor")

    monkeypatch.setattr(af, "transpose_selected_label_track", boom)
    with pytest.raises(SystemExit, match="-f"):
        rebuildap.rebuild(transpose=2)


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
