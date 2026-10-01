"""`rebuildap hotkeys`: which Hammerspoon shortcuts are bound, or how to bind them.

The answer comes from the running Hammerspoon itself, asked over `hs -c`, so it
reports what is bound right now rather than what some config file once said.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rebuildap import hotkeys


def _reply(loaded, bindings=None):
    """What `hs -q -c <query>` prints: the query's JSON, unquoted."""
    data = {"loaded": loaded}
    if loaded:
        data["bindings"] = bindings
    return json.dumps(data)


QUANTIZE = {"mods": ["cmd", "control", "shift"], "key": "Q", "idx": "⌘⌃⇧Q"}
TRANSPOSE = {"mods": ["cmd", "control", "shift"], "key": "T", "idx": "⌘⌃⇧T"}


# --- reading Hammerspoon's reply --------------------------------------------


def test_module_not_loaded_means_not_installed():
    state = hotkeys.parse_reply(_reply(False))
    assert state.status is hotkeys.Status.NOT_INSTALLED


def test_module_without_a_binding_record_is_outdated():
    """A Hammerspoon still running the module from before it recorded its
    bindings: loaded, but nothing to report until the config is reloaded."""
    state = hotkeys.parse_reply(_reply(True, False))
    assert state.status is hotkeys.Status.OUTDATED


def test_bound_shortcuts_are_reported():
    state = hotkeys.parse_reply(
        _reply(True, {"quantize": QUANTIZE, "transpose": TRANSPOSE})
    )
    assert state.status is hotkeys.Status.INSTALLED
    assert state.bindings["quantize"]["key"] == "Q"
    assert state.bindings["transpose"]["idx"] == "⌘⌃⇧T"


def test_an_empty_binding_record_reads_as_nothing_bound():
    # hs.json encodes an empty Lua table as a JSON array, not an object.
    state = hotkeys.parse_reply('{"loaded": true, "bindings": []}')
    assert state.status is hotkeys.Status.INSTALLED
    assert state.bindings == {}


def test_an_unreadable_reply_is_unknown_not_a_crash():
    state = hotkeys.parse_reply("not json at all")
    assert state.status is hotkeys.Status.UNKNOWN
    assert "not json at all" in state.reason


# --- asking Hammerspoon -----------------------------------------------------


def _no_subprocess(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("hs was run")

    monkeypatch.setattr(subprocess, "run", forbidden)


def test_without_the_hs_tool_nothing_is_run(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(hotkeys, "_hammerspoon_running", lambda: True)
    _no_subprocess(monkeypatch)

    state = hotkeys.probe()
    assert state.status is hotkeys.Status.UNKNOWN
    assert "`hs`" in state.reason


def test_with_hammerspoon_not_running_hs_is_never_run(monkeypatch):
    """`hs` asks the user whether to launch Hammerspoon when it is not running,
    which would hang a command that was only asked a question."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/opt/homebrew/bin/hs")
    monkeypatch.setattr(hotkeys, "_hammerspoon_running", lambda: False)
    _no_subprocess(monkeypatch)

    state = hotkeys.probe()
    assert state.status is hotkeys.Status.UNKNOWN
    assert "not running" in state.reason


def _hs_answers(monkeypatch, returncode=0, stdout="", stderr="", exc=None):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if exc:
            raise exc
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(shutil, "which", lambda _name: "/opt/homebrew/bin/hs")
    monkeypatch.setattr(hotkeys, "_hammerspoon_running", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_the_reply_is_parsed(monkeypatch):
    _hs_answers(monkeypatch, stdout=_reply(True, {"quantize": QUANTIZE}) + "\n")
    state = hotkeys.probe()
    assert state.status is hotkeys.Status.INSTALLED
    assert set(state.bindings) == {"quantize"}


def test_the_query_cannot_load_or_launch_anything(monkeypatch):
    """Asking must not change Hammerspoon: `require` would load the module into a
    config that never asked for it, `-A` would launch Hammerspoon, and an open
    stdin is where `hs` would wait for an answer to its launch prompt."""
    calls = _hs_answers(monkeypatch, stdout=_reply(False))
    hotkeys.probe()

    ((command, kwargs),) = calls
    assert "-A" not in command
    assert not any("require" in str(part) for part in command)
    assert kwargs.get("stdin") is subprocess.DEVNULL
    assert kwargs.get("timeout")


def test_an_hs_failure_is_unknown_with_its_message(monkeypatch):
    # Without hs.ipc loaded in the config, hs cannot reach the instance.
    _hs_answers(monkeypatch, returncode=69, stderr="can't access Hammerspoon\n")
    state = hotkeys.probe()
    assert state.status is hotkeys.Status.UNKNOWN
    assert "can't access Hammerspoon" in state.reason


def test_an_hs_timeout_is_unknown(monkeypatch):
    _hs_answers(monkeypatch, exc=subprocess.TimeoutExpired("hs", 3))
    state = hotkeys.probe()
    assert state.status is hotkeys.Status.UNKNOWN


# --- what the user sees -----------------------------------------------------


MODULE_DIR = Path("/clone/rebuildap/contrib/hammerspoon")


def _text(state, module_dir=MODULE_DIR):
    return hotkeys.report(state, module_dir)


def test_bound_shortcuts_are_listed_with_their_keys():
    state = hotkeys.HotkeyState(
        hotkeys.Status.INSTALLED,
        bindings={"quantize": QUANTIZE, "transpose": TRANSPOSE},
    )
    text = _text(state)
    for command, binding in (("quantize", QUANTIZE), ("transpose", TRANSPOSE)):
        line = next(line for line in text.splitlines() if command in line)
        assert binding["idx"] in line
        assert "+".join(binding["mods"] + [binding["key"]]) in line
    assert "require(" not in text  # installed: no install snippet


def test_an_unbound_command_is_named_as_such():
    state = hotkeys.HotkeyState(
        hotkeys.Status.INSTALLED, bindings={"quantize": QUANTIZE}
    )
    line = next(line for line in _text(state).splitlines() if "transpose" in line)
    assert "not bound" in line


@pytest.mark.parametrize(
    "state",
    [
        hotkeys.HotkeyState(hotkeys.Status.NOT_INSTALLED),
        hotkeys.HotkeyState(hotkeys.Status.INSTALLED, bindings={}),
    ],
    ids=["module not loaded", "loaded, nothing bound"],
)
def test_without_shortcuts_the_install_snippet_is_shown(state):
    text = _text(state)
    assert 'require("hs.ipc")' in text
    assert f'{MODULE_DIR}/?.lua"' in text
    assert "bindHotkeys" in text


def test_without_a_local_clone_the_snippet_points_at_the_repository():
    text = _text(hotkeys.HotkeyState(hotkeys.Status.NOT_INSTALLED), module_dir=None)
    assert hotkeys.REPOSITORY_URL in text


def test_an_outdated_module_asks_for_a_reload():
    text = _text(hotkeys.HotkeyState(hotkeys.Status.OUTDATED))
    assert "Reload Config" in text


def test_unknown_names_the_reason_and_still_says_how_to_install():
    state = hotkeys.HotkeyState(
        hotkeys.Status.UNKNOWN, reason="Hammerspoon is not running"
    )
    text = _text(state)
    assert "Hammerspoon is not running" in text
    assert "bindHotkeys" in text


@pytest.mark.skipif(shutil.which("luac") is None, reason="luac not installed")
def test_the_install_snippet_is_valid_lua(tmp_path):
    """The snippet is pasted into init.lua, so it must parse as Lua - quoting
    and escaping are what a structural test would miss."""
    from conftest import _REAL_SUBPROCESS_RUN

    text = _text(hotkeys.HotkeyState(hotkeys.Status.NOT_INSTALLED))
    snippet = hotkeys.install_snippet(MODULE_DIR)
    assert snippet in text
    lua = tmp_path / "init.lua"
    lua.write_text(snippet)
    result = _REAL_SUBPROCESS_RUN(
        ["luac", "-p", str(lua)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# --- the command line -------------------------------------------------------


def test_hotkeys_is_a_command(monkeypatch):
    import rebuildap

    calls = []
    monkeypatch.setattr(rebuildap, "_show_hotkeys", lambda: calls.append(True))
    assert rebuildap.main(["hotkeys"]) == 0
    assert calls == [True]


@pytest.mark.parametrize(
    "argv", [["--help"], ["quantize", "--help"], ["transpose", "--help"]]
)
def test_help_points_at_the_hotkeys_command(argv, capsys):
    import rebuildap

    with pytest.raises(SystemExit):
        rebuildap.main(argv)
    assert "rebuildap hotkeys" in capsys.readouterr().out
