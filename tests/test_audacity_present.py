"""Tests for audacity_present that need no running Audacity.

The interesting failure modes here are environmental (no scripting module, no
Accessibility permission, ambiguous window titles), so each test fakes the
environment rather than driving the real app.
"""

import subprocess
import time

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


# --- already-open detection -----------------------------------------------
#
# Opening a project that is already open raises a modal "Error Opening Project"
# dialog that blocks OpenProject2 the way the format-upgrade dialog does. Unlike
# that one it is *preventable*: Audacity titles a project window with its .aup3
# stem, so the condition is visible before the command is sent. Clicking this
# dialog is the one dismissal with live evidence against it (Audacity exited),
# hence detection rather than dismissal.


def test_project_window_open_matches_its_aup3_stem(monkeypatch):
    _fake_window_names(monkeypatch, [["Audacity", "angie"]])
    assert ap.project_window_open("angie") is True


def test_a_different_project_does_not_count_as_open(monkeypatch):
    _fake_window_names(monkeypatch, [["Audacity", "brown_sugar"]])
    assert ap.project_window_open("angie") is False


def test_no_windows_means_not_open(monkeypatch):
    _fake_window_names(monkeypatch, [[]])
    assert ap.project_window_open("angie") is False


def test_empty_untitled_projects_do_not_match_a_stem(monkeypatch):
    """Every empty project window is titled 'Audacity' — never a real stem."""
    _fake_window_names(monkeypatch, [["Audacity", "Audacity"]])
    assert ap.project_window_open("angie") is False


def test_stem_match_is_exact_not_substring(monkeypatch):
    """'angie' must not be reported open by a window titled 'angie_live'."""
    _fake_window_names(monkeypatch, [["angie_live"]])
    assert ap.project_window_open("angie") is False


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


# --- window ownership -----------------------------------------------------
#
# Cmd-W closes whatever is frontmost, not the window rebuildap opened, so
# closing blind can destroy a user's unsaved project and leave a "Save
# changes?" dialog that wedges the scripting pipe. Every test below pins one
# rule: we close only a window we can positively identify as ours.
#
# Titles observed on 3.7.8: a saved/opened project window is titled with the
# .aup3 stem, while every empty project window is titled "Audacity" — hence
# the ambiguity cases.


def _fake_front_window(monkeypatch, sequence):
    """Make frontmost_audacity_window_name() return each value in turn."""
    calls = {"i": 0}

    def fake():
        i = min(calls["i"], len(sequence) - 1)
        calls["i"] += 1
        return sequence[i]

    monkeypatch.setattr(ap, "frontmost_audacity_window_name", fake)


def _record_calls(monkeypatch, name, result=True):
    """Replace ap.<name> with a recorder; returns the list of call args."""
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(ap, name, fake)
    return calls


def test_closes_when_our_window_is_already_frontmost(monkeypatch):
    _fake_window_names(monkeypatch, [["a_lucky_guy"]])
    _fake_front_window(monkeypatch, ["a_lucky_guy"])
    raises = _record_calls(monkeypatch, "raise_audacity_window_as")
    closes = _record_calls(monkeypatch, "close_audacity_window_as")

    assert ap.close_owned_window("a_lucky_guy") is True
    assert len(closes) == 1
    assert raises == [], "no need to raise a window that is already frontmost"


def test_raises_our_window_before_closing_when_focus_moved(monkeypatch):
    """The user clicked another window: raise ours, verify, then close."""
    _fake_window_names(monkeypatch, [["a_lucky_guy", "someone_elses_song"]])
    _fake_front_window(monkeypatch, ["someone_elses_song", "a_lucky_guy"])
    raises = _record_calls(monkeypatch, "raise_audacity_window_as")
    closes = _record_calls(monkeypatch, "close_audacity_window_as")

    assert ap.close_owned_window("a_lucky_guy") is True
    assert [c[0][0] for c in raises] == ["a_lucky_guy"]
    assert len(closes) == 1


def test_refuses_to_close_when_our_window_cannot_be_raised(monkeypatch):
    """AXRaise silently failed — closing now would hit the user's window."""
    _fake_window_names(monkeypatch, [["a_lucky_guy", "someone_elses_song"]])
    _fake_front_window(monkeypatch, ["someone_elses_song", "someone_elses_song"])
    _record_calls(monkeypatch, "raise_audacity_window_as")
    closes = _record_calls(monkeypatch, "close_audacity_window_as")

    assert ap.close_owned_window("a_lucky_guy") is False
    assert closes == [], "must not send Cmd-W to a window we do not own"


def test_refuses_to_close_an_ambiguous_title(monkeypatch):
    """Two windows share our title, so 'ours' is not identifiable."""
    _fake_window_names(monkeypatch, [["Audacity", "Audacity"]])
    _fake_front_window(monkeypatch, ["Audacity"])
    closes = _record_calls(monkeypatch, "close_audacity_window_as")

    assert ap.close_owned_window("Audacity") is False
    assert closes == []


def test_refuses_to_close_when_our_window_is_gone(monkeypatch):
    """User already closed it; a blind Cmd-W would hit whatever replaced it."""
    _fake_window_names(monkeypatch, [["someone_elses_song"]])
    _fake_front_window(monkeypatch, ["someone_elses_song"])
    closes = _record_calls(monkeypatch, "close_audacity_window_as")

    assert ap.close_owned_window("a_lucky_guy") is False
    assert closes == []


def test_refusal_explains_itself(monkeypatch, capsys):
    _fake_window_names(monkeypatch, [["Audacity", "Audacity"]])
    _fake_front_window(monkeypatch, ["Audacity"])
    _record_calls(monkeypatch, "close_audacity_window_as")

    ap.close_owned_window("Audacity", verbose=True)
    err = capsys.readouterr().err.lower()
    assert "audacity" in err and "left open" in err


def test_refuses_to_close_when_frontmost_is_unreadable(monkeypatch):
    """osascript failed, so we cannot confirm ownership — do nothing."""
    _fake_window_names(monkeypatch, [["a_lucky_guy"]])
    _fake_front_window(monkeypatch, [None, None])
    _record_calls(monkeypatch, "raise_audacity_window_as")
    closes = _record_calls(monkeypatch, "close_audacity_window_as")

    assert ap.close_owned_window("a_lucky_guy") is False
    assert closes == []


# --- frontmost-window parsing ---------------------------------------------


def test_frontmost_window_name_is_stripped(monkeypatch):
    _fake_osascript(monkeypatch, stdout="a_lucky_guy\n")
    assert ap.frontmost_audacity_window_name() == "a_lucky_guy"


def test_frontmost_window_name_is_none_when_there_are_no_windows(monkeypatch):
    """System Events raises 'Invalid index' rather than returning empty."""
    _fake_osascript(
        monkeypatch, returncode=1, stderr="Can’t get window 1. Invalid index. (-1719)"
    )
    assert ap.frontmost_audacity_window_name() is None


# --- format-upgrade dialog ------------------------------------------------
#
# Opening an .aup3 saved by an older Audacity raises a modal "Project update
# required" dialog that blocks OpenProject2 until acknowledged. The dialog has
# no window title, so it is matched on its static text.

UPGRADE_STATIC_TEXTS = (
    "Project update required, This project was created using an older Audacity "
    "version. Once saved, the project can only be opened with Audacity version "
    "3.7 or newer., OK"
)
OTHER_DIALOG_STATIC_TEXTS = (
    "Error Opening Project, angie is already open in another window."
)


def test_upgrade_dialog_detected_from_static_text(monkeypatch):
    _fake_osascript(monkeypatch, stdout=UPGRADE_STATIC_TEXTS)
    assert ap.upgrade_dialog_present()


def test_unrelated_dialog_is_not_mistaken_for_the_upgrade_dialog(monkeypatch):
    _fake_osascript(monkeypatch, stdout=OTHER_DIALOG_STATIC_TEXTS)
    assert not ap.upgrade_dialog_present()


def test_no_dialog_means_not_present(monkeypatch):
    _fake_osascript(monkeypatch, stdout="")
    assert not ap.upgrade_dialog_present()


def test_osascript_failure_is_silent_and_reports_absent(monkeypatch, capsys):
    """A poll loop must not spam stderr while Audacity is starting or gone."""
    _fake_osascript(monkeypatch, returncode=1, stderr="Can't get process Audacity")
    assert not ap.upgrade_dialog_present()
    assert capsys.readouterr().err == ""


def test_dismiss_reports_true_only_when_a_dialog_was_clicked(monkeypatch):
    _fake_osascript(monkeypatch, stdout="dismissed")
    assert ap.dismiss_upgrade_dialog()
    _fake_osascript(monkeypatch, stdout="none")
    assert not ap.dismiss_upgrade_dialog()


def test_watcher_dismisses_dialog_that_appears_mid_flight(monkeypatch):
    """The dialog only shows up after the open command is already in flight."""
    state = {"polls": 0, "clicked": False}

    def present():
        state["polls"] += 1
        return state["polls"] >= 2 and not state["clicked"]

    def dismiss():
        state["clicked"] = True
        return True

    monkeypatch.setattr(ap, "upgrade_dialog_present", present)
    monkeypatch.setattr(ap, "dismiss_upgrade_dialog", dismiss)
    monkeypatch.setattr(ap, "UPGRADE_DIALOG_POLL", 0.01)

    with ap.dismissing_upgrade_dialog() as dismissed:
        deadline = time.monotonic() + 2
        while not dismissed.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)

    assert state["clicked"]
    assert dismissed.is_set()


def test_watcher_clicks_nothing_when_no_dialog_appears(monkeypatch):
    clicks = []
    monkeypatch.setattr(ap, "upgrade_dialog_present", lambda: False)
    monkeypatch.setattr(ap, "dismiss_upgrade_dialog", lambda: clicks.append(1) or True)
    monkeypatch.setattr(ap, "UPGRADE_DIALOG_POLL", 0.01)

    with ap.dismissing_upgrade_dialog() as dismissed:
        time.sleep(0.05)

    assert clicks == []
    assert not dismissed.is_set()


def test_watcher_stops_after_the_block_exits(monkeypatch):
    """A leaked watcher would keep clicking dialogs raised by later commands."""
    polls = []
    monkeypatch.setattr(ap, "upgrade_dialog_present", lambda: polls.append(1) or False)
    monkeypatch.setattr(ap, "dismiss_upgrade_dialog", lambda: True)
    monkeypatch.setattr(ap, "UPGRADE_DIALOG_POLL", 0.01)

    with ap.dismissing_upgrade_dialog():
        time.sleep(0.03)
    after_exit = len(polls)
    time.sleep(0.1)

    assert len(polls) == after_exit
