"""Which Hammerspoon shortcuts run rebuildap, or how to set them up.

The shortcuts live in ``contrib/hammerspoon/rebuildap.lua``, loaded from the
user's ``~/.hammerspoon/init.lua``. Whether they are bound is asked of the
*running* Hammerspoon over its ``hs`` command-line tool, so the answer is what is
bound right now. A state file written by the module would go stale the moment its
``require`` lines were removed, and parsing ``init.lua`` breaks on any other way of
writing the config. See decisions.md 2026-10-01 10:24.

Asking must never change Hammerspoon: the query reads ``package.loaded`` rather
than calling ``require`` (which would load the module into a config that never
asked for it), and ``hs`` is not run at all when Hammerspoon is not running,
because it then prompts whether to launch it.
"""

import enum
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import psutil

HS = "hs"
HAMMERSPOON_PROCESS = "Hammerspoon"
MODULE_NAME = "rebuildap"
REPOSITORY_URL = "https://github.com/bwagner/rebuildap"
HOTKEYS_DOC = "docs/hotkeys.md"

# `hs -t`: its send and receive timeout. A healthy answer measured under 10 ms
# (2026-10-01); this only bounds a wedged instance. The subprocess timeout sits a
# little above it so hs gets to report its own failure first.
HS_TIMEOUT_SECONDS = 2
_SUBPROCESS_TIMEOUT_SECONDS = HS_TIMEOUT_SECONDS + 1

# The commands the module can bind, and what pressing each one does.
SHORTCUT_COMMANDS = {
    "quantize": "snap the selected label track to its beats track",
    "transpose": "pick an interval, then transpose the selected label track",
}
# The bindings the install snippet suggests. docs/hotkeys.md shows the same ones.
SUGGESTED_MODS = ("cmd", "control", "shift")
SUGGESTED_KEYS = {"quantize": "Q", "transpose": "T"}

# Returns {"loaded": false} or {"loaded": true, "bindings": <table or false>};
# `false` is a module loaded before it started recording its bindings.
_QUERY = (
    f'local m = package.loaded["{MODULE_NAME}"]; '
    'if type(m) ~= "table" then return hs.json.encode({loaded = false}) end; '
    "return hs.json.encode({loaded = true, bindings = m.bindings or false})"
)

_INDENT = "  "


class Status(enum.Enum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not installed"
    OUTDATED = "outdated"
    UNKNOWN = "unknown"


@dataclass
class HotkeyState:
    status: Status
    bindings: dict = field(default_factory=dict)
    reason: str = ""


def _hammerspoon_running():
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] == HAMMERSPOON_PROCESS:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


def parse_reply(text):
    """Turn the query's JSON reply into a :class:`HotkeyState`."""
    try:
        data = json.loads(text)
    except ValueError:
        return HotkeyState(
            Status.UNKNOWN, reason=f"Hammerspoon replied {text.strip()!r}"
        )
    if not data.get("loaded"):
        return HotkeyState(Status.NOT_INSTALLED)
    bindings = data.get("bindings")
    if bindings is False:
        return HotkeyState(Status.OUTDATED)
    # hs.json encodes an empty Lua table as [], so anything empty means none.
    return HotkeyState(Status.INSTALLED, bindings=dict(bindings or {}))


def probe():
    """Ask the running Hammerspoon which rebuildap shortcuts it has bound."""
    hs = shutil.which(HS)
    if hs is None:
        return HotkeyState(
            Status.UNKNOWN,
            reason=f"Hammerspoon's `{HS}` command-line tool is not on PATH",
        )
    if not _hammerspoon_running():
        return HotkeyState(Status.UNKNOWN, reason="Hammerspoon is not running")
    try:
        result = subprocess.run(
            [hs, "-q", "-t", str(HS_TIMEOUT_SECONDS), "-c", _QUERY],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return HotkeyState(Status.UNKNOWN, reason="Hammerspoon did not answer")
    except OSError as e:
        return HotkeyState(Status.UNKNOWN, reason=f"`{HS}` could not be run: {e}")
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or "no message"
        return HotkeyState(
            Status.UNKNOWN,
            reason=f"`{HS}` failed (exit {result.returncode}): {message}",
        )
    return parse_reply(result.stdout)


def module_dir():
    """The directory holding the Lua module, when rebuildap runs from a clone.

    The module is not part of the installed package, so a copy install has no
    local path to offer and the snippet points at the repository instead.
    """
    candidate = Path(__file__).resolve().parent.parent / "contrib" / "hammerspoon"
    return candidate if (candidate / f"{MODULE_NAME}.lua").is_file() else None


def install_snippet(directory):
    """The Lua to append to ``~/.hammerspoon/init.lua``."""
    where = str(directory) if directory else "/path/to/rebuildap/contrib/hammerspoon"
    mods = ", ".join(f'"{m}"' for m in SUGGESTED_MODS)
    width = max(len(c) for c in SUGGESTED_KEYS)
    lines = [
        '-- Lets the `hs` command-line tool talk to this instance (hs -c "...").',
        'require("hs.ipc")',
        "",
        f'package.path = package.path .. ";{where}/?.lua"',
        f'require("{MODULE_NAME}"):bindHotkeys({{',
        *(
            f'  {command.ljust(width)} = {{{{{mods}}}, "{key}"}},'
            for command, key in SUGGESTED_KEYS.items()
        ),
        "})",
    ]
    return "\n".join(lines)


def _doc_location(directory):
    if directory:
        return str(directory.parent.parent / HOTKEYS_DOC)
    return f"{REPOSITORY_URL}/blob/main/{HOTKEYS_DOC}"


def _install_text(directory):
    # Unindented, so it can be copied into init.lua exactly as printed.
    snippet = install_snippet(directory)
    clone = (
        ""
        if directory
        else f"Clone {REPOSITORY_URL} and use its contrib/hammerspoon directory.\n"
    )
    return (
        f"To install them, append this to ~/.hammerspoon/init.lua:\n\n"
        f"{snippet}\n\n"
        f"{clone}"
        "Then reload the config (Hammerspoon menu bar icon > Reload Config) and\n"
        "grant Hammerspoon Accessibility permission when macOS asks.\n"
        f"Guide: {_doc_location(directory)}"
    )


def _binding_lines(bindings):
    width = max(len(c) for c in SHORTCUT_COMMANDS)
    lines = []
    for command, does in SHORTCUT_COMMANDS.items():
        binding = bindings.get(command)
        if binding:
            keys = "+".join([*binding.get("mods", []), binding.get("key", "")])
            shown = f"{binding.get('idx', '')}  {keys}".strip()
            lines.append(f"{_INDENT}{command.ljust(width)}  {shown}  - {does}")
        else:
            lines.append(f"{_INDENT}{command.ljust(width)}  not bound")
    return lines


def report(state, directory):
    """What `rebuildap hotkeys` prints for ``state``."""
    if state.status is Status.INSTALLED and state.bindings:
        return "\n".join(
            [
                "Hammerspoon shortcuts (they act on the frontmost Audacity project):",
                *_binding_lines(state.bindings),
                f"Guide: {_doc_location(directory)}",
            ]
        )
    if state.status is Status.OUTDATED:
        return (
            "Hammerspoon has rebuildap's shortcut module loaded, but from before it\n"
            "reported its bindings. Reload the config (Hammerspoon menu bar icon >\n"
            "Reload Config) and run this again."
        )
    if state.status is Status.UNKNOWN:
        return (
            f"Cannot tell whether rebuildap's shortcuts are installed: {state.reason}.\n"
            f"\n{_install_text(directory)}"
        )
    return f"No rebuildap shortcuts are bound in Hammerspoon.\n\n{_install_text(directory)}"


def show():
    print(report(probe(), module_dir()))
