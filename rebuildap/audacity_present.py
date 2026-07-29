#!/usr/bin/env python

import errno
import os
import select
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import contextmanager

import psutil

from . import audacity_funcs as af

_SCRIPT_PIPE_TO = f"/tmp/audacity_script_pipe.to.{os.getuid()}"
_SCRIPT_PIPE_FROM = f"/tmp/audacity_script_pipe.from.{os.getuid()}"

RESPONSE_TERMINATOR = b"BatchCommand finished"  # marks a complete pipe response
READ_CHUNK_BYTES = 65536  # per-read size when draining the from-pipe
SELECT_INTERVAL = 0.25  # select() slice while waiting for a response
REOPEN_RETRY_INTERVAL = 0.1  # retry pause while Audacity reopens the FIFOs

# Readiness/probe tuning. See wait_for_audacity_ready and assert_audacity_window.
# A new project answers the pipe ~3.4s after Cmd-N on 3.7.8 (window at ~2.7s),
# so the readiness budget has to comfortably clear that plus cold-start jitter.
READY_TIMEOUT = 20.0  # overall budget for the end-to-end readiness probe
PROBE_TIMEOUT_MAX = 3.0  # hard cap per individual GetInfo round-trip
PROBE_TIMEOUT_MIN = 0.5  # never give a probe less than this
POLL_DELAY_INITIAL = 0.5  # first backoff sleep between probes
POLL_DELAY_MAX = 2.0  # backoff ceiling
POLL_DELAY_FACTOR = 1.5  # exponential backoff multiplier
COLD_START_SETTLE = 1.5  # menu-subsystem settling delay after a cold start
LAUNCH_WINDOW_TIMEOUT = 20.0  # wait for a window after launching Audacity
NEW_WINDOW_TIMEOUT = 15.0  # wait for the Cmd-N project window to appear
WINDOW_POLL_INTERVAL = 0.5  # polling interval while waiting for a window
DRAIN_PREVIEW_BYTES = 200  # stale-byte preview length in the drain diagnostic
SCRIPT_PIPE_GRACE = 8.0  # allow this long for the FIFOs to be created at startup

# Format-upgrade dialog. Opening an .aup3 written by an older Audacity raises a
# modal "Project update required" dialog, and until it is acknowledged
# OpenProject2 never returns — the caller just times out, with nothing to
# distinguish it from a wedged pipe. Measured on 3.7.8: the dialog appears
# ~0.18s after the command is sent and the open completes ~0.03s after it is
# dismissed, so a 5s per-attempt timeout was never too tight — the whole budget
# was being burned waiting on a human.
#
# Dismissing it is safe for read-only flows. Its own wording is "Once saved, the
# project can only be opened with Audacity version 3.7 or newer", i.e. the
# conversion happens on *save*; verified by md5 — a project file was unchanged
# byte-for-byte after being opened with the dialog dismissed. It offers OK and
# nothing else (AXCancelButton is `missing value`, so Escape does nothing),
# which makes acknowledging the only way past it.
#
# The dialog has **no window title** — it shows up in the window list as an
# empty name — so it cannot be found the way every other window here is found.
# Match on its static text instead.
UPGRADE_DIALOG_TEXT = "Project update required"
UPGRADE_DIALOG_POLL = 0.15  # dialog observed at ~0.18s; poll ahead of that

# NyquistPrompt's refusal when there is no time region. Also untitled, so it is
# matched on static text like the upgrade dialog. The quotes Audacity puts around
# "Nyquist Prompt" are deliberately left out of the marker: the tail is unique on
# its own and needs no escaping inside the AppleScript string literal.
NO_REGION_DIALOG_TEXT = "requires one or more tracks to be selected"
NO_REGION_DIALOG_POLL = 0.15  # same cadence as the upgrade-dialog watcher

MOD_SCRIPT_PIPE_HINT = (
    "Audacity's scripting FIFOs were never created, which means the "
    "mod-script-pipe module is not active. Enable it under "
    "Preferences > Modules > mod-script-pipe (set it to 'Enabled') and "
    "restart Audacity. See "
    "https://manual.audacityteam.org/man/scripting.html"
)

ACCESSIBILITY_HINT = (
    "osascript was refused control of Audacity. Grant your terminal (or the "
    "app running rebuildap) Accessibility permission under System Settings > "
    "Privacy & Security > Accessibility. Note that rebuildap drives Audacity "
    "via GUI keystrokes, so it needs a real, unlocked login session."
)
# osascript error fragments that mean "no accessibility permission", not "no window".
_ACCESSIBILITY_ERROR_MARKERS = ("assistive access", "-1719", "-1743", "not authorized")


class ScriptPipeUnavailableError(RuntimeError):
    """mod-script-pipe is not active, so scripting can never work.

    Distinct from a timeout: waiting longer cannot help — the user has to
    enable the module and restart Audacity.
    """


def script_pipe_exists() -> bool:
    """True if both scripting FIFOs exist on disk.

    mod-script-pipe creates them at Audacity startup, so their absence means
    the module is disabled (or Audacity has not got far enough into launching
    yet — hence SCRIPT_PIPE_GRACE before we conclude anything).
    """
    return os.path.exists(_SCRIPT_PIPE_TO) and os.path.exists(_SCRIPT_PIPE_FROM)


# Deliberately no "is the pipe listening?" helper here. Probing readiness by
# opening the write end with O_WRONLY | O_NONBLOCK and closing it looks free,
# but Audacity reads that as a client connecting and hanging up: it ends the
# session and reopens both FIFOs, so the pre-check breaks the very round-trip
# it was meant to protect. A True result wouldn't have meant much anyway — a
# project-less Audacity holds the FIFO open and still answers nothing. Use
# _probe_tracks_with_timeout, which is end-to-end and self-limiting.


"""
audacity_present.py


"""


def run_osascript(script: str, what: str) -> tuple[bool, str]:
    """Run an AppleScript, reporting failure instead of swallowing it.

    Every GUI action here — activating Audacity, Cmd-N, Cmd-W, listing
    windows — goes through osascript, and each one fails silently if the
    caller lacks Accessibility permission or there is no GUI login session.
    That turns a permissions problem into an unexplained timeout much later,
    so failures are always announced on stderr.

    Returns ``(ok, stdout)``.
    """
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr.strip()
        print(f"[osascript] {what} failed: {err}", file=sys.stderr)
        if any(m in err for m in _ACCESSIBILITY_ERROR_MARKERS):
            print(ACCESSIBILITY_HINT, file=sys.stderr)
        return False, ""
    return True, result.stdout.strip()


def _query_osascript_quiet(script: str) -> str | None:
    """Run an AppleScript for its value, staying silent when it fails.

    :func:`run_osascript` announces every failure on stderr, which is right for
    one-shot actions but wrong for a poll loop: while Audacity is starting or
    shutting down "no such process" is the expected answer several times a
    second. Returns None on failure.
    """
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def upgrade_dialog_present() -> bool:
    """True if Audacity is showing the "project needs updating" dialog."""
    out = _query_osascript_quiet(
        'tell application "System Events" to tell process "Audacity" to '
        "return value of every static text of "
        '(every window whose subrole is "AXDialog")'
    )
    return out is not None and UPGRADE_DIALOG_TEXT in out


def dismiss_upgrade_dialog() -> bool:
    """Acknowledge the format-upgrade dialog. Returns True if one was dismissed.

    Targets the dialog by its static text rather than clicking whatever dialog
    happens to be frontmost: several modal dialogs can be stacked at once (an
    "Error Opening Project" alert and an "Applying Open Project2..." progress
    window have both been seen alongside it), and dismissing the wrong one is
    the same class of mistake as a bare Cmd-W.
    """
    script = (
        'tell application "System Events" to tell process "Audacity"\n'
        '  repeat with w in (every window whose subrole is "AXDialog")\n'
        "    if ((value of static texts of w) as string) contains "
        f'"{UPGRADE_DIALOG_TEXT}" then\n'
        '      click button "OK" of w\n'
        '      return "dismissed"\n'
        "    end if\n"
        "  end repeat\n"
        "end tell\n"
        'return "none"'
    )
    out = _query_osascript_quiet(script)
    return out == "dismissed"


def no_region_dialog_present() -> bool:
    """True if Audacity is showing NyquistPrompt's "no time region" refusal.

    Its wording blames track selection, but the missing thing is the *region*:
    measured 2026-07-29 on 3.7.8, a run with only label tracks selected read the
    selection fine with a region and raised this dialog without one.
    """
    out = _query_osascript_quiet(
        'tell application "System Events" to tell process "Audacity" to '
        "return value of every static text of "
        '(every window whose subrole is "AXDialog")'
    )
    return out is not None and NO_REGION_DIALOG_TEXT in out


def dismiss_no_region_dialog() -> bool:
    """Clear NyquistPrompt's refusal. Returns True if one was dismissed.

    **This is the one modal rebuildap clicks**, and only because it was measured
    safe: with the client still attached, both a hand click and this programmatic
    one leave Audacity running and the pipe fully usable (2026-07-29, 3.7.8). The
    five deaths on record all had the client process *already gone* -- closing the
    FIFO with a modal open is the hazard, not the click. See
    ``~/.claude/audacity.md``.

    Like :func:`dismiss_upgrade_dialog` it targets the dialog by static text, never
    "whatever is frontmost": the ``<name> is already open in another window.`` alert
    remains on the never-click list, its client state having never been measured.
    """
    script = (
        'tell application "System Events" to tell process "Audacity"\n'
        '  repeat with w in (every window whose subrole is "AXDialog")\n'
        "    if ((value of static texts of w) as string) contains "
        f'"{NO_REGION_DIALOG_TEXT}" then\n'
        '      click button "OK" of w\n'
        '      return "dismissed"\n'
        "    end if\n"
        "  end repeat\n"
        "end tell\n"
        'return "none"'
    )
    out = _query_osascript_quiet(script)
    return out == "dismissed"


@contextmanager
def dismissing_no_region_dialog():
    """Watch for NyquistPrompt's refusal and clear it while inside.

    Sibling of :func:`dismissing_upgrade_dialog`, and for the same two reasons:
    the dialog can only appear once the command is already in flight, and waiting
    for the read to time out first would both leave it on screen for the whole
    15s budget and recover through ``_pa_do_timed``'s abandoned-thread path.

    The yielded event being set is the *signal* callers act on: it means Audacity
    refused the read, which means there is no time region. The watcher only ever
    touches this one dialog, matched on its static text, and only via osascript -
    it never goes near the scripting pipe, so it cannot disturb the in-flight
    command.
    """
    done = threading.Event()
    dismissed = threading.Event()

    def watch():
        while not done.is_set():
            if no_region_dialog_present() and dismiss_no_region_dialog():
                dismissed.set()
                return
            done.wait(NO_REGION_DIALOG_POLL)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        yield dismissed
    finally:
        done.set()
        watcher.join(timeout=NO_REGION_DIALOG_POLL * 2)


@contextmanager
def dismissing_upgrade_dialog(verbose: bool = False):
    """Watch for the format-upgrade dialog and acknowledge it while inside.

    The dialog can only appear *after* the open command is in flight, so it
    cannot be pre-empted; and waiting for the command to time out first would
    mean recovering through ``_pa_do_timed``'s abandoned-thread path, which is
    documented to eat the next call's response. Watching concurrently keeps the
    command on its normal, successful path instead.

    The watcher only ever touches the upgrade dialog, and only via osascript —
    it never goes near the scripting pipe, so it cannot interfere with the
    in-flight command.
    """
    done = threading.Event()
    dismissed = threading.Event()

    def watch():
        while not done.is_set():
            if upgrade_dialog_present() and dismiss_upgrade_dialog():
                dismissed.set()
                return
            done.wait(UPGRADE_DIALOG_POLL)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        yield dismissed
    finally:
        done.set()
        watcher.join(timeout=UPGRADE_DIALOG_POLL * 2)
        if verbose and dismissed.is_set():
            print(
                "Acknowledged Audacity's 'project needs updating' dialog. The "
                "project file is not modified by opening it - the format "
                "conversion would only happen on save."
            )


def audacity_window_names() -> list[str]:
    """Return the titles of Audacity's open windows (empty list if none).

    Note that a title alone does not tell us whether a window is a usable
    *project*: a lone ``About Audacity`` dialog counts as an open window but
    has no project behind it, so the scripting pipe will accept commands and
    never answer. Use :func:`_probe_project_empty` to decide usability.

    An osascript failure also yields an empty list — indistinguishable here
    from "no windows", which is why run_osascript reports it on stderr.
    """
    script = """
    tell application "System Events"
        return name of windows of process "Audacity"
    end tell
    """
    ok, out = run_osascript(script, "listing Audacity windows")
    if not ok:
        return []
    return [n.strip() for n in out.split(",") if n.strip()]


def is_audacity_window_open():
    """
    Checks whether Audacity window is open.
    """
    return bool(audacity_window_names())


def project_window_open(stem: str) -> bool:
    """True if a project window titled ``stem`` is already open.

    Opening a project Audacity already has open raises a modal ``Error Opening
    Project`` / "<name> is already open in another window." alert, which blocks
    ``OpenProject2:`` exactly the way the format-upgrade dialog does. Unlike
    that one this is *preventable*: Audacity titles a project window with its
    ``.aup3`` stem — the same fact :func:`close_owned_window` relies on — so the
    condition is visible before any command is sent. Prevention is the only
    sanctioned handling here: Audacity **exited immediately** the one time this
    dialog was dismissed with an osascript click, so clicking it is the one
    dismissal with live evidence against it.

    Matched exactly, not by substring, so ``angie`` is not reported open by a
    window titled ``angie_live``.

    Two known blind spots, both degrading to the old behaviour (a timeout)
    rather than to anything worse:

    - Window titles carry no path, so two same-named projects in different
      directories are indistinguishable. A false positive costs a skipped
      project; it never opens the wrong one.
    - Whether a project with *unsaved* edits still titles its window exactly
      ``stem`` is unverified. If Audacity adds a dirty marker, this misses it.
    """
    return stem in audacity_window_names()


def wait_for_audacity_window(timeout: float = LAUNCH_WINDOW_TIMEOUT) -> bool:
    """Poll until Audacity has at least one window, or ``timeout`` elapses.

    Purely AppleScript-based, so it works before the scripting pipe is usable
    — which matters because the pipe cannot answer until a project window
    exists.  Returns True if a window appeared.
    """
    return wait_for_new_audacity_window([], timeout)


def wait_for_new_audacity_window(
    before: Sequence[str], timeout: float = NEW_WINDOW_TIMEOUT
) -> bool:
    """Poll until Audacity gains a window relative to ``before``.

    Checking for a *new* window rather than merely "any window" matters: with a
    stray ``About Audacity`` dialog on screen, "any window" is already true and
    would mask a Cmd-N that never landed.

    Counts as well as titles, because an empty project window is titled
    ``Audacity`` — so opening a second one adds no new *title*, and comparing
    title sets alone would miss it.
    """
    before_count = len(before)
    before_titles = set(before)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        names = audacity_window_names()
        if len(names) > before_count or (set(names) - before_titles):
            return True
        time.sleep(WINDOW_POLL_INTERVAL)
    return False


def is_audacity_running():
    """
    Returns true if Audacity is running.
    """
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            # Check if the process name is "Audacity"
            if "Audacity" in proc.info["name"]:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


def start_audacity():
    """
    Starts Audacity.
    """
    os.system('open -a "Audacity"')


def bring_audacity_window_to_front_as() -> bool:
    """Bring Audacity to the front and open a new project (Cmd-N).

    Returns False if the AppleScript itself failed — typically missing
    Accessibility permission, in which case no keystroke was ever delivered.
    """
    script = """
    tell application "Audacity"
        activate
    end tell
    tell application "System Events"
        -- Wait a bit for Audacity to become active
        delay 1
        -- Simulate Cmd+N to open a new project
        keystroke "n" using {command down}
    end tell
    """
    ok, _ = run_osascript(script, "opening a new Audacity project (Cmd-N)")
    return ok


def close_audacity_window_as() -> bool:
    """Close Audacity's frontmost window (Cmd-W). Returns False if osascript failed.

    Closes whatever is frontmost, which is not necessarily the window the
    caller opened — so this is the raw primitive, not the thing to call.
    Use :func:`close_owned_window`, which confirms ownership first.
    """
    script = """
    tell application "Audacity" to activate
    tell application "System Events"
        keystroke "w" using command down
    end tell
    """
    ok, _ = run_osascript(script, "closing the Audacity window (Cmd-W)")
    return ok


def frontmost_audacity_window_name() -> str | None:
    """Title of Audacity's frontmost window, or ``None`` if there isn't one.

    With no windows open, System Events does not return an empty result — it
    errors with ``Can't get window 1 ... Invalid index. (-1719)``. That shares
    an error code with the Accessibility refusal that ``run_osascript``
    diagnoses, so both surface here as ``None`` and callers must treat it as
    "ownership unknown" rather than "no window".
    """
    script = """
    tell application "System Events"
        tell process "Audacity" to return name of front window
    end tell
    """
    ok, out = run_osascript(script, "reading Audacity's frontmost window")
    if not ok or not out.strip():
        return None
    return out.strip()


def raise_audacity_window_as(title: str) -> bool:
    """Bring the Audacity window titled ``title`` to the front via AXRaise.

    Returns whether the AppleScript succeeded, which is *not* the same as the
    window actually being frontmost afterwards — callers must re-read
    :func:`frontmost_audacity_window_name` to confirm.
    """
    script = f"""
    tell application "Audacity" to activate
    tell application "System Events"
        tell process "Audacity"
            perform action "AXRaise" of (first window whose name is "{title}")
        end tell
    end tell
    """
    ok, _ = run_osascript(script, f"raising the Audacity window {title!r}")
    return ok


def close_owned_window(title: str, verbose: bool = False) -> bool:
    """Close the Audacity window titled ``title``, but only if it is provably ours.

    Cmd-W closes whatever is frontmost. If focus has moved — the user clicked
    their own project, a dialog stole it — a blind Cmd-W closes *their* window,
    discarding unsaved work and raising a "Save changes?" dialog that then
    wedges the scripting pipe. This is the only known data-loss path in
    rebuildap, so the rule is: confirm the frontmost window is ours, or do
    nothing.

    ``title`` identifies the window because Audacity titles a saved or opened
    project with its .aup3 stem. Empty projects are all titled ``Audacity``,
    which is why a duplicated title counts as unidentifiable rather than a
    match.

    Returns True only if Cmd-W was actually sent. Refusing leaks a window,
    which is the deliberately cheaper failure: a stray window costs nothing,
    a wrong Cmd-W costs the user's work.
    """

    def refuse(reason: str) -> bool:
        print(
            f"Not closing the Audacity window {title!r}: {reason}. "
            "No window was closed; left open rather than risk closing the "
            "wrong one.",
            file=sys.stderr,
        )
        return False

    matches = [n for n in audacity_window_names() if n == title]
    if not matches:
        return refuse("it is no longer open")
    if len(matches) > 1:
        return refuse(f"{len(matches)} windows share that title")

    front = frontmost_audacity_window_name()
    if front != title:
        if front is None and verbose:
            print(
                "Could not read Audacity's frontmost window; attempting to raise "
                f"{title!r} anyway.",
                file=sys.stderr,
            )
        raise_audacity_window_as(title)
        front = frontmost_audacity_window_name()
        if front != title:
            return refuse(
                f"it could not be brought to the front (frontmost: {front!r})"
            )

    if verbose:
        print(f"Closing the Audacity window {title!r}.")
    return close_audacity_window_as()


def _probe_tracks_with_timeout(timeout_per_probe: float):
    """Run one ``GetInfo: Type=Tracks`` probe with a hard timeout.

    Returns the parsed tracks list on success, or ``None`` on failure/timeout.

    Talks to the FIFOs directly rather than going through ``pa.do()`` in a
    daemon thread. That older approach leaked: on timeout the thread stayed
    blocked in ``read_pipe.readline()`` while holding *both* pipes open, so it
    later consumed the response belonging to the next probe. Once one probe
    timed out, every subsequent probe in the same process failed — which is
    precisely the state a window-less Audacity puts us in. Here every fd is
    closed on the way out, so a timeout costs nothing but the wait.

    Ordering matters: Audacity opens the to-pipe for reading first and only
    then the from-pipe for writing, so we must open the to-pipe for *writing*
    first or the two sides deadlock.
    """
    deadline = time.monotonic() + timeout_per_probe
    wfd = rfd = None
    try:
        # O_NONBLOCK: ENXIO instead of blocking forever when no reader is attached.
        # Closing our write end makes Audacity see EOF and reopen both FIFOs, so
        # right after a previous probe there is a brief readerless gap. Retry
        # across it rather than reporting the pipe dead.
        while True:
            try:
                wfd = os.open(_SCRIPT_PIPE_TO, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as e:
                if e.errno not in (errno.ENXIO, errno.ENOENT):
                    raise
                if time.monotonic() >= deadline:
                    return None
                time.sleep(REOPEN_RETRY_INTERVAL)
        os.write(wfd, b"GetInfo: Type=Tracks\n")
        # Reads never block on open, so this is safe even if Audacity never replies.
        rfd = os.open(_SCRIPT_PIPE_FROM, os.O_RDONLY | os.O_NONBLOCK)
        buf = b""
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([rfd], [], [], min(SELECT_INTERVAL, remaining))
            if not ready:
                continue
            chunk = os.read(rfd, READ_CHUNK_BYTES)
            if chunk:
                buf += chunk
            if RESPONSE_TERMINATOR in buf:
                return af._parse_tracks_response(buf.decode("utf-8", errors="replace"))
        return None
    except (OSError, ValueError):  # ENXIO, or an unparseable/partial response
        return None
    finally:
        for fd in (wfd, rfd):
            if fd is not None:
                os.close(fd)


def _probe_project_empty(timeout: float = PROBE_TIMEOUT_MAX):
    """Best-effort "is the open project empty?" that can never block.

    Returns True/False when the pipe answers, and ``None`` when it doesn't.
    ``None`` is the interesting case: it means Audacity is running and holding
    the FIFO, but has no project window to dispatch to (e.g. only an ``About
    Audacity`` dialog is open), so commands are consumed without a reply.

    This exists so that :func:`assert_audacity_window` never calls the
    unguarded, indefinitely-blocking ``af.is_project_empty()``.
    """
    tracks = _probe_tracks_with_timeout(timeout)
    return None if tracks is None else len(tracks) == 0


def wait_for_audacity_ready(
    timeout: float = READY_TIMEOUT, verbose: bool = False
) -> None:
    """Poll Audacity's scripting pipe until it responds to a read-only query.

    Works around an Audacity bug: on a freshly started instance, commands
    that route through the menu subsystem (e.g. ``Close:``) can arrive
    before menus are fully initialized, causing a null-pointer dereference
    in ``lib-menus.dylib`` (observed signature:
    ``EXC_BAD_ACCESS / KERN_INVALID_ADDRESS`` at ``0x220``, process uptime
    ~5 s, ``mod-script-pipe.so`` delivering ``Close:`` at crash time).

    The scripting pipe becomes available earlier than the menu subsystem.
    We probe with ``GetInfo: Type=Tracks`` (pipe-only, no menu dispatch)
    with exponential backoff. Each probe has its own hard timeout because a
    running-but-project-less Audacity consumes commands without ever
    answering. If any probe needed more than one attempt — a proxy for
    "Audacity was just cold-started" — we add a short settling delay so
    the menu subsystem can finish initializing before the caller sends
    commands like ``Close:``.

    Raises ``ScriptPipeUnavailableError`` if the FIFOs never appear (module
    disabled — waiting cannot help), or ``TimeoutError`` if they exist but
    nothing answers within ``timeout`` seconds. See
    ``docs/audacity-quirks.md`` > Audacity cold-start race.
    """
    start = time.monotonic()
    delay = POLL_DELAY_INITIAL
    attempt = 0
    while time.monotonic() - start < timeout:
        attempt += 1
        # No FIFOs means mod-script-pipe isn't active. They're created during
        # startup, so allow a grace period before concluding it's disabled —
        # but don't burn the whole budget on something waiting can't fix.
        if not script_pipe_exists():
            if time.monotonic() - start >= SCRIPT_PIPE_GRACE:
                raise ScriptPipeUnavailableError(
                    f"{_SCRIPT_PIPE_TO} does not exist. {MOD_SCRIPT_PIPE_HINT}"
                )
            time.sleep(delay)
            delay = min(delay * POLL_DELAY_FACTOR, POLL_DELAY_MAX)
            continue
        # Drain any stale bytes left by a previously timed-out probe before
        # attempting the next round-trip (diagnostic printed to stderr).
        drained = af._drain_read_pipe()
        if drained:
            preview = drained[:DRAIN_PREVIEW_BYTES].decode("utf-8", errors="replace")
            if len(drained) > DRAIN_PREVIEW_BYTES:
                preview += "..."
            print(
                f"[pipe drain] discarded {len(drained)} stale bytes: {preview!r}",
                file=sys.stderr,
            )
        remaining = timeout - (time.monotonic() - start)
        per_probe = min(PROBE_TIMEOUT_MAX, max(PROBE_TIMEOUT_MIN, remaining))
        if _probe_tracks_with_timeout(per_probe) is not None:
            break
        time.sleep(delay)
        delay = min(delay * POLL_DELAY_FACTOR, POLL_DELAY_MAX)
    else:
        windows = audacity_window_names()
        detail = (
            f"Open windows: {windows}. Audacity accepts pipe commands but never "
            "answers them unless a project window is frontmost, so a dialog "
            "(About, 'Save changes?', crash recovery, an export in progress) "
            "produces exactly this timeout."
            if windows
            else "Audacity has no open window at all."
        )
        raise TimeoutError(
            f"Audacity scripting pipe did not respond within {timeout}s. {detail}"
        )
    if attempt > 1:
        # Probe needed retries → cold start. Give the menu subsystem a short
        # beat to finish initializing before the caller issues a menu-routed
        # command such as Close:.
        time.sleep(COLD_START_SETTLE)
        if verbose:
            elapsed = time.monotonic() - start
            print(f"Audacity ready after {elapsed:.1f}s (cold start).")


def assert_audacity_running(verbose: bool = True):
    """Ensure the Audacity process exists and has put up a window.

    Deliberately does *not* probe the scripting pipe: the pipe cannot answer
    until a project window exists, and ensuring that is
    :func:`assert_audacity_window`'s job. Waiting on the pipe here deadlocked
    whenever Audacity was running window-less or showing only a dialog.
    """
    if is_audacity_running():
        if verbose:
            print("Audacity is running.")
        return
    if verbose:
        print("Audacity is not running. Starting it.")
    start_audacity()
    # Only worth waiting after a launch. An already-running Audacity that has
    # no window will never grow one on its own — assert_audacity_window opens
    # one via Cmd-N — so waiting there just burns the timeout.
    if not wait_for_audacity_window(LAUNCH_WINDOW_TIMEOUT) and verbose:
        print(f"No Audacity window appeared within {LAUNCH_WINDOW_TIMEOUT}s.")


def assert_audacity_window(verbose: bool = True):
    """Ensure an empty, script-addressable project window is frontmost.

    The probe — not the window title — decides usability: a running Audacity
    with no project window (none at all, or only an ``About Audacity`` dialog)
    accepts pipe commands and never answers them. That state persists across
    runs, so it must be actively recovered from rather than waited out.

    Recovery is a single Cmd-N. We then wait only for the *window* to appear,
    not for the pipe: measured on 3.7.8, the window shows at ~2.7 s and the
    pipe starts answering at ~3.4 s, so probing for responsiveness here would
    race project initialization. Confirming responsiveness is
    :func:`wait_for_audacity_ready`'s job, which the caller runs next.
    """
    if _probe_project_empty() is True:
        if verbose:
            print("An Audacity window is open. Will use this.")
        return

    if verbose:
        print("Bringing Audacity window to the front with a new project.")
    before = audacity_window_names()
    if not bring_audacity_window_to_front_as():  # Cmd-N
        # The keystroke never reached Audacity, so no window can appear and
        # the readiness probe that follows would time out for the wrong
        # reason. run_osascript has already explained why on stderr.
        return
    if not wait_for_new_audacity_window(before, NEW_WINDOW_TIMEOUT) and verbose:
        print(
            f"No new Audacity window appeared within {NEW_WINDOW_TIMEOUT}s "
            f"(windows: {audacity_window_names()}). A modal dialog may be "
            "swallowing the Cmd-N."
        )


def assert_audacity(verbose: bool = True):
    assert_audacity_running(verbose)
    # Readiness is probed only after a project window is guaranteed — the
    # scripting pipe has nothing to dispatch to before that point.
    assert_audacity_window(verbose)
    wait_for_audacity_ready(verbose=verbose)


def main():
    print("This main is just for testing purposes.")
    assert_audacity()


if __name__ == "__main__":
    main()
