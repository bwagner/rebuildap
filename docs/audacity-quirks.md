# Audacity quirks and the workarounds they forced

Audacity is a hostile dependency: it crashes on documented paths, and its
scripting pipe can silently swallow commands. Much of `rebuildap` is
workarounds, each one deliberate and each one paid for by a real failure.
This page records what went wrong and why the code looks the way it does.

Before "simplifying" any of it, read the relevant section here - several
obvious-looking cleanups have been tried and reverted.

Back to the [README](../README.md).

## Audacity cold-start race

Running `rebuildap -c` across many projects uncovered an Audacity crash when
commands reached a freshly-started instance too quickly. Crash signature:

- `EXC_BAD_ACCESS / KERN_INVALID_ADDRESS` at address `0x220` (near-null pointer deref)
- Faulting module: `lib-menus.dylib`
- Process uptime at crash: **~5 seconds**
- `mod-script-pipe.so` was delivering a `Close:` command at crash time

Audacity's mod-script-pipe becomes available **earlier** than the menu
subsystem. If a command that routes through menus (notably `Close:`)
arrives during that window, the menu dispatcher dereferences a
not-yet-initialized pointer and Audacity crashes.

**Workarounds:**

1. **Readiness probe** (`audacity_present.wait_for_audacity_ready`): after
   starting or detecting Audacity, probe `GetInfo: Type=Tracks` — a
   pipe-only query that does not touch menus — with exponential backoff
   until it responds. If the probe needed retries (a proxy for cold
   start), add a short settling delay before returning so menu init can
   finish. Eliminates the cold-start (~5 s uptime) crash.

2. **Close via AppleScript Cmd-W instead of `Close:`** (via
   `audacity_present.close_owned_window`). Repeated
   `pa.do("Close:")` cycles across many projects eventually hit the same
   menu-dispatch null-deref even on a "warm" Audacity (observed: uptime
   ~327 s after a handful of open/close cycles). Sending Cmd-W via
   AppleScript routes the window close through AppKit's event dispatch
   rather than the scripting pipe's menu-command path, sidestepping the
   buggy path.

## Which window gets closed

Cmd-W closes whatever window is *frontmost*, which is not necessarily the
one `rebuildap` opened. If focus moved in the meantime — you clicked your
own project, or a dialog stole it — a blind Cmd-W would close your window,
discard unsaved work, and leave a `Save changes?` dialog that then wedges
the scripting pipe.

So `rebuildap` never closes blind. Audacity titles a saved or opened
project window with its `.aup3` stem, and `close_owned_window` closes a
window only after confirming that title is frontmost — raising it first if
focus moved. If the window is gone, or two windows share the title (every
*empty* project window is titled `Audacity`), it refuses, explains why on
stderr, and leaves everything open. A stray window costs nothing; closing
the wrong one costs your work.

For the same reason a rebuilt project's window is closed only once it has
been **saved**: closing an unsaved project raises `Save changes?`. With
`-n` / `--no-save`, or when an `.aup3` already exists, the window is left
open on purpose.

These are workarounds, not fixes — the underlying bug is in Audacity and
should be reported upstream. But together they're sufficient to run
`rebuildap` across many projects without crashes.

## Audacity running without a project window

A separate failure with the same symptom (`TimeoutError: Audacity scripting
pipe did not respond`). If Audacity is running but has **no project window** —
either no window at all, or only a dialog such as `About Audacity` — then
mod-script-pipe still creates and holds the FIFOs, accepts commands, and
never answers them. Measured on 3.7.8: a raw `GetInfo: Type=Tracks` in this
state returns a single newline instead of a response.

This state persists across runs, so every invocation failed identically until
Audacity was given a project window.

**Fix:** ordering. `assert_audacity` now runs

1. `assert_audacity_running` — process only (and, if we launched it, wait for a window)
2. `assert_audacity_window` — guarantee a project window, opening one with Cmd-N
3. `wait_for_audacity_ready` — *then* probe the pipe

Previously readiness was probed in step 1, before any project window was
guaranteed — so it waited for an answer that could not arrive. On timeout the
error now also lists the open window titles.

Two related traps, both since removed:

- **The readiness probe must not leak fds.** It used to run `pa.do()` in a
  daemon thread; on timeout that thread stayed blocked in `readline()` holding
  *both* pipes open and then consumed the *next* probe's response. One timeout
  poisoned every later probe in the process. It now talks to the FIFOs
  directly and always closes them.
- **No cheap "is the pipe listening?" pre-check.** Opening the write end with
  `O_WRONLY | O_NONBLOCK` and closing it reads to Audacity as a client
  connecting and hanging up: it ends the session and reopens both FIFOs,
  breaking the round-trip that follows. It also could not distinguish a
  healthy pipe from a project-less one, since both hold the FIFO open.

Timing on 3.7.8, from a window-less instance: the project window appears
~2.7 s after Cmd-N and the pipe starts answering at ~3.4 s, so the readiness
budget must clear that comfortably.

## Failures that used to surface as the same timeout

Three environmental problems produced an identical, uninformative timeout.
Each now reports what is actually wrong:

- **mod-script-pipe not enabled.** The FIFOs are created at Audacity startup,
  so if they never appear the module is inactive — waiting cannot help.
  `wait_for_audacity_ready` now raises `ScriptPipeUnavailableError` after a
  short grace period, naming the module and the preference to enable.
- **No Accessibility permission.** Every GUI action (activate, Cmd-N, Cmd-W,
  listing windows) goes through `osascript`, and each used to be run with its
  return code ignored — so a refused keystroke was invisible and only showed
  up as a later timeout. All of them now go through `run_osascript`, which
  reports failures on stderr and adds a hint when macOS refused assistive
  access. Note this means rebuildap needs a real, unlocked GUI login session.
- **Ambiguous window titles.** An empty project window is titled `Audacity`,
  so opening a second one adds no new *title*. New-window detection compares
  window counts as well as titles.

`tests/test_audacity_present.py` covers all three by faking the environment, so no
running Audacity is required.

## Projects saved by an older Audacity

Opening an `.aup3` written by an older Audacity raises a modal **"Project update
required"** dialog. Until someone acknowledges it, `OpenProject2:` never
returns, so the caller times out with nothing to distinguish it from a wedged
pipe — which made every pre-upgrade project in a batch sweep look like a
scripting failure.

A watcher now acknowledges that one dialog while the open is in flight
(`audacity_present.dismissing_upgrade_dialog`). It cannot be pre-empted, because
the dialog only appears once the command is already running; and waiting for the
timeout first would mean recovering through the abandoned-thread path, which is
documented above to eat the next call's response. Watching concurrently keeps
the command on its normal, successful path.

Two details make this safe rather than presumptuous:

- **Opening does not modify the file.** The dialog says "*Once saved*, the
  project can only be opened with Audacity version 3.7 or newer" — the
  conversion happens on save, and `-c` never saves. Verified by md5: a project
  file was byte-identical after being opened with the dialog dismissed.
- **The watcher only ever touches that one dialog**, matched on its static text.
  Several modal dialogs can be stacked at once (an `Error Opening Project`
  alert, an `Applying Open Project2...` progress window), and clicking whichever
  happens to be frontmost would be the same class of mistake as a bare Cmd-W.

The dialog has **no window title** — it appears in the window list as an empty
name — so unlike everything else here it cannot be found by title. It offers
`OK` and nothing else (`AXCancelButton` is `missing value`, so Escape does
nothing), which makes acknowledging the only way past it.

Timing on 3.7.8: the dialog appears ~0.18 s after the command is sent and the
open completes ~0.03 s after it is dismissed. The 5 s per-attempt timeout was
never too tight — the whole budget was being spent waiting on a human.

## Projects that are already open

Opening a project Audacity already has open raises a different modal alert —
**`Error Opening Project`** / "`<name>` is already open in another window."
(buttons `OK` **and** `Cancel`) — which blocks `OpenProject2:` the same way.

This one is **refused, not dismissed**. Two reasons:

- **It can be pre-empted.** Audacity titles a project window with its `.aup3`
  stem, the same fact `close_owned_window` relies on, so
  `audacity_present.project_window_open` can see the condition *before* any
  command is sent — and sending the command is what raises the alert.
- **Clicking it is the one dismissal with evidence against it.** Audacity
  **exited immediately** the one time this alert was dismissed with an
  osascript click, with no crash report written. Cause unknown.

So `open_project` raises `ProjectAlreadyOpenError` and touches nothing. Check
mode reports it and moves on to the next project rather than aborting a sweep,
and deliberately skips its `close_owned_window` step — that window is not ours
to close, and it may hold edits the label files do not have.

The check runs **before `assert_audacity`**, not only inside `open_project`, so
a project that is going to be skipped costs nothing at all. An already-open
project has tracks, so it fails the empty-project probe and `assert_audacity`
answers with a Cmd-N — which would leave a stray empty window behind for every
project a sweep skips. Measured: 0.22 s and no new window, against 1.8 s and one
stray window when the check sat behind `assert_audacity`.

Running that early needs two guards (`audacity_funcs.project_already_open`):

- **`.aup3` input only.** Audio rebuilds into a *new* project and can never
  raise the alert, yet it shares its stem with the project it builds
  (`angie.opus` -> `angie.aup3`), so a stem check alone would refuse to rebuild
  whenever the old project happened to be open.
- **Only when Audacity is running.** Listing the windows of a process that does
  not exist is an osascript *error*, duly reported on stderr, so asking
  unconditionally would put a spurious failure in front of the user on every
  cold start. A stopped Audacity cannot have anything open anyway.

One blind spot remains, degrading to the old behaviour (a timeout) rather than
to anything worse, so the timeout message now names this as a likely cause:
window titles carry no path, so two same-named projects in different directories
are indistinguishable. A false positive costs a skipped project; it never opens
the wrong one. This is now known to be permanent — see below.

## What window titles can and cannot tell you

Probed directly on 3.7.8 (2026-07-18), across empty / open-saved / modified /
saved-as states, since several of the workarounds above rest on it:

- **A modified project window shows the bare stem — there is no dirty marker.**
  So `project_window_open` and `close_owned_window` do *not* go blind on a
  project with unsaved edits, which had been an open question.
- **The flip side: a title cannot tell you whether a window is safe to Cmd-W.**
  Saved and unsaved-modified look identical, and `AXModified` does not exist on
  Audacity windows (`-1728`, "can't get attribute"). Nothing can check "is this
  window dirty?" before closing it, so that safety rests entirely on flow
  construction: close only what you opened, and only after saving.
- **`AXDocument` is `missing value` in every state.** Audacity is wxWidgets and
  does not expose a document path, so there is **no** route to per-window file
  identity — neither via AX nor via `GetInfo`, whose types are only `Commands`,
  `Menus`, `Preferences`, `Tracks`, `Clips`, `Envelopes`, `Labels`, `Boxes`.
  Same-stem projects in different directories are permanently indistinguishable.
- Saving retitles the window to the new stem.
- **An *unsaved* project built by importing audio is also titled with that
  audio's stem** — not `Audacity`. Observed 2026-07-18 running `rebuildap -n
  song.opus`: the window came up as `song` despite never being saved. (Only an
  *empty* project window is titled `Audacity`.) So "title == stem" does **not**
  imply "a saved project on disk at that path". Both current users of that
  inference degrade safely — `project_window_open` would refuse a check as
  "already open" when the match is really an unsaved rebuild, and
  `close_owned_window` sees two identical titles and refuses as ambiguous — but
  anything new relying on the title should not assume a file exists behind it.

## When an .aup3 changes on disk

An `.aup3` is a SQLite database — hence the `.aup3-shm` / `.aup3-wal` companions
next to an open project, and hence all three being in the `.gitignore` advice.
It runs in [WAL (write-ahead log)](https://sqlite.org/wal.html) mode: verified
`PRAGMA journal_mode = wal` on a copy, with `page_size` 65536 × `page_count`
1326 = 86,900,736 bytes, exactly the file size.

Measured on 3.7.8 (2026-07-18) by snapshotting md5, size and sidecars at every
step of open -> edit -> undo -> save-as-elsewhere -> close -> quit:

| step | md5 | size | `-wal` |
|---|---|---|---|
| before anything | `62744d9aa160` | 86,900,736 | 0 |
| project open, untouched | `62744d9aa160` | 86,900,736 | 0 |
| after `NewLabelTrack:` | **`92c1bfb1c49f`** | 86,900,736 | 131,152 |
| after `Undo:` | **`61ba21111e3a`** | 86,900,736 | 131,152 |
| after save-as **elsewhere** | **`56be671822ae`** | **86,675,456** | gone |
| after closing the window | `56be671822ae` | 86,675,456 | gone |
| after quitting Audacity | `56be671822ae` | 86,675,456 | gone |

What that actually shows, as against the plausible guess that a checkpoint on
close is responsible:

- **The main file changes the moment you edit** — not at close. Its bytes differ
  right after `NewLabelTrack:`, while its size is unchanged and the WAL has
  grown.
- **Undo does not restore it.** It changes the bytes again, to a third value.
- **Save-as *elsewhere* is what rewrites and shrinks the original**, dropping the
  `-wal` / `-shm` sidecars with it. Detaching the old database checkpoints and
  tidies it; it does not put it back the way it was.
- **Closing the window and quitting change nothing further.** The damage, such as
  it is, is already done by then.
- Merely *opening* a project is safe — byte-identical, confirmed here and in a
  separate run.

So: assume any write-touching operation dirties the file immediately, regardless
of where you later save.

This matches the upstream explanation in
[audacity#9161](https://github.com/audacity/audacity/issues/9161) — the issue
`check_label_age`'s docstring cites — which is **closed as not-planned**
(2025-08-27). A maintainer's comment there gives the cause directly, and it is
worth quoting because it is the real mechanism rather than an inference from
file bytes:

> Every edit operation you do is saved to the project file even before you press
> "Save" button, so the undo and crash recovery systems can do their work. At the
> same time, this changes the file even if the project data is intact.

It is inherent to the project format, not a bug awaiting a fix. The Audacity 4
idea floated there is a temporary copy on open, synced on close
([PR #8405](https://github.com/audacity/audacity/pull/8405), explicitly not
production-ready). So treat this as permanent behaviour to design around.

One trap for tooling: opening an `.aup3` with SQLite **read-only still creates
`-shm` and `-wal` sidecars** next to it (the main file's bytes are untouched).
A read-only inspection is therefore not a no-op on the directory.
