# rebuildap

rebuildap: rebuild [Audacity](https://www.audacityteam.org/) Project

## Purpose

Audacity aup3 files are huge. If you want to maintain them in a repository, every change
to your labels will generate a new version of the binary aup3 file.

Solution: Regenerate the aup3 file from the original audio material + your labels. Now you're only
keeping track of the (rarely changing) original audio source material and the (more often changing)
textual label files.

```console
usage: rebuildap [-h] [-v] [-l] [-c] [-p] [-V] [filename]

rebuild Audacity project

positional arguments:
  filename       The audio file name.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -l, --label    Import label file.
  -c, --check    Check whether audacity file newer than label files and show
                 differences.
  -p, --precise  Use the interactive ExportLabels dialog (6-decimal precision)
                 instead of the default non-interactive GetInfo path
                 (3-decimal precision). See README Comments.
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

Note that exporting label tracks is forcedly interactive, as the respective scripting
command [ExportLabels](https://manual.audacityteam.org/man/scripting_reference.html#:~:text=Description-,ExportLabels%3A,-Export%20Labels)
fails to offer a non-interactive mode.

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
- Install pyaudacity from fork:
  `pip install git+https://github.com/bwagner/pyaudacity`
- `pip install psutil`
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

For beat-quantized labels (the common case, e.g. output of `DBNDownBeatTracker`
rounded to 2 decimals) both paths yield identical files.

`rebuildap` uses the non-interactive `GetInfo` path by default. Pass `-p` /
`--precise` to fall back to the interactive dialog path when exact fidelity on
sub-beat labels matters.

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

2. **Close via AppleScript Cmd-W instead of `Close:`** (check_label_age
   uses `audacity_present.close_audacity_window_as`). Repeated
   `pa.do("Close:")` cycles across many projects eventually hit the same
   menu-dispatch null-deref even on a "warm" Audacity (observed: uptime
   ~327 s after a handful of open/close cycles). Sending Cmd-W via
   AppleScript routes the window close through AppKit's event dispatch
   rather than the scripting pipe's menu-command path, sidestepping the
   buggy path.

These are workarounds, not fixes — the underlying bug is in Audacity and
should be reported upstream. But together they're sufficient to run
`rebuildap` across many projects without crashes.

## See also

- [audacity_click_label](https://github.com/bwagner/audacity_click_label)
- [shift_labels](https://github.com/bwagner/shift_labels)
- [quantize_labels](https://github.com/bwagner/quantize_labels)
- [beats2bars](https://github.com/bwagner/beats2bars)
- [audacity_legatize](https://github.com/bwagner/audacity_legatize)
- [pyaudacity](https://github.com/bwagner/pyaudacity)

## Links

- [Audacity and Nyquist](https://www.audacity-forum.de/download/edgar/nyquist/nyquist-doc/devel/audacity-nyquist-en.htm)
- [AudioNyq](https://audionyq.com/)
- [Audacity Scripting Reference](https://manual.audacityteam.org/man/scripting_reference.html)
- [Typer](https://typer.tiangolo.com/tutorial/)

## Thank You

- [Steve Daulton](https://github.com/SteveDaulton) for the Nyquist-Script
