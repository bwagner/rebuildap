# rebuildap

rebuildap: rebuild [Audacity](https://www.audacityteam.org/) Project

```shell
rebuildap.py mysong.mp3
```

This imports a song in audio format (mp3, wav, anything Audacity can import) into Audacity along
with label files that might be lying next to it, conforming to the following naming convention:
Name of the input file: (e.g. `mysong.mp3`), stem of the file, with `*_` prepended and with `.txt`
appended, e.g. `*_mysong.txt`. This is the file glob.

## Purpose
Audacity aup3 files are huge. If you want to maintain them in a repository, every change
to your labels will generate a new version of the binary aup3 file.

Solution: Regenerate the aup3 file from the original audio material + your labels. Now you're only
keeping track of the (rarely changing) original audio source material and the (more often changing)
textual label files.

## Prerequisites:
 - You need Audacity
 - Enable Preferences>Modules>mod-script-pipe [1]
 - Install Nyquist [2] script:
   [ImportLabels.py](https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny)
   Audacity: Nyquist Plugin Installer> navigate to `ImportLabels.ny`
   - Press Apply
   - Restart Audacity
 - Install pyaudacity from fork:
   `pip install git+https://github.com/bwagner/pyaudacity`
 - `pip install psutil`

## TODO:
 - make installable
     - pyproject.toml etc.
 - add command line option to ignore all labels.
 - add command line option to ignore certain labels.
 - add support for "dependencies": Only recreate the
   aup3 file if any of the labels or the audio are
   newer than the aup3.
 - make git ignore aup3 files
 - find a way to export a single label track from Audacity.
 - write instructions for:
   - replacing label track
   - replacing audio track
   - adding new label track
   - removing label track

## Links
1. [mod-script-pipe](https://manual.audacityteam.org/man/scripting.html)
2. [Nyquist](https://manual.audacityteam.org/man/nyquist.html)
## Thank You
- Steve Daulton for the Nyquist-Script
