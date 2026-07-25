"""Keeps the offline suite genuinely offline.

The suite used to be offline only by *control flow*: several tests call
``check_label_age`` with no fakes at all and never reach Audacity purely because
an early return fires first. That is fragile. On 2026-07-25 a mutation-test run
disabled one such early return, and the suite drove the user's real Audacity and
handed it a pytest temp file containing four bytes — raising a modal "Error
Opening File or Project" dialog that had to be dismissed by hand, and that can
wedge the scripting pipe.

So every test that is not marked ``audacity`` gets the outside world removed:
touching Audacity now fails loudly instead of doing it. A test that wants the
live layer faked in a particular way still monkeypatches it as before — the test
body runs after this fixture, so its own patches win.

The blocked names are the places where this package actually reaches out:

- ``pa.do``                  — every scripting-pipe command
- ``start_audacity``         — ``os.system('open -a "Audacity"')``
- ``_probe_tracks_with_timeout`` — talks to the FIFOs directly, bypassing pa.do
- ``subprocess.run``         — every AppleScript / GUI keystroke goes through
                               ``run_osascript`` -> here, as does the
                               quantize_labels shell-out

``run_osascript`` itself is deliberately *not* blocked: several tests unit-test its
own logic (splitting, stripping, the Accessibility hint) with ``subprocess.run``
faked beneath it, which is legitimate offline work and still cannot reach osascript.

Tests marked ``audacity`` are exempt: they are meant to drive a real Audacity and
are deselected by default (``addopts = "-m 'not audacity'"``).
"""

import subprocess

import pytest

from rebuildap import audacity_funcs as af
from rebuildap import audacity_present as ap

AUDACITY_MARKER = "audacity"


def _forbidden(what):
    """A stand-in that fails the test rather than reaching the outside world."""

    def blocked(*args, **kwargs):
        detail = ""
        if args:
            detail = f" (called with {args[0]!r})"
        raise AssertionError(
            f"Offline test reached the live layer: {what}{detail}.\n"
            "Fake it in the test (monkeypatch), or mark the test "
            f"@pytest.mark.{AUDACITY_MARKER} if it is meant to drive a real "
            "Audacity. See tests/conftest.py."
        )

    return blocked


@pytest.fixture(autouse=True)
def no_live_audacity(request, monkeypatch):
    """Block every route to a real Audacity unless the test opts in."""
    if request.node.get_closest_marker(AUDACITY_MARKER):
        return  # this test is *supposed* to drive Audacity

    monkeypatch.setattr(af.pa, "do", _forbidden("pa.do"), raising=False)
    monkeypatch.setattr(ap, "start_audacity", _forbidden("start_audacity"))
    monkeypatch.setattr(
        ap, "_probe_tracks_with_timeout", _forbidden("_probe_tracks_with_timeout")
    )
    # Beneath osascript and the quantize_labels shell-out. Tests that legitimately
    # fake a subprocess call patch it again in their own body, which wins.
    monkeypatch.setattr(subprocess, "run", _forbidden("subprocess.run"))
