"""Tests for the pure helpers in ``audacity_funcs`` that need no live Audacity.

These were stranded in the repo-root ``test.py``, which pytest never collected
because its name does not match the ``test_*.py`` glob. They exercise plain
functions over paths, so they belong with the rest of the offline suite; the
tests that actually drive Audacity live in ``test_audacity_live.py``.
"""

from pathlib import Path

import pytest

from rebuildap import audacity_funcs as af


@pytest.mark.parametrize(
    "identifiers, expected",
    [
        # Basic ordering with one match per identifier
        (
            ["part_001", "chord_A", "lyrics_01", "bar_10", "beat_100"],
            ["part_001", "chord_A", "lyrics_01", "bar_10", "beat_100"],
        ),
        # No matches, so unrecognized items go before bars and beats
        (
            ["alpha", "beta", "gamma", "bar_10", "beat_100"],
            ["alpha", "beta", "gamma", "bar_10", "beat_100"],
        ),
        # Bars and beats go last even when others are recognized
        (
            ["chord_A", "lyrics_01", "random", "beat_100", "bar_10"],
            ["chord_A", "lyrics_01", "random", "bar_10", "beat_100"],
        ),
        # Unrecognized still sorts before bars and beats
        (
            ["part_001", "random_label", "beat_100", "bar_10"],
            ["part_001", "random_label", "bar_10", "beat_100"],
        ),
        # No recognized labels at all, bars and beats still last
        (
            ["random_01", "random_02", "bar_10", "beat_100"],
            ["random_01", "random_02", "bar_10", "beat_100"],
        ),
        # Only bars and beats, bar before beat
        (["beat_100", "bar_10"], ["bar_10", "beat_100"]),
        # Mixed recognized and unrecognized
        (
            ["beat_100", "part_002", "chord_B", "random_label", "bar_10"],
            ["part_002", "chord_B", "random_label", "bar_10", "beat_100"],
        ),
    ],
)
def test_reorder_labels(identifiers, expected):
    assert af.reorder_labels([Path(p) for p in identifiers]) == [
        Path(p) for p in expected
    ]


def test_is_audacity_project():
    assert af.is_audacity_project(Path("bla.aup3"))


def test_is_not_audacity_project():
    assert not af.is_audacity_project(Path("bla.mp3"))


# --- refusing to open an already-open project ------------------------------
#
# Opening a project that is already open raises a modal "Error Opening Project"
# dialog that blocks OpenProject2 until acknowledged. Dismissing it is not an
# option: Audacity exited immediately the one time it was clicked via
# osascript. So open_project must notice the condition beforehand and send
# nothing at all — the assertion that matters below is that the pipe is never
# touched, since a sent command is what triggers the dialog.


def _spy_pipe(monkeypatch):
    """Record every pipe call open_project makes, without making any."""
    calls = []
    monkeypatch.setattr(af, "ping_pipe", lambda *a, **k: calls.append("ping"))
    monkeypatch.setattr(af, "_pa_do_timed", lambda cmd, _t: calls.append(cmd) or "OK")
    return calls


def _fake_already_open(monkeypatch, is_open):
    from rebuildap import audacity_present as ap

    monkeypatch.setattr(ap, "project_window_open", lambda _stem: is_open)


def test_open_project_sends_nothing_when_the_project_is_already_open(monkeypatch):
    calls = _spy_pipe(monkeypatch)
    _fake_already_open(monkeypatch, True)

    with pytest.raises(af.ProjectAlreadyOpenError):
        af.open_project(Path("/tmp/angie.aup3"))

    assert calls == []


def test_refusal_names_the_project_so_the_user_can_act(monkeypatch):
    _spy_pipe(monkeypatch)
    _fake_already_open(monkeypatch, True)

    with pytest.raises(af.ProjectAlreadyOpenError, match="angie"):
        af.open_project(Path("/tmp/angie.aup3"))


def test_open_project_proceeds_normally_when_not_already_open(monkeypatch):
    calls = _spy_pipe(monkeypatch)
    _fake_already_open(monkeypatch, False)

    af.open_project(Path("/tmp/angie.aup3"))

    assert any("OpenProject2:" in c and "angie.aup3" in c for c in calls)


def test_timeout_points_at_the_already_open_dialog(monkeypatch):
    """The check can be raced: the user may open the project just after it runs.

    That path still times out, so the message has to name the likely cause —
    otherwise it is indistinguishable from a wedged pipe.
    """
    monkeypatch.setattr(af, "ping_pipe", lambda *a, **k: None)

    def timeout(_cmd, _t):
        raise TimeoutError("Audacity scripting pipe did not respond")

    monkeypatch.setattr(af, "_pa_do_timed", timeout)
    _fake_already_open(monkeypatch, False)

    with pytest.raises(TimeoutError, match="already open"):
        af.open_project(Path("/tmp/angie.aup3"))
