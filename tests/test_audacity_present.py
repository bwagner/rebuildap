"""Tests for audacity_present that need no running Audacity.

The interesting failure modes here are environmental (no scripting module, no
Accessibility permission, ambiguous window titles), so each test fakes the
environment rather than driving the real app.
"""

import subprocess
import time

import pytest

from rebuildap import audacity_funcs as af
from rebuildap import audacity_present as ap

# Captured at import time, before conftest's autouse fixture replaces the module
# attribute with a stand-in that fails the test. The two tests below are *about*
# start_audacity itself, so they need the real one; they stay offline because
# they fake the subprocess.run underneath it.
_REAL_START_AUDACITY = ap.start_audacity


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
    # The TimeoutError message lists the open window titles, which is a real
    # osascript call - this test used to make one against the developer's own
    # machine on every offline run (caught by tests/conftest.py, 2026-07-25).
    monkeypatch.setattr(ap, "audacity_window_names", lambda: [])

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


# --- the no-region dialog (NyquistPrompt's refusal) --------------------------
#
# A NyquistPrompt sent with no time region raises a modal instead of answering,
# wedging the pipe. Measured 2026-07-29: the refusal is survivable and the pipe
# recovers fully, provided the client does NOT hang up first -- so this dialog is
# detected, dismissed, and read as "there is no region".

NO_REGION_STATIC_TEXTS = (
    'Message, "Nyquist Prompt" requires one or more tracks to be selected., OK'
)


def test_no_region_dialog_detected_from_static_text(monkeypatch):
    _fake_osascript(monkeypatch, stdout=NO_REGION_STATIC_TEXTS)
    assert ap.no_region_dialog_present()


def test_upgrade_dialog_is_not_mistaken_for_the_no_region_dialog(monkeypatch):
    """Each dialog gets its own matcher; only one of them is safe to click."""
    _fake_osascript(monkeypatch, stdout=UPGRADE_STATIC_TEXTS)
    assert not ap.no_region_dialog_present()


def test_already_open_alert_is_not_mistaken_for_the_no_region_dialog(monkeypatch):
    """The 'already open' alert must never be clicked -- see CLAUDE.md."""
    _fake_osascript(monkeypatch, stdout=OTHER_DIALOG_STATIC_TEXTS)
    assert not ap.no_region_dialog_present()


def test_no_dialog_means_no_region_dialog_absent(monkeypatch):
    _fake_osascript(monkeypatch, stdout="")
    assert not ap.no_region_dialog_present()


def test_no_region_probe_is_silent_when_osascript_fails(monkeypatch, capsys):
    _fake_osascript(monkeypatch, returncode=1, stderr="Can't get process Audacity")
    assert not ap.no_region_dialog_present()
    assert capsys.readouterr().err == ""


def test_dismiss_no_region_reports_true_only_when_clicked(monkeypatch):
    _fake_osascript(monkeypatch, stdout="dismissed")
    assert ap.dismiss_no_region_dialog()
    _fake_osascript(monkeypatch, stdout="none")
    assert not ap.dismiss_no_region_dialog()


def test_no_region_watcher_dismisses_dialog_that_appears_mid_flight(monkeypatch):
    """The refusal only shows up once the read is already in flight."""
    state = {"polls": 0, "clicked": False}

    def present():
        state["polls"] += 1
        return state["polls"] >= 2 and not state["clicked"]

    def dismiss():
        state["clicked"] = True
        return True

    monkeypatch.setattr(ap, "no_region_dialog_present", present)
    monkeypatch.setattr(ap, "dismiss_no_region_dialog", dismiss)
    monkeypatch.setattr(ap, "NO_REGION_DIALOG_POLL", 0.01)

    with ap.dismissing_no_region_dialog() as dismissed:
        deadline = time.monotonic() + 2
        while not dismissed.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)

    assert state["clicked"]
    assert dismissed.is_set()


def test_no_region_watcher_clicks_nothing_when_no_dialog_appears(monkeypatch):
    clicks = []
    monkeypatch.setattr(ap, "no_region_dialog_present", lambda: False)
    monkeypatch.setattr(
        ap, "dismiss_no_region_dialog", lambda: clicks.append(1) or True
    )
    monkeypatch.setattr(ap, "NO_REGION_DIALOG_POLL", 0.01)

    with ap.dismissing_no_region_dialog() as dismissed:
        time.sleep(0.05)

    assert clicks == []
    assert not dismissed.is_set()


# --- a missing or wrong-version Audacity ----------------------------------
#
# Reported 2026-09-08: with only Audacity 4 installed, `rebuildap check` printed
# `Unable to find application named 'Audacity'` and then kept going for 52.8s --
# 20s waiting for a window, 20s probing a scripting pipe whose FIFOs were stale
# leftovers, and a Cmd-N in between -- before dying with a TimeoutError
# traceback. From the outside that is indistinguishable from a hang, and the one
# fact that mattered was printed first and then contradicted by everything after
# it. These tests pin the two halves of the fix: fail immediately, and say which
# situation this is.


def _fake_command(monkeypatch, returncode=0, stderr=""):
    """Fake subprocess.run for a plain command - here `open -a Audacity`."""

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout="", stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", fake_run)


def _fake_app_bundle(directory, name, bundle_id, version):
    """Write a minimal .app whose Info.plist carries the identity we read."""
    import plistlib

    contents = directory / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    with open(contents / "Info.plist", "wb") as fh:
        plistlib.dump(
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleShortVersionString": version,
                "CFBundleName": "Audacity",
            },
            fh,
        )
    return directory / f"{name}.app"


def _installs(monkeypatch, tmp_path, *bundles):
    for name, bundle_id, version in bundles:
        _fake_app_bundle(tmp_path, name, bundle_id, version)
    monkeypatch.setattr(ap, "APPLICATION_DIRS", (str(tmp_path),))


def test_failed_launch_raises_instead_of_waiting_for_a_window(monkeypatch, tmp_path):
    """Nothing was launched, so every subsequent wait is pure delay."""
    monkeypatch.setattr(ap, "is_audacity_running", lambda: False)
    monkeypatch.setattr(ap, "start_audacity", lambda: (False, "no such application"))
    monkeypatch.setattr(
        ap,
        "wait_for_audacity_window",
        lambda *a, **k: pytest.fail("waited for a window that can never appear"),
    )

    with pytest.raises(ap.AudacityUnavailableError, match="no such application"):
        ap.assert_audacity_running(verbose=False)


def test_a_successful_launch_still_waits_for_its_window(monkeypatch):
    """The happy path is unchanged: a launch that worked is worth waiting on."""
    monkeypatch.setattr(ap, "is_audacity_running", lambda: False)
    monkeypatch.setattr(ap, "start_audacity", lambda: (True, ""))
    waited = []
    monkeypatch.setattr(
        ap, "wait_for_audacity_window", lambda *a, **k: waited.append(True) or True
    )

    ap.assert_audacity_running(verbose=False)
    assert waited == [True]


def test_an_already_running_audacity_is_never_launched(monkeypatch):
    monkeypatch.setattr(ap, "is_audacity_running", lambda: True)
    monkeypatch.setattr(
        ap, "start_audacity", lambda: pytest.fail("launched an already-running app")
    )
    ap.assert_audacity_running(verbose=False)


def test_start_audacity_reports_the_failure_open_printed(monkeypatch):
    """os.system discarded this status, which is how the 53s walk began."""
    _fake_command(
        monkeypatch, returncode=1, stderr="Unable to find application named 'Audacity'"
    )
    ok, err = _REAL_START_AUDACITY()
    assert ok is False
    assert "Unable to find application named" in err


def test_start_audacity_reports_success(monkeypatch):
    _fake_command(monkeypatch, returncode=0)
    assert _REAL_START_AUDACITY() == (True, "")


def test_only_audacity_4_installed_is_named_in_the_error(monkeypatch, tmp_path):
    _installs(
        monkeypatch, tmp_path, ("Audacity 4", "org.audacityteam.audacity4", "4.0.0")
    )
    monkeypatch.setattr(ap, "is_audacity_running", lambda: False)
    monkeypatch.setattr(ap, "start_audacity", lambda: (False, "not found"))

    with pytest.raises(ap.AudacityUnavailableError) as excinfo:
        ap.assert_audacity_running(verbose=False)
    message = str(excinfo.value)
    assert "4.0.0" in message
    assert "3.x" in message


def test_no_audacity_at_all_says_so_rather_than_blaming_a_version(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(ap, "APPLICATION_DIRS", (str(tmp_path),))
    monkeypatch.setattr(ap, "is_audacity_running", lambda: False)
    monkeypatch.setattr(ap, "start_audacity", lambda: (False, "not found"))

    with pytest.raises(ap.AudacityUnavailableError, match="No Audacity application"):
        ap.assert_audacity_running(verbose=False)


def test_a_supported_audacity_produces_no_version_hint(monkeypatch, tmp_path):
    """Absence of a hint is what lets the normal error messages stand."""
    _installs(monkeypatch, tmp_path, ("Audacity", "org.audacityteam.audacity", "3.7.8"))
    assert ap.unsupported_audacity_hint() is None


def test_audacity_3_alongside_4_is_not_a_version_problem(monkeypatch, tmp_path):
    _installs(
        monkeypatch,
        tmp_path,
        ("Audacity", "org.audacityteam.audacity", "3.7.8"),
        ("Audacity 4", "org.audacityteam.audacity4", "4.0.0"),
    )
    assert ap.unsupported_audacity_hint() is None


def test_an_unfindable_audacity_is_never_reported_as_the_wrong_version(
    monkeypatch, tmp_path
):
    """Absence of evidence is not evidence: an Audacity installed somewhere
    unusual must not be reported as an Audacity 4."""
    monkeypatch.setattr(ap, "APPLICATION_DIRS", (str(tmp_path),))
    assert ap.unsupported_audacity_hint() is None


def test_installs_are_identified_by_bundle_id_not_by_name(monkeypatch, tmp_path):
    """Audacity 4 calls itself 'Audacity' in CFBundleName, so names decide nothing."""
    _installs(
        monkeypatch,
        tmp_path,
        ("Audacity 4", "org.audacityteam.audacity4", "4.0.0"),
        ("Audacity Recorder", "com.example.notaudacity", "1.0"),
    )
    found = ap.find_audacity_installs()
    assert [i.bundle_id for i in found] == ["org.audacityteam.audacity4"]
    assert found[0].major == 4


def test_a_bundle_without_a_readable_plist_is_skipped(monkeypatch, tmp_path):
    (tmp_path / "Broken.app" / "Contents").mkdir(parents=True)
    (tmp_path / "Broken.app" / "Contents" / "Info.plist").write_text("not a plist")
    monkeypatch.setattr(ap, "APPLICATION_DIRS", (str(tmp_path),))
    assert ap.find_audacity_installs() == []


def test_an_unparseable_version_is_not_mistaken_for_a_supported_one(
    monkeypatch, tmp_path
):
    _installs(
        monkeypatch, tmp_path, ("Audacity", "org.audacityteam.audacity", "unknown")
    )
    assert ap.find_audacity_installs()[0].major is None
    assert ap.unsupported_audacity_hint() is not None


def test_a_missing_applications_directory_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ap, "APPLICATION_DIRS", (str(tmp_path / "nope"),))
    assert ap.find_audacity_installs() == []


def test_audacity_4_explains_the_absent_fifos_it_causes(monkeypatch, tmp_path):
    """'Enable mod-script-pipe in Preferences' is impossible advice on an
    Audacity 4 - it has no such module to enable."""
    _installs(
        monkeypatch, tmp_path, ("Audacity 4", "org.audacityteam.audacity4", "4.0.0")
    )
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_TO", str(tmp_path / "to"))
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_FROM", str(tmp_path / "from"))
    monkeypatch.setattr(ap, "SCRIPT_PIPE_GRACE", 0.2)

    with pytest.raises(ap.ScriptPipeUnavailableError, match="4.0.0"):
        ap.wait_for_audacity_ready(timeout=10.0)


def test_stale_fifos_from_a_replaced_audacity_still_name_the_version(
    monkeypatch, tmp_path
):
    """The reported run's actual shape: FIFOs left behind by an Audacity 3 that
    is no longer installed, so the pipe *looks* present and the run times out."""
    _installs(
        monkeypatch, tmp_path, ("Audacity 4", "org.audacityteam.audacity4", "4.0.0")
    )
    (tmp_path / "to").touch()
    (tmp_path / "from").touch()
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_TO", str(tmp_path / "to"))
    monkeypatch.setattr(ap, "_SCRIPT_PIPE_FROM", str(tmp_path / "from"))
    monkeypatch.setattr(ap, "audacity_window_names", lambda: [])
    monkeypatch.setattr(af, "_drain_read_pipe", lambda: b"")
    monkeypatch.setattr(ap, "_probe_tracks_with_timeout", lambda _t: None)

    with pytest.raises(TimeoutError, match="4.0.0"):
        ap.wait_for_audacity_ready(timeout=0.5)
