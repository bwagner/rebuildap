"""Tests for audacity_present that need no running Audacity.

The interesting failure modes here are environmental (no scripting module, no
Accessibility permission, ambiguous window titles), so each test fakes the
environment rather than driving the real app.
"""

import subprocess

import pytest

from rebuildap import audacity_present as ap


def _fake_window_names(monkeypatch, sequence):
    """Make audacity_window_names() return each list in turn, repeating the last."""
    calls = {"i": 0}

    def fake():
        i = min(calls["i"], len(sequence) - 1)
        calls["i"] += 1
        return sequence[i]

    monkeypatch.setattr(ap, "audacity_window_names", fake)


def _fake_osascript(monkeypatch, returncode=0, stdout="", stderr=""):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", fake_run)


# --- new-window detection -------------------------------------------------


def test_second_untitled_project_counts_as_a_new_window(monkeypatch):
    """Two empty projects share the title 'Audacity', so counts must be compared.

    Comparing title sets alone reports no new window here.
    """
    _fake_window_names(monkeypatch, [["Audacity"], ["Audacity", "Audacity"]])
    assert ap.wait_for_new_audacity_window(["Audacity"], timeout=5.0) is True


def test_new_title_alongside_a_dialog_is_detected(monkeypatch):
    _fake_window_names(
        monkeypatch, [["About Audacity"], ["About Audacity", "Untitled"]]
    )
    assert ap.wait_for_new_audacity_window(["About Audacity"], timeout=5.0) is True


def test_unchanged_windows_report_no_new_window(monkeypatch):
    _fake_window_names(monkeypatch, [["Audacity"]])
    assert ap.wait_for_new_audacity_window(["Audacity"], timeout=0.5) is False


def test_window_closing_is_not_mistaken_for_a_new_window(monkeypatch):
    _fake_window_names(monkeypatch, [["Audacity", "About Audacity"], ["Audacity"]])
    assert (
        ap.wait_for_new_audacity_window(["Audacity", "About Audacity"], timeout=0.5)
        is False
    )


# --- window-name parsing --------------------------------------------------


def test_window_names_are_split_and_stripped(monkeypatch):
    _fake_osascript(monkeypatch, stdout="Audacity, About Audacity\n")
    assert ap.audacity_window_names() == ["Audacity", "About Audacity"]


def test_no_windows_yields_empty_list(monkeypatch):
    _fake_osascript(monkeypatch, stdout="\n")
    assert ap.audacity_window_names() == []


def test_osascript_failure_yields_empty_list(monkeypatch):
    _fake_osascript(monkeypatch, returncode=1, stderr="boom")
    assert ap.audacity_window_names() == []


# --- osascript failure reporting ------------------------------------------


def test_failing_osascript_is_reported_not_swallowed(monkeypatch, capsys):
    _fake_osascript(monkeypatch, returncode=1, stderr="something went wrong")
    ok, out = ap.run_osascript("whatever", "doing a thing")
    assert (ok, out) == (False, "")
    assert "doing a thing" in capsys.readouterr().err


def test_missing_accessibility_permission_gets_an_actionable_hint(monkeypatch, capsys):
    _fake_osascript(
        monkeypatch,
        returncode=1,
        stderr="osascript is not allowed assistive access. (-1719)",
    )
    ok, _ = ap.run_osascript("whatever", "sending a keystroke")
    assert ok is False
    assert "Accessibility" in capsys.readouterr().err


def test_keystroke_helpers_report_failure(monkeypatch):
    _fake_osascript(monkeypatch, returncode=1, stderr="nope")
    assert ap.bring_audacity_window_to_front_as() is False
    assert ap.close_audacity_window_as() is False


# --- mod-script-pipe availability -----------------------------------------


def test_script_pipe_exists_requires_both_fifos(monkeypatch, tmp_path):
    to_pipe, from_pipe = tmp_path / "to", tmp_path / "from"
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_TO", str(to_pipe))
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_FROM", str(from_pipe))
    assert ap.script_pipe_exists() is False

    to_pipe.touch()
    assert ap.script_pipe_exists() is False, "one FIFO alone is not enough"

    from_pipe.touch()
    assert ap.script_pipe_exists() is True


def test_absent_fifos_raise_an_actionable_error_not_a_timeout(monkeypatch, tmp_path):
    """A disabled module can't be waited out, so say so instead of timing out."""
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_TO", str(tmp_path / "to"))
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_FROM", str(tmp_path / "from"))
    monkeypatch.setattr(ap, "SCRIPT_PIPE_GRACE", 0.2)

    with pytest.raises(ap.ScriptPipeUnavailableError, match="mod-script-pipe"):
        ap.wait_for_audacity_ready(timeout=10.0)


def test_fifo_check_gives_startup_a_grace_period(monkeypatch, tmp_path):
    """The FIFOs appear during startup, so don't bail on the first poll."""
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_TO", str(tmp_path / "to"))
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_FROM", str(tmp_path / "from"))
    monkeypatch.setattr(ap, "SCRIPT_PIPE_GRACE", 30.0)

    # Grace not yet elapsed, so this is a plain timeout, not "module disabled".
    with pytest.raises(TimeoutError):
        ap.wait_for_audacity_ready(timeout=1.0)
