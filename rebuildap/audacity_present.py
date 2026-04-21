#!/usr/bin/env python

import errno
import os
import subprocess
import sys
import threading
import time

import psutil
import pyaudacity as pa

from . import audacity_funcs as af

_SCRIPT_PIPE_TO = f"/tmp/audacity_script_pipe.to.{os.getuid()}"


def _is_script_pipe_listening() -> bool:
    """Return True iff mod-script-pipe has the 'to-Audacity' FIFO open for reading.

    Uses a non-blocking write open: on Unix, opening a FIFO with
    ``O_WRONLY | O_NONBLOCK`` succeeds only if a reader is currently
    attached; otherwise the kernel returns ENXIO immediately. Costs <1 ms
    and doesn't involve any round-trip to Audacity.

    Note: a True result means the FIFO has a reader, not that Audacity is
    actually progressing commands (a wedged mod-script-pipe may still hold
    the fd). Use as a cheap pre-check; follow with an end-to-end probe.
    """
    try:
        fd = os.open(_SCRIPT_PIPE_TO, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        return True
    except OSError as e:
        if e.errno in (errno.ENXIO, errno.ENOENT):
            return False
        raise


"""
audacity_present.py


"""


def is_audacity_window_open():
    """
    Checks whether Audacity window is open.
    """
    script = """
    tell application "System Events"
        set audacityWindows to (name of windows of process "Audacity")
        if length of audacityWindows is greater than 0 then
            return true
        else
            return false
        end if
    end tell
    """
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip() == "true"


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


def bring_audacity_window_to_front_as():
    """
    Brings Audacity window to the front and opens new project
    using AppleScript.
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
    subprocess.run(["osascript", "-e", script])


def close_audacity_window_as():
    script = """
    tell application "Audacity" to activate
    tell application "System Events"
        keystroke "w" using command down
    end tell
    """
    subprocess.run(["osascript", "-e", script])


def _probe_tracks_with_timeout(timeout_per_probe: float):
    """Run one ``GetInfo: Type=Tracks`` probe with a hard timeout.

    Returns the parsed tracks list on success, or ``None`` on failure/timeout.

    Why threading? ``pa.do()`` opens Audacity's FIFO for writing, which
    **blocks indefinitely** on Unix until mod-script-pipe has opened the
    read side. A plain ``try/except`` around ``pa.do()`` cannot time out
    during cold start. We run the probe in a daemon thread and give up on
    it after ``timeout_per_probe`` seconds. A stuck thread is acceptable
    — daemon threads die with the process.
    """
    result: dict = {"tracks": None}

    def worker():
        try:
            raw = pa.do("GetInfo: Type=Tracks")
            result["tracks"] = af._parse_tracks_response(raw)
        except Exception:  # noqa: BLE001 — any failure means "not ready yet"
            pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout_per_probe)
    return result["tracks"]


def wait_for_audacity_ready(timeout: float = 10.0, verbose: bool = False) -> None:
    """Poll Audacity's scripting pipe until it responds to a read-only query.

    Works around an Audacity bug: on a freshly started instance, commands
    that route through the menu subsystem (e.g. ``Close:``) can arrive
    before menus are fully initialized, causing a null-pointer dereference
    in ``lib-menus.dylib`` (observed signature:
    ``EXC_BAD_ACCESS / KERN_INVALID_ADDRESS`` at ``0x220``, process uptime
    ~5 s, ``mod-script-pipe.so`` delivering ``Close:`` at crash time).

    The scripting pipe becomes available earlier than the menu subsystem.
    We probe with ``GetInfo: Type=Tracks`` (pipe-only, no menu dispatch)
    with exponential backoff. Each probe has its own hard timeout because
    ``pa.do`` blocks on FIFO open until mod-script-pipe attaches on the
    read side. If any probe needed more than one attempt — a proxy for
    "Audacity was just cold-started" — we add a short settling delay so
    the menu subsystem can finish initializing before the caller sends
    commands like ``Close:``.

    Raises ``TimeoutError`` if the pipe does not respond within ``timeout``
    seconds. See README > Comments > Audacity cold-start race.
    """
    start = time.monotonic()
    delay = 0.5
    attempt = 0
    while time.monotonic() - start < timeout:
        attempt += 1
        # Cheap pre-check: is mod-script-pipe even attached to the FIFO?
        # Saves us from burning the per-probe timeout during startup.
        if not _is_script_pipe_listening():
            time.sleep(delay)
            delay = min(delay * 1.5, 2.0)
            continue
        # Drain any stale bytes left by a previously timed-out probe before
        # attempting the next round-trip (diagnostic printed to stderr).
        drained = af._drain_read_pipe()
        if drained:
            preview = drained[:200].decode("utf-8", errors="replace")
            if len(drained) > 200:
                preview += "..."
            print(
                f"[pipe drain] discarded {len(drained)} stale bytes: {preview!r}",
                file=sys.stderr,
            )
        remaining = timeout - (time.monotonic() - start)
        per_probe = min(3.0, max(0.5, remaining))
        if _probe_tracks_with_timeout(per_probe) is not None:
            break
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)
    else:
        raise TimeoutError(f"Audacity scripting pipe did not respond within {timeout}s")
    if attempt > 1:
        # Probe needed retries → cold start. Give the menu subsystem a short
        # beat to finish initializing before the caller issues a menu-routed
        # command such as Close:.
        time.sleep(1.5)
        if verbose:
            elapsed = time.monotonic() - start
            print(f"Audacity ready after {elapsed:.1f}s (cold start).")


def assert_audacity_running(verbose: bool = True):
    if is_audacity_running():
        if verbose:
            print("Audacity is running.")
    else:
        if verbose:
            print("Audacity is not running. Starting it.")
        start_audacity()
    # Always probe readiness — Audacity may have just been launched (by us
    # or by the user) and its menu subsystem may not be initialized yet.
    wait_for_audacity_ready(verbose=verbose)


def assert_audacity_window(verbose: bool = True):
    if is_audacity_window_open() and af.is_project_empty():
        if verbose:
            print("An Audacity window is open. Will use this.")
    else:
        if verbose:
            print("Bringing Audacity window to the front with a new project.")
        bring_audacity_window_to_front_as()
        time.sleep(1)  # give it time


def assert_audacity(verbose: bool = True):
    assert_audacity_running(verbose)
    assert_audacity_window(verbose)


def main():
    print("This main is just for testing purposes.")
    assert_audacity()


if __name__ == "__main__":
    main()
