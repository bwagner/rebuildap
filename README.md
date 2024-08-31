# rebuildap

rebuildap: rebuild [Audacity](https://www.audacityteam.org/) Project

This imports a song in audio format (mp3, wav, anything Audacity can import) into Audacity along
with label files that might be lying around, conforming to the following naming convention:
Name of the input file: (e.g. june_p1ht.mp3), stem of the file, with "*_" prepended and with ".txt"
appended, e.g. *_june_p1ht.txt. This is the glob.

## Purpose
Audacity aup3 files are huge. If you want to maintain them in a repository, every change
to your labels will generate a new version of the binary aup3 file.

Solution: Regenerate the aup3 file from the original audio material + your labels. Now you're only
keeping track of the (rarely changing) audio material and the (more often changing) textual label
files.

## Prerequisites:
    - You need Audacity
    - Enable Prefernces>Modules>[mod-script-pipe](https://manual.audacityteam.org/man/scripting.html)
    - Install [Nyquist](https://manual.audacityteam.org/man/nyquist.html) script:
      https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny
      Audacity: Nyquist Plugin Installer> navigate to ImportLabels.ny
      - Press Apply
      - Restart Audacity
    - Install pyaudacity from fork:
      pip install git+https://github.com/bwagner/pyaudacity
    - pip install psutil

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

## Thank You
- Steve Daulton for the Nyquist-Script
