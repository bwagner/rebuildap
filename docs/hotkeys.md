# Running rebuildap from a keyboard shortcut

**Optional.** Nothing in rebuildap needs this; it is a convenience for the
commands that act on the project you already have open.

`quantize` and `transpose` operate on the *selected label track of the frontmost
Audacity project*. That makes them a natural fit for a global hotkey: you are
already in Audacity with a track selected and a region marked, and switching to a
terminal to type a command is the only awkward part. A hotkey removes it.

This works because the scripting pipe acts on the **frontmost project window**, so
a shortcut pressed while you are looking at a project targets that project by
construction. No project name, no path, no cwd.

## Which commands are worth binding

| command | bindable | why |
|---|---|---|
| `quantize` | yes | Writes into the project's own directory, resolved from the project stem. Independent of the working directory. |
| `transpose` | yes, via a picker | Same directory rule, but it takes two knobs (`SEMITONES` and `-s/--sharps`) and is **cumulative**, so it wants a selection step rather than a bare keypress — see [Transpose: pick the interval](#transpose-pick-the-interval). |
| `export` (no argument) | **no** | Writes into the **current directory**. A hotkey has no meaningful cwd, so the files would land wherever the launcher happened to start. |
| `build`, `import` | no | Both start from a file you have to name; there is nothing for a shortcut to infer. |

## Prerequisites

- Everything in the README's [Prerequisites](../README.md#prerequisites).
- [Hammerspoon](https://www.hammerspoon.org/). Any hotkey launcher works
  (Shortcuts, Automator Quick Actions, Keyboard Maestro, skhd); Hammerspoon is
  used here because its whole configuration is a plain text file, so the setup
  below can be pasted rather than clicked together.

## Install

The Hammerspoon side ships with rebuildap as
[`contrib/hammerspoon/rebuildap.lua`](../contrib/hammerspoon/rebuildap.lua) - a
module, not a snippet to copy, so fixes reach you with `git pull` instead of
needing a re-paste. Append to `~/.hammerspoon/init.lua`:

```lua
-- Lets the `hs` command-line tool talk to this instance (hs -c "...").
require("hs.ipc")

package.path = package.path
  .. ";" .. os.getenv("HOME") .. "/projects/rebuildap/contrib/hammerspoon/?.lua"
require("rebuildap"):bindHotkeys({
  quantize  = {{"cmd", "control", "shift"}, "Q"},
  transpose = {{"cmd", "control", "shift"}, "T"},
})
```

Point `package.path` at wherever you cloned rebuildap. Either binding may be
omitted; binding neither loads the module but registers nothing.

### Configuration

Defaults suit a standard install and need no configuration. To override, call
`setup` before `bindHotkeys`:

```lua
require("rebuildap").setup({
  binary      = "/opt/homebrew/bin/rebuildap",   -- default: auto-detected
  pathEntries = {os.getenv("HOME") .. "/tools"}, -- prepended to PATH
  logFile     = false,                           -- default: ~/.hammerspoon/rebuildap.log
  notify      = false,                           -- alerts only, no notifications
}):bindHotkeys({ ... })
```

**`binary`** must be an absolute path - `hs.task` does not search `$PATH` for the
executable itself. Left unset, the module takes the first of
`~/.local/bin/rebuildap`, `/opt/homebrew/bin/rebuildap`, `/usr/local/bin/rebuildap`
that exists. Ask it which one it picked:

```console
hs -c 'print(require("rebuildap").resolveBinary())'
```

That question is worth asking after any change to rebuildap itself: a
`uv tool install`ed copy is separate from your working tree and
[goes stale silently](../README.md#install). Run `uv tool install --reinstall .`
before expecting a source change to show up under a hotkey.

**`pathEntries`** replaces the directories prepended to the child's `PATH`. It has
to contain wherever `quantize_labels` lives (usually `~/bin`), because
`rebuildap quantize` locates it on `$PATH`. See [Gotchas](#gotchas) for why the
environment is built explicitly rather than inherited.

## Transpose: pick the interval

`transpose` differs from `quantize` in two ways that shape how it should be bound.

It takes **two** parameters — `SEMITONES` and the accidental spelling (`-s/--sharps`
against a flats default) — so a key-per-interval scheme would need twice the
bindings. And it is **cumulative**: firing it twice transposes twice, with no
error and a notification that looks identical to a single run. `quantize` is
idempotent, so a stray repeat costs nothing; a stray repeat of `transpose` leaves
the track a whole extra interval away, and the versioned `.txt` is rewritten
immediately. (It is recoverable — those `.txt` files are the ones you keep in git —
but the label track itself needs an Audacity undo.) Note also that `transpose 0`
is not a no-op: on a sharp-spelled track it respells to flats.

A picker answers both points at once: it carries the two knobs in one list, and
its selection step is the confirmation the non-idempotence deserves. The module
builds one with `hs.chooser` - 48 rows, every interval up and down in both
spellings - so binding `transpose` is all that is needed to get it.

Using it: press the shortcut, type to filter (`2` narrows to the whole-step rows,
`perfect 5th` to those four, `sharps` to the sharp-spelled half), then Return. Esc
dismisses without running anything.

The chooser shows ⌘1–⌘9 accelerators beside the visible rows. **They cannot collide
with other applications' shortcuts**: they are key equivalents on a focused panel,
not global bindings — `hs.hotkey.getHotkeys()` lists only the bindings you declared,
and while the chooser is open the focused window belongs to Hammerspoon. The mirror
of that is worth knowing: while it is open those keys do not reach Audacity either,
just as with any dialog. They address the *filtered* rows, so ⌘1 means "first match
for what I typed" rather than a fixed interval.

## Enable

1. Reload the configuration: Hammerspoon menubar icon > **Reload Config**.
   (`hs -c "hs.reload()"` also works, but only *after* the first reload has
   loaded `hs.ipc`.)
2. Grant Hammerspoon **Accessibility** permission when macOS asks - System
   Settings > Privacy & Security > Accessibility. rebuildap drives Audacity partly
   through GUI keystrokes, and the permission is attributed to the launching app,
   not to rebuildap. Without it the run fails with osascript errors.

## Verify

Open a project, select one label track and drag out a time region, then press the
shortcut. You should see a brief on-screen alert as it starts, then a notification with
the result. Either way it is appended to `~/.hammerspoon/rebuildap.log`:

```
2026-07-28 23:47:47  rebuildap quantize - ok
Label track 'chords' was already quantized; updated its file:
  /path/to/project/chords_<stem>.txt
```

Nothing visible happens *in Audacity* when the command is a no-op, so check the
log rather than the window.

**The log is authoritative, not the notification.** macOS silences notifications from
*every* app whenever it considers the display shared - measured 2026-07-29, where they
were delivered to Notification Center but never shown, for hours, unnoticed. If results
never appear, that is a system setting (*Allow notifications when mirroring or sharing
the display*), not a rebuildap fault.

A **failure** therefore also gets its own on-screen alert, naming the exit code and the
first line of output, which `hs.alert` draws directly and so survives that suppression:

```
rebuildap quantize - failed (exit 1)
Select exactly one label track; 2 are selected (parts, chords).
```

Success stays notification-and-log only - an alert per successful run would be noise,
and a run that quietly did nothing is the case the log exists for.

## No time region? It just does the whole track

**A selected label track with no time region is fine.** `quantize` and `transpose`
read the selection through Nyquist, which refuses a zero-length one with a modal:

> "Nyquist Prompt" requires one or more tracks to be selected.

The message blames track selection, but the missing thing is the *region* - measured
on 3.7.8, only label tracks selected, a region reads cleanly and no region raises this.
You reach that state by clicking inside a label track past its last label; clicking the
track's *header* always selects `first-label-start .. last-label-end`, so that gesture
never produces it.

rebuildap clears the dialog itself, within about a fifth of a second, and reads the
refusal as its answer: no region means the whole track. There is nothing to notice and
nothing to dismiss. This used to hang the command for 15s and could take Audacity down -
see `docs/audacity-quirks.md` for why the dismissal was never the dangerous part.

`-f` still skips the selection read altogether if you want to be explicit about it.

## Gotchas

**`hs.application.frontmostApplication()` cannot be trusted for the guard.**
Measured on macOS 26.3.1 with Hammerspoon holding Accessibility on an unlocked,
on-console session: it returned `loginwindow` while `app:isFrontmost()`,
`hs.window.focusedWindow()` and System Events all correctly named the real
frontmost application. The module's guard uses `:isFrontmost()` for that reason,
falling back to `hs.window.focusedWindow()` only to *name* whatever is in front.

**The frontmost check has to happen at keypress time, not in the chooser
callback.** Once the chooser is showing, Hammerspoon is frontmost and the check
would always fail - which is why the module splits `run()` (checks, then runs)
from `exec()` (runs unconditionally). After a row is chosen it reactivates
Audacity and waits briefly before starting rebuildap, because the pipe acts on
the frontmost project window.

**A GUI-launched task gets almost no environment.** `hs.task` inherits neither
`~/bin` nor `~/.local/bin`, so `quantize` fails in its `$PATH` lookup for
`quantize_labels`. `hs.execute(cmd, true)` runs a login shell and would fix the
PATH, but it also sources your shell profile - anything the profile prints lands
in the captured output and then in the notification. The module builds the
environment explicitly with `setEnvironment` instead, and passes `HOME` along
with `PATH`.

## Removing it

Delete the `package.path` and `require("rebuildap")` lines from
`~/.hammerspoon/init.lua` and reload the configuration. The log at
`~/.hammerspoon/rebuildap.log` can go too; nothing reads it.
