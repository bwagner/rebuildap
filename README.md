# rebuildap

Keep your [Audacity](https://www.audacityteam.org/) label tracks in plain text:
export them, check them against the project, transform them in place, and rebuild
the whole project from them - so the huge binary `.aup3` never has to live in git.

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
rebuildap build mysong.mp3
```

You get `mysong.aup3` next to the audio, with `chords` and `parts` as label
tracks. Only the `.mp3` and the `.txt` files need to be in git.

Later, after editing labels in Audacity, check whether the project has drifted
from the versioned files:

```console
rebuildap check mysong.aup3
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

There are six commands:

| Command | What happens |
|---|---|
| `build AUDIO` | the audio (`.mp3`, `.wav`, anything Audacity imports) is imported, and the matching `*_stem.txt` files become label tracks |
| `export [AUP3]` | the project's label tracks are exported to individual `.txt` files; with no argument, the running Audacity project's are |
| `check [AUP3]` | the project's label tracks are compared against the versioned `.txt` files |
| `import LABELFILE` | one label file is added to the open project as a label track |
| `quantize [BEATS_TRACK]` | the open project's selected label track is snapped to a beats track, in place |
| `transpose SEMITONES` | the open project's selected label track has its chords transposed, in place |

`rebuildap` on its own means `rebuildap export`, so the common case stays a
single word.

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

The files are named after the open project's **`.aup3` stem**, so a transposed
copy `song_G.aup3` writes `chords_song_G.txt` and never touches the original's
`chords_song.txt` - even though both projects share a audio track called `song`.
With several projects open the **frontmost** one is used, which is also the one
Audacity's scripting pipe acts on; if something other than a project is in front,
rebuildap says so instead of guessing. See
[Which project the exported files are named after](docs/label-export.md#which-project-the-exported-files-are-named-after).

After a rebuild the project is saved as `<audio-stem>.aup3` **beside the audio
file**, not in the current directory, so `check` has something to look at and a
crash doesn't cost you the rebuild.

**An existing `.aup3` is never overwritten.** It's your working copy and may hold
edits the label files don't have. So `build` on audio whose `.aup3` is already
there stops immediately and tells you, rather than importing everything and
discarding the result at save time. Move the existing file aside to rebuild, or
pass `-n` / `--no-save` to rebuild into an unsaved window on purpose.

Saving also nudges the label files' mtimes up to match the new `.aup3`, so
`check` doesn't re-diff a project it just built. No label file's *content* is
touched — [the reasoning is here](docs/label-export.md#why-saving-touches-label-mtimes).

Label tracks are exported non-interactively through Audacity's scripting pipe, so
batch runs never stop for a dialog. There's a small precision trade-off:
[Exporting label tracks](docs/label-export.md).

## Usage

```console
usage: rebuildap [-h] [-V] COMMAND ...

Keep Audacity label tracks in plain text: export them, check them
against the project, transform them in place, and rebuild the whole
project from them.

positional arguments:
  COMMAND
    build        Rebuild a project from an audio file and its label files.
    export       Export label tracks to versioned .txt files.
    check        Compare a project's label tracks with the versioned .txt
                 files.
    import       Import a label file into the open project as a label track.
    quantize     Snap the selected label track to a beats track, in place.
    transpose    Transpose the selected label track's chords, in place.

options:
  -h, --help     show this help message and exit
  -V, --version  show program's version number and exit

Run `rebuildap COMMAND --help` for a command's own options.

`rebuildap` on its own means `rebuildap export`: the open project's
label tracks are written into the current directory - the selected
ones, or all of them if none are selected.
```

Each command's own options:

```console
usage: rebuildap build [-h] [-v] [-n] AUDIO

Import AUDIO into a new Audacity project, turning every *_<stem>.txt file
beside it into a label track, and save the result as <stem>.aup3 next to the
audio.

positional arguments:
  AUDIO          Audio file to rebuild from (.mp3, .wav, anything Audacity
                 imports). Every *_<stem>.txt beside it becomes a label track.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -n, --no-save  Don't save the rebuilt project as <audio-stem>.aup3 beside
                 the audio file. By default it is saved when no .aup3 exists
                 yet; an existing one is never overwritten.
```

```console
usage: rebuildap export [-h] [-v] [-f] [AUP3]

Write label tracks out as the versioned *_<stem>.txt files, named after the
project's .aup3 stem. With AUP3 they land beside that file; with no argument
the open project is used and they land in the current directory, which is
where these source-of-truth files belong.

positional arguments:
  AUP3           Project whose label tracks to export. Omit to export the open
                 Audacity project's - the selected tracks, or all of them if
                 none are selected.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -f, --force    Export the open project into the current directory even when
                 it appears to live elsewhere. Applies only without AUP3,
                 which exports beside itself.
```

```console
usage: rebuildap check [-h] [-v] [-f] [AUP3]

Report where the project and its versioned label files have diverged, and
update any .txt whose content changed. Label tracks that exist only in
Audacity are flagged too.

positional arguments:
  AUP3           Project to check. Omit to use the sole .aup3 in the current
                 directory.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -f, --force    Open Audacity and compare every label file even when all of
                 them are newer than the .aup3. Opens Audacity every run, and
                 catches label files rewritten (by e.g. git checkout, touch)
                 without changing the project.
```

```console
usage: rebuildap import [-h] [-v] LABELFILE

Add LABELFILE to the open Audacity project as a label track. The file may be
1-, 2- or 3-column; it is normalized on import and never rewritten.

positional arguments:
  LABELFILE      Label file to import; its <name>_<stem>.txt prefix names the
                 track.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
```

```console
usage: rebuildap quantize [-h] [-v] [-f] [BEATS_TRACK]

Snap the selected label track's boundaries to a beats label track already in
the open project, re-import it at its original position, and update its
versioned .txt to match.

positional arguments:
  BEATS_TRACK    Label track to snap to. Omit to auto-detect it: the sole
                 label track whose name starts with 'beat' (case-insensitive).

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -f, --force    Quantize the whole track without reading the time selection.
                 By default only the boundaries inside an active selection are
                 snapped.
```

```console
usage: rebuildap transpose [-h] [-v] [-s] [-f] SEMITONES

Transpose the chords in the open project's selected label track by SEMITONES
half steps and update its versioned .txt to match. Label text that is not a
chord (section markers, lyric cues, fingerings) is left alone and reported.

positional arguments:
  SEMITONES      Half steps to transpose by; negative transposes down.

options:
  -h, --help     show this help message and exit
  -v, --verbose  Enable verbose mode.
  -s, --sharps   Spell transposed chords with sharps (A#) instead of the
                 default flats (Bb).
  -f, --force    Transpose the whole track without reading the time selection.
                 By default only the labels starting inside an active
                 selection are transposed.
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
- For `quantize` only: [quantize_labels](https://github.com/bwagner/quantize_labels)
  on your `$PATH` (as `quantize_labels.py` or `quantize_labels`), which does the
  snapping. `transpose` needs nothing extra — its sister
  [transpose](https://github.com/bwagner/transpose) is a declared dependency and
  is installed with `rebuildap`.

## Install

```console
cd <project_root>
uv tool install --reinstall .
```

This builds and installs `rebuildap` as a globally available tool. Omit
`--reinstall` for the first install.

### Optional: a keyboard shortcut

`quantize` and `transpose` act on the selected label track of the project you are
looking at, so they can be driven from a global hotkey instead of a terminal — the
scripting pipe targets the frontmost project window, which is the one in front of
you. The Hammerspoon side ships with the repo as
`contrib/hammerspoon/rebuildap.lua`; [setting it up](docs/hotkeys.md) is two lines
in your Hammerspoon config.
Entirely optional, and not every command suits it: a no-argument `export` writes
into the current directory, which a hotkey does not meaningfully have.

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
- [Running rebuildap from a keyboard shortcut](docs/hotkeys.md) — optional; which
  commands suit a hotkey and which don't, and the two environment traps that make
  a GUI-launched run behave differently from a terminal one.
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
- [quantize_labels](https://github.com/bwagner/quantize_labels) — does the
  snapping behind `quantize`
- [transpose](https://github.com/bwagner/transpose) — does the chord
  transposition behind `transpose`
- [beats2bars](https://github.com/bwagner/beats2bars)
- [audacity_legatize](https://github.com/bwagner/audacity_legatize)
- [pyaudacity](https://github.com/bwagner/pyaudacity)

## Links

- [Audacity and Nyquist](https://www.audacity-forum.de/download/edgar/nyquist/nyquist-doc/devel/audacity-nyquist-en.htm)
- [AudioNyq](https://audionyq.com/)
- [Audacity Scripting Reference](https://manual.audacityteam.org/man/scripting_reference.html)

## Thank You

- [Steve Daulton](https://github.com/SteveDaulton) for the Nyquist-Script
