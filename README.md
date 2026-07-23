# rebuildap

Rebuild an [Audacity](https://www.audacityteam.org/) project from your audio file
plus plain-text label files — so the huge binary `.aup3` never has to live in git.

Audacity `.aup3` files are large. Keep one in a repository and every tweak to a
label writes a fresh copy of the whole binary. `rebuildap` lets you version the
things that actually change — small, diffable `.txt` label files — and regenerate
the project from them whenever you need it.

## Quick start

Say you have a song and its labels sitting side by side:

```console
mysong.mp3
chords_mysong.txt
parts_mysong.txt
```

Build the Audacity project from them:

```console
rebuildap mysong.mp3
```

You get `mysong.aup3` next to the audio, with `chords` and `parts` as label
tracks. Only the `.mp3` and the `.txt` files need to be in git.

Later, after editing labels in Audacity, check whether the project has drifted
from the versioned files:

```console
rebuildap -c mysong.aup3
```

That reports a diff per label track and updates any `.txt` whose content changed.
It also flags any label track that exists only in Audacity, with no `.txt` file
yet — select those tracks in Audacity and run `rebuildap` with no arguments in
the project directory to export them.

## How it works

Label files are matched by name: for input `mysong.mp3`, every `*_mysong.txt`
beside it is treated as a label track, and the prefix becomes the track name
(`chords_mysong.txt` -> a track called `chords`).

A label file may be 1-column (`time`, e.g. raw beat times), 2-column
(`time<TAB>text`, e.g. `DBNDownBeatTracker` downbeat numbers), or 3-column
(`start<TAB>end<TAB>text`, Audacity's own export format). All three are accepted
and normalized on import; the versioned files are never rewritten. A line that
fits none of these is rejected up front, naming the file and line
([details](docs/label-export.md#importing-labels-normalizing-the-input-format)).

There are three ways to call it:

| You pass… | What happens |
|---|---|
| an audio file (`.mp3`, `.wav`, anything Audacity imports) | it's imported, and the matching `*_stem.txt` files become label tracks |
| an `.aup3` project | its label tracks are exported to individual `.txt` files |
| nothing | the running Audacity project is used — selected label tracks are exported, or all of them if none are selected |

With no argument the `.txt` files are written **only into the current
directory** — these are the versioned source of truth, so they are never
scattered elsewhere. If the current directory is not the open project's own
directory, rebuildap looks it up in Audacity's **Open Recent** menu and, when it
finds it, prints where the project lives and asks you to `cd` there — exporting
nothing (pass `-f` / `--force` to export into the current directory anyway).
Only when it cannot suggest anywhere does it say so and export into the current
directory anyway. Each export is reported (the track name, then its full path on
its own line), so a successful run is never silent and its destination is always
visible.

After a rebuild the project is saved as `<audio-stem>.aup3` **beside the audio
file**, not in the current directory, so `-c` has something to check and a crash
doesn't cost you the rebuild.

**An existing `.aup3` is never overwritten.** It's your working copy and may hold
edits the label files don't have. So rebuilding audio whose `.aup3` is already
there stops immediately and tells you, rather than importing everything and
discarding the result at save time. Move the existing file aside to rebuild, or
pass `-n` / `--no-save` to rebuild into an unsaved window on purpose.

Saving also nudges the label files' mtimes up to match the new `.aup3`, so `-c`
doesn't re-diff a project it just built. No label file's *content* is touched —
[the reasoning is here](docs/label-export.md#why-saving-touches-label-mtimes).

Label tracks are exported non-interactively through Audacity's scripting pipe, so
batch runs never stop for a dialog. There's a small precision trade-off:
[Exporting label tracks](docs/label-export.md).

## Usage

```console
usage: rebuildap [-h] [-v] [-l] [-c] [-d] [-n] [-f] [-q [BEATS_TRACK]] [-V]
                 [filename]

rebuild Audacity project

positional arguments:
  filename              Audio to rebuild from, or an .aup3 to export from.
                        Omit to export the open Audacity project's labels (see
                        Input modes below).

options:
  -h, --help            show this help message and exit
  -v, --verbose         Enable verbose mode.
  -l, --label           Import label file.
  -c, --check           Check whether Audacity file is newer than label files
                        and show differences.
  -d, --deep            With -c, compare every label file against the
                        project's label tracks, even ones newer than the
                        .aup3. Opens Audacity every run; catches label files
                        rewritten (git checkout, touch) without changing the
                        project.
  -n, --no-save         Don't save the rebuilt project as <audio-stem>.aup3
                        beside the audio file. By default it is saved when no
                        .aup3 exists yet; an existing one is never
                        overwritten.
  -f, --force           Force. For the no-argument export: export into the
                        current directory even when the open project appears
                        to live elsewhere (Open Recent). For -q: quantize the
                        whole track instead of only the current time
                        selection. Does not override the never-overwrite rule.
  -q, --quantize [BEATS_TRACK]
                        Quantize the selected label track in the open project
                        to a beats label track already in it, in place: label
                        boundaries inside the current time selection snap to
                        the beats grid (the whole track when nothing is
                        selected, or with -f), the track is re-imported at its
                        original position, and its versioned .txt is updated
                        to match. Give a track name to pick the reference, or
                        omit it to auto-detect it.
  -V, --version         show program's version number and exit

Input modes:
  audio file   imported; matching *_<stem>.txt become label tracks
  .aup3        its label tracks are exported to .txt files
  (nothing)    the open Audacity project is used — selected label
               tracks are exported, or all of them if none are selected
```

## Prerequisites

- **macOS.** Windows and Linux are not supported yet. `rebuildap` drives Audacity
  partly through GUI keystrokes, so it needs a real, unlocked login session.
- [Audacity](https://www.audacityteam.org/)
- Enable [mod-script-pipe](https://manual.audacityteam.org/man/scripting.html)
  under Preferences > Modules > mod-script-pipe
- The [Nyquist](https://manual.audacityteam.org/man/nyquist.html) plug-in
  [ImportLabels.ny](https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny):
  Tools > Nyquist Plugin Installer > pick `ImportLabels.ny` > Apply > restart Audacity.
  This step exists only because mod-script-pipe cannot import labels from a file;
  if [audacity#7171](https://github.com/audacity/audacity/issues/7171) ever lands,
  the plug-in becomes unnecessary.
- [uv](https://docs.astral.sh/uv/)

## Install

```console
cd <project_root>
uv tool install --reinstall .
```

This builds and installs `rebuildap` as a globally available tool. Omit
`--reinstall` for the first install.

## Keeping .aup3 files out of git

Add these to your project's `.gitignore`:

```
*.aup3
*.aup3-shm
*.aup3-wal
```

An `.aup3` is a SQLite database running in
[WAL (write-ahead log)](https://sqlite.org/wal.html) mode, so an open project is
accompanied by `-shm` and `-wal` files — none of the three belong in git. Note
that the `.aup3` changes on disk as soon as you edit a project, and an undo does
not put it back: [the measurements are here](docs/audacity-quirks.md#when-an-aup3-changes-on-disk).

If several people work on the same project, have everyone configure
[git-lfs locks](https://github.com/git-lfs/git-lfs/wiki/File-Locking) so two of
you can't modify a binary at once:

```console
cd your_dir_containing_audio_labels_and_aup3_files
git config lfs.locksverify true
```

## Under the hood

Automating Audacity is harder than it looks — it crashes on documented paths, and
its scripting pipe can silently swallow commands. Most of this codebase is
workarounds, each one paid for by a real failure. If you're curious, or about to
change something and wondering why it's written that way:

- [Audacity quirks and the workarounds they forced](docs/audacity-quirks.md) —
  the cold-start crash, which window gets closed and why it's never blind, modal
  dialogs that wedge the scripting pipe, and what window titles can and can't
  tell you.
- [Exporting label tracks](docs/label-export.md) — the two available export
  paths, their precision difference, and why the interactive one was retired.
- [Roadmap](docs/roadmap.md) — what's still missing.

## Contribute

```console
git clone https://github.com/bwagner/rebuildap
cd rebuildap
pre-commit install
```

If `pre-commit install` fails, run `pip install pre-commit` (see
[pre-commit](https://pre-commit.com/)).

Tests: `uv run pytest` for the offline suite. Tests marked `audacity` drive a
real Audacity and are deselected by default; `uv run pytest -m audacity` runs
them and needs a GUI login session.

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
