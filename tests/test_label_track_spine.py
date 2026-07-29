"""Offline tests for the shared in-place label-track spine.

The modes that rewrite one label track in the open project (``-q`` today) share
three steps around their own computation: resolve the single selected label
track, resolve how much of it the time selection covers, and swap the new
content in. Those three are tested here directly, rather than only through the
mode that happens to use them, so the next mode inherits the coverage.
"""

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from rebuildap import audacity_funcs as af


def _track(name, kind="label", selected=False):
    return {"name": name, "kind": kind, "selected": 1 if selected else 0}


# --- resolving the single selected label track ------------------------------


def test_resolves_the_sole_selected_label_track():
    tracks = [
        _track("song", kind="wave"),
        _track("chords", selected=True),
        _track("beats"),
    ]
    assert af.resolve_selected_label_track(tracks) == (1, "chords")


def test_a_selected_audio_track_does_not_count_as_the_target():
    """Only *label* tracks are candidates -- a selected audio track is not one."""
    tracks = [_track("song", kind="wave", selected=True), _track("chords")]
    with pytest.raises(af.LabelTrackError, match="none is selected"):
        af.resolve_selected_label_track(tracks)


def test_no_selected_label_track_is_an_error():
    with pytest.raises(af.LabelTrackError, match="[Ss]elect"):
        af.resolve_selected_label_track([_track("chords"), _track("beats")])


def test_several_selected_label_tracks_are_named_in_the_error():
    tracks = [_track("chords", selected=True), _track("parts", selected=True)]
    with pytest.raises(af.LabelTrackError) as excinfo:
        af.resolve_selected_label_track(tracks)
    # Naming them is the point: the user has to know which to deselect.
    assert "chords" in str(excinfo.value)
    assert "parts" in str(excinfo.value)


# --- resolving the selection scope ------------------------------------------


def test_whole_track_returns_no_selection_without_reading_it(monkeypatch):
    """-f must skip the Nyquist read entirely, not just discard its result."""

    def fail():
        raise AssertionError("read_time_selection must not be called for -f")

    monkeypatch.setattr(af, "read_time_selection", fail)
    assert af.resolve_selection_scope(whole_track=True) is None


def test_a_real_region_is_returned_as_is(monkeypatch):
    monkeypatch.setattr(af, "read_time_selection", lambda: (1.5, 4.25))
    assert af.resolve_selection_scope() == (1.5, 4.25)


def test_a_bare_cursor_falls_back_to_the_whole_track(monkeypatch):
    """A click without a drag is a zero-width 'region' and means the whole track."""
    monkeypatch.setattr(af, "read_time_selection", lambda: (2.0, 2.0))
    assert af.resolve_selection_scope() is None


def test_selection_read_error_propagates(monkeypatch):
    """The caller resolves scope before mutating, so this must abort the run."""

    def boom():
        raise af.SelectionReadError("no accessor")

    monkeypatch.setattr(af, "read_time_selection", boom)
    with pytest.raises(af.SelectionReadError):
        af.resolve_selection_scope()


# --- swapping the new content in --------------------------------------------


def _record_swap(monkeypatch, track_count=3):
    """Capture the swap primitives in call order, returning the events list."""
    events = []
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: events.append("remove"))
    monkeypatch.setattr(
        af,
        "make_label_track_from_file",
        lambda path, name=None: events.append(
            f"import:{name}:{Path(path).read_text()}"
        ),
    )
    monkeypatch.setattr(af, "get_track_count", lambda: track_count)
    monkeypatch.setattr(
        af, "move_track_to", lambda frm, to: events.append(f"move:{frm}->{to}")
    )
    monkeypatch.setattr(af, "select_tracks", lambda idx: events.append(f"select:{idx}"))
    return events


def test_swap_runs_in_the_safe_order(monkeypatch):
    """The dangerous invariant: remove the old track, import the new one, then
    move it back to the old index, then re-select it."""
    events = _record_swap(monkeypatch)

    changed = af.replace_label_track(1, "chords", "new\n", "old\n")

    assert changed is True
    assert events == [
        "remove",
        "import:chords:new\n",
        "move:2->1",  # re-import lands at the bottom (3 tracks -> index 2)
        "select:[1]",
    ]


def test_identical_content_skips_the_swap_entirely(monkeypatch):
    """No mtime bump and no undo churn when nothing actually changed."""
    events = _record_swap(monkeypatch)

    changed = af.replace_label_track(1, "chords", "same\n", "same\n")

    assert changed is False
    assert events == [], "unchanged content must not touch the project"


def test_the_temp_file_is_removed_after_the_swap(monkeypatch):
    """The imported bytes go through a throwaway temp; it must not be left behind."""
    seen = []
    monkeypatch.setattr(af, "remove_selected_tracks", lambda: None)
    monkeypatch.setattr(
        af,
        "make_label_track_from_file",
        lambda path, name=None: seen.append(Path(path)),
    )
    monkeypatch.setattr(af, "get_track_count", lambda: 1)
    monkeypatch.setattr(af, "move_track_to", lambda frm, to: None)
    monkeypatch.setattr(af, "select_tracks", lambda idx: None)

    af.replace_label_track(0, "chords", "new\n", "old\n")

    assert seen and not seen[0].exists()


def test_the_temp_file_is_removed_even_when_the_import_fails(monkeypatch):
    events = _record_swap(monkeypatch)
    seen = []

    def failing_import(path, name=None):
        seen.append(Path(path))
        raise RuntimeError("import blew up")

    monkeypatch.setattr(af, "make_label_track_from_file", failing_import)

    with pytest.raises(RuntimeError, match="import blew up"):
        af.replace_label_track(0, "chords", "new\n", "old\n")

    assert seen and not seen[0].exists()
    assert events == ["remove"]


# --- recovering from the no-region modal -------------------------------------
#
# NyquistPrompt refuses when there is no time region, raising a modal that wedges
# the pipe. The region cannot be read in advance by any known channel (measured
# 2026-07-29), so the refusal IS the answer: no region means the whole track.
# The one thing that must never happen is letting the timeout unwind the process
# while the modal is still up -- the client hangup, not the dialog, is what has
# killed Audacity five times.


def _raise_timeout():
    raise TimeoutError("pa.do(...) did not return within 15.0s")


def test_no_region_modal_is_dismissed_and_read_as_whole_track(monkeypatch):
    """The refusal answers the question the region cannot be queried for."""
    dismissed = []
    monkeypatch.setattr(af, "read_time_selection", _raise_timeout)
    monkeypatch.setattr(af, "_no_region_dialog_present", lambda: True)
    monkeypatch.setattr(
        af, "_dismiss_no_region_dialog", lambda: dismissed.append(1) or True
    )
    assert af.resolve_selection_scope() is None
    assert dismissed == [1]


def test_timeout_without_our_dialog_refuses_and_clicks_nothing(monkeypatch):
    """Some other modal, or none at all: refuse rather than click blind."""
    clicks = []
    monkeypatch.setattr(af, "read_time_selection", _raise_timeout)
    monkeypatch.setattr(af, "_no_region_dialog_present", lambda: False)
    monkeypatch.setattr(
        af, "_dismiss_no_region_dialog", lambda: clicks.append(1) or True
    )
    with pytest.raises(af.SelectionReadError):
        af.resolve_selection_scope()
    assert clicks == []


def test_a_dialog_that_will_not_dismiss_is_an_error_not_a_whole_track_run(monkeypatch):
    """Failing to clear it must not be read as 'no region' -- the pipe is still wedged."""
    monkeypatch.setattr(af, "read_time_selection", _raise_timeout)
    monkeypatch.setattr(af, "_no_region_dialog_present", lambda: True)
    monkeypatch.setattr(af, "_dismiss_no_region_dialog", lambda: False)
    with pytest.raises(af.SelectionReadError):
        af.resolve_selection_scope()


def test_whole_track_never_looks_for_the_modal(monkeypatch):
    """-f skips the read, so there is no refusal to recover from."""

    def fail():
        raise AssertionError("must not probe for a dialog when -f skipped the read")

    monkeypatch.setattr(af, "read_time_selection", _raise_timeout)
    monkeypatch.setattr(af, "_no_region_dialog_present", fail)
    assert af.resolve_selection_scope(whole_track=True) is None


# --- the watcher: clear the refusal while the read is still in flight ---------
#
# Waiting for the 15s timeout meant the dialog sat on screen for the whole
# budget, and recovery then ran through _pa_do_timed's abandoned-thread path,
# which is documented to eat the next call's response. Watching concurrently
# clears it in ~0.2s and keeps the command on its normal path.


@contextmanager
def _watcher(fired: bool):
    ev = threading.Event()
    if fired:
        ev.set()
    yield ev


def test_watcher_firing_means_no_region_even_though_the_read_failed(monkeypatch):
    monkeypatch.setattr(af, "_dismissing_no_region_dialog", lambda: _watcher(True))
    monkeypatch.setattr(af, "read_time_selection", _raise_timeout)
    assert af.resolve_selection_scope() is None


def test_watcher_firing_covers_a_selection_read_error_too(monkeypatch):
    """Dismissed mid-flight, Nyquist never ran, so no file was written."""

    def boom():
        raise af.SelectionReadError("Nyquist did not report the selection (got '')")

    monkeypatch.setattr(af, "_dismissing_no_region_dialog", lambda: _watcher(True))
    monkeypatch.setattr(af, "read_time_selection", boom)
    assert af.resolve_selection_scope() is None


def test_watcher_idle_leaves_a_normal_read_untouched(monkeypatch):
    monkeypatch.setattr(af, "_dismissing_no_region_dialog", lambda: _watcher(False))
    monkeypatch.setattr(af, "read_time_selection", lambda: (1.5, 4.25))
    assert af.resolve_selection_scope() == (1.5, 4.25)


def test_watcher_idle_and_read_failed_still_refuses(monkeypatch):
    """No dialog of ours: this is some other failure and must not go whole-track."""
    monkeypatch.setattr(af, "_dismissing_no_region_dialog", lambda: _watcher(False))
    monkeypatch.setattr(af, "read_time_selection", _raise_timeout)
    monkeypatch.setattr(af, "_no_region_dialog_present", lambda: False)
    with pytest.raises(af.SelectionReadError):
        af.resolve_selection_scope()
