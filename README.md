# rebuildap

rebuildap: rebuild [Audacity](https://www.audacityteam.org/) Project

## Purpose
Audacity aup3 files are huge. If you want to maintain them in a repository, every change
to your labels will generate a new version of the binary aup3 file.

Solution: Regenerate the aup3 file from the original audio material + your labels. Now you're only
keeping track of the (rarely changing) original audio source material and the (more often changing)
textual label files.

```console
 Usage: rebuildap.py [OPTIONS] [FILENAME]

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   filename      [FILENAME]  The audio file name. [default: None]             │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --verbose  -v        Enable verbose mode.                                    │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

When providing an audio file (mp3, wav, anything Audacity can import) for the
`filename` argument, that file is imported into Audacity along with label files
that might be lying next to it, conforming to the following naming convention:
Stem of the input file name with `.txt` appended and anything prepended ending
in `_` is considered a label file.  E.g. with this input audio file
`mysong.mp3` all files `*_mysong.txt` are considered related label files.

When providing an aup3 file, its label tracks are exported individually.

When not providing a file at all, a running instance of Audacity with a project
containing label tracks is searched for, of which the selected label tracks are
exported or all if none are selected.

Note that exporting label tracks is forcedly interactive, as the respective scripting
command [ExportLabels](https://manual.audacityteam.org/man/scripting_reference.html#:~:text=Description-,ExportLabels%3A,-Export%20Labels)
fails to offer a non-interactive mode.

## Prerequisites:
 - macOS. (Windows and Linux are not yet supported)
 - You need Audacity
 - Enable Preferences>Modules>mod-script-pipe [mod-script-pipe](https://manual.audacityteam.org/man/scripting.html)
 - Install [Nyquist](https://manual.audacityteam.org/man/nyquist.html) script:
   [ImportLabels.py](https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny)
   Audacity: Nyquist Plugin Installer> navigate to `ImportLabels.ny`
   - Press Apply
   - Restart Audacity
 - Install pyaudacity from fork:
   `pip install git+https://github.com/bwagner/pyaudacity`
 - `pip install psutil`

## Build Installable Package
```console
python -m venv ~/venv/rap
source ~/venv/rap/bin/activate
cd <project_root>
./mkdist.py
```
This creates a `dist` directory containing the installable package.
Install it via:
```console
pip install dist/rebuildap-0.0.1-py3-none-any.whl
```

## TODO:
 - allow additional audio tracks
 - write a text file with the used sources to reconstruct the aup3.
   allow also this file as input to the script, which then will
   sheepishly import the files mentioned (instead of being smart)
 - more tests
 - Currently only macOS, no Windows/Linux
 - add support for "dependencies": Only recreate the
   aup3 file if any of the labels or the audio are
   newer than the aup3.
 - make git ignore aup3 files
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

## Comments
Audacity doesn't support exporting label tracks selectively: When exporting via File>Export Other>Export Labels..,
all labels get thrown together into the same file.
There's a [workaround](https://forum.audacityteam.org/t/export-individual-label-when-multiple-labels-in-project/58799/32),
However, GetInfo unfortunately exports labels with [limited precision](https://github.com/audacity/audacity/issues/4220).
Thus, when exporting labels, we temporarily delete all but one label track at a time, export that track, undo the deletion,
etc.

## Links
- [Audacity and Nyquist](https://www.audacity-forum.de/download/edgar/nyquist/nyquist-doc/devel/audacity-nyquist-en.htm)
- [AudioNyq](https://audionyq.com/)
- [Audacity Scripting Reference](https://manual.audacityteam.org/man/scripting_reference.html)
- [Typer](https://typer.tiangolo.com/tutorial/)

## Thank You
- [Steve Daulton](https://github.com/SteveDaulton) for the Nyquist-Script
