#!/usr/bin/env python

import json
import os
import subprocess
import time

import psutil
import pyaudacity as pa
import typer

"""
audacity_present.py


"""

# when mod-script-pipe worked out fine:
RESPONSE_OK = "\nBatchCommand finshed: OK\n"


def is_project_empty():
    return get_track_count() == 0


def get_track_count():
    return len(json.loads(pa.get_info("Tracks", "JSON")[: -len(RESPONSE_OK)]))


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


def assert_audacity_running(verbose: bool = True):
    if is_audacity_running():
        if verbose:
            print("Audacity is running.")
    else:
        if verbose:
            print("Audacity is not running. Starting it.")
        start_audacity()
        time.sleep(2)  # give it time to start


def assert_audacity_window(verbose: bool = True):
    if is_audacity_window_open() and is_project_empty():
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
    typer.run(main)
