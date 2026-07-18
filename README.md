# rebuildap

rebuildap: rebuild [Audacity](https://www.audacityteam.org/) Project

## Purpose

Audacity aup3 files are huge. If you want to maintain them in a repository, every change
to your labels will generate a new version of the binary aup3 file.

Solution: Regenerate the aup3 file from the original audio material + your labels. Now you're only
keeping track of the (rarely changing) original audio source material and the (more often changing)
textual label files.

```console
usage: rebuildap [-h] [-v] [-l] [-c] [-n] [-V] [filename]

rebuild Audacity project

positional arguments:
  filename       The audio file name.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -l, --label    Import label file.
  -c, --check    Check whether audacity file newer than label files and show
                 differences.
  -n, --no-save  Don't save the rebuilt project as <audio-stem>.aup3 beside
                 the audio file. By default it is saved when no .aup3 exists
                 yet; an existing one is never overwritten.
  -V, --version  show program's version number and exit
```

When providing an audio file (mp3, wav, anything Audacity can import) for the
`filename` argument, that file is imported into Audacity along with label files
that might be lying next to it, conforming to the following naming convention:
Stem of the input file name with `.txt` appended and anything prepended ending
in `_` is considered a label file. E.g. with this input audio file
`mysong.mp3` all files `*_mysong.txt` are considered related label files.

When providing an aup3 file, its label tracks are exported individually.

When not providing a file at all, a running instance of Audacity with a project
containing label tracks is searched for, of which the selected label tracks are
exported or all if none are selected.

After a rebuild, the project is saved as `<audio-stem>.aup3` **beside the audio
file** (not in the current directory), so `rebuildap -c` has something to check
and a crash doesn't cost you the rebuild. An existing `.aup3` is never
overwritten — it is your working copy and may hold edits the label files don't
have. Pass `-n` / `--no-save` to skip saving entirely.

Saving also touches the label files' mtimes to match the new `.aup3`. Without
that, `-c` would treat every label file as older than the project and re-export
and diff it on every run, forever: it only rewrites a label file when the
content actually diverges, so the condition would never clear. The rebuild
proves the two agree, so recording that is accurate — no label file's *content*
is modified.

Label tracks are exported non-interactively via the scripting pipe
(`GetInfo: Type=Labels`), so batch runs never stop for a dialog. See
[Comments](#two-ways-to-export-label-tracks) for the precision trade-off and why
the interactive alternative was retired.

## Recommendation

In order to consistently prevent git from tracking Audacity files, add these lines to
the `.gitignore` file of your project:

```
*.aup3
*.aup3-shm
*.aup3-wal
```

In addition, every contributor to your project should
configure [git-lfs locks](https://github.com/git-lfs/git-lfs/wiki/File-Locking) to prevent concurrent
modifications of binary files:
```console
cd your_dir_containing_audio_labels_and_aup3_files
git config lfs.locksverify true
```


## Prerequisites

- macOS. (Windows and Linux are not yet supported)
- You need [Audacity](https://www.audacityteam.org/)
- Enable Preferences>Modules>mod-script-pipe [mod-script-pipe](https://manual.audacityteam.org/man/scripting.html)
- Install [Nyquist](https://manual.audacityteam.org/man/nyquist.html) script:
  [ImportLabels.ny](https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny)
  Audacity: Tools> Nyquist Plugin Installer> navigate to `ImportLabels.ny`
    - Press Apply
    - Restart Audacity
- [uv](https://docs.astral.sh/uv/)

## Install

```console
cd <project_root>
uv tool install --reinstall .
```

This uses [uv](https://docs.astral.sh/uv/) to build and install `rebuildap` as a
globally available tool. Omit `--reinstall` for the first install.

## TODO

- allow additional audio tracks
- write a text file with the used sources to reconstruct the aup3.
  Allow also this file as input to the script, which then will
  sheepishly import the files mentioned (instead of being smart)
- more tests
- Currently only macOS, no Windows/Linux
- write instructions for:
    - replacing label track
    - replacing audio track
    - adding new label track
    - removing label track
- add command line option to ignore all labels.
- add command line option to ignore certain labels.

## Contribute

```console
git clone https://github.com/bwagner/rebuildap
cd rebuildap
pre-commit install
```

if `pre-commit install` fails, issue `pip install pre-commit` (see [pre-commit](https://pre-commit.com/))

## Comments

### Two ways to export label tracks

Audacity doesn't support exporting label tracks selectively: when exporting via
File > Export Other > Export Labels…, all label tracks get concatenated into a
single file. There's
a [workaround](https://forum.audacityteam.org/t/export-individual-label-when-multiple-labels-in-project/58799/32)
— temporarily remove all but one label track, export, then undo.

The downside of the workaround is that it requires the user to click through
the save dialog once per label track. Audacity's scripting pipe (`GetInfo:
Type=Labels`) offers a non-interactive alternative, at the cost of some
[precision](https://github.com/audacity/audacity/issues/4220).

Empirical comparison (on a project with beat-quantized labels plus one
manually-placed sub-beat label `C7#9`):

| source                                  | start       | end         |
|-----------------------------------------|-------------|-------------|
| interactive `ExportLabels:` (dialog)    | `125.437458`| `126.671804`|
| `GetInfo: Type=Labels` (non-interactive)| `125.437`   | `126.672`   |

`GetInfo` truncates to **3 decimals** while the interactive export preserves
**6 decimals**. Maximum rounding error: ~0.5 ms, i.e. ~22 samples @ 44.1 kHz —
inaudible but not sample-accurate.

For beat-quantized labels (the common case, e.g. output of
[`DBNDownBeatTracker`](https://github.com/CPJKU/madmom/blob/main/bin/DBNDownBeatTracker)
rounded to 2 decimals) both paths yield identical files.

`rebuildap` uses the non-interactive `GetInfo` path exclusively. The interactive
dialog path was offered behind `-p` / `--precise` until 2026-07-18 and has been
retired: `ExportLabels:` takes **no parameters** (see the
[scripting reference](https://manual.audacityteam.org/man/scripting_reference.html#:~:text=Description-,ExportLabels%3A,-Export%20Labels)),
so the save dialog alone decided where each artifact landed and the caller had
to guess that location afterwards — it read them back from the current working
directory, which was wrong whenever `rebuildap` was invoked from elsewhere.
Combined with one dialog click per label track, the ~0.5 ms precision edge did
not justify keeping a second export path alive.

A [Nyquist](https://manual.audacityteam.org/man/nyquist.html) plug-in route
was investigated and rejected: Nyquist-side label access also goes through
`aud-get-info` (see Steve Daulton's `ExportAllLabelTracks1.ny` for reference),
so a custom Nyquist plug-in would have the **same 3-decimal precision floor**
as our direct `GetInfo` path — no advantage.

### Audacity cold-start race

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

### Which window gets closed

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

### Audacity running without a project window

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

### Failures that used to surface as the same timeout

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

### Projects saved by an older Audacity

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

### Projects that are already open

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

Two blind spots remain, both degrading to the old behaviour (a timeout) rather
than to anything worse, so the timeout message now names this as a likely cause:

- Window titles carry no path, so two same-named projects in different
  directories are indistinguishable. A false positive costs a skipped project;
  it never opens the wrong one.
- Whether a project with *unsaved* edits still titles its window exactly with
  its stem is unverified. If Audacity adds a dirty marker, the check misses it.

## See also

- [audacity_click_label](https://github.com/bwagner/audacity_click_label)
- [audacity_shift_labels](https://github.com/bwagner/audacity_shift_labels)
- [quantize_labels](https://github.com/bwagner/quantize_labels)
- [beats2bars](https://github.com/bwagner/beats2bars)
- [audacity_legatize](https://github.com/bwagner/audacity_legatize)
- [pyaudacity](https://github.com/bwagner/pyaudacity)

## Links

- [Audacity and Nyquist](https://www.audacity-forum.de/download/edgar/nyquist/nyquist-doc/devel/audacity-nyquist-en.htm)
- [AudioNyq](https://audionyq.com/)
- [Audacity Scripting Reference](https://manual.audacityteam.org/man/scripting_reference.html)

## Thank You

- [Steve Daulton](https://github.com/SteveDaulton) for the Nyquist-Script
