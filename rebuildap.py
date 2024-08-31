#!/usr/bin/env python

import glob
import sys
from pathlib import Path

import pyaudacity as pa
import typer

import audacity_present

"""
rebuildap.py song.mp3

rebuildap: rebuild Audacity Project

This imports a song in audio format (mp3, wav, anything Audacity can import) into Audacity along
with label files that might be lying around, conforming to the following naming convention:
Name of the input file: (e.g. june_p1ht.mp3), stem of the file, with "*_" prepended and with ".txt"
appended, e.g. *_june_p1ht.txt. This is the glob.

Purpose: Audacity aup3 files are huge. If you want to maintain them in a repository, every change
to your labels will generate a new version of the binary aup3 file.

Solution: Regenerate the aup3 file from the original audio material + your labels. Now you're only
keeping track of the (rarely changing) audio material and the (more often changing) textual label
files.

Prerequisites:
    - You need Audacity
    - Enable Prefernces>Modules>mod-script-pipe
    - Install Nyquist script:
      https://audionyq.com/wp-content/uploads/2022/09/ImportLabels.ny
      Audacity: Nyquist Plugin Installer> navigate to ImportLabels.ny
      - Press Apply
      - Restart Audacity
    - Install pyaudacity from fork:
      pip install git+https://github.com/bwagner/pyaudacity
    - pip install psutil

"""

"""
TODO:
    - add command line option to ignore all labels.
    - add command line option to ignore certain labels.
    - add support for "dependencies": Only recreate the
      aup3 file if any of the labels or the audio are
      newer than the aup3.
    - make git ignore aup3 files
    - write instructions for:
        - replacing label track
        - replacing audio track
        - adding new label track
        - removing label track
"""

# when mod-script-pipe worked out fine:
RESPONSE_OK = "\nBatchCommand finshed: OK\n"


def make_label_track(label_file, label_track_name):
    pa.do("SelectTracks: Track=0 Mode=Set")
    pa.do(f"ImportLabels: fname={label_file}")
    pa.do(f"SelectTracks: Track={audacity_present.get_track_count() - 1} Mode=Set")
    pa.do(f"SetTrack: Name={label_track_name}")


def create_labels_glob(abs_path: Path):
    dirname = abs_path.parent
    return glob.glob(f"{dirname}/*_{abs_path.stem}.txt")


def t_main(
    filename: str = typer.Argument(..., help="The audio file name."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose mode."),
):

    if len(sys.argv) < 2:
        print(f"{Path(__file__).name} filename")
        return

    audacity_present.assert_audacity(verbose)

    abs_path = Path(filename).expanduser().resolve()

    if verbose:
        print(f"Importing {abs_path}")
    pa.import_audio(abs_path)
    if verbose:
        print(f"Done importing {abs_path}")
    label_files = create_labels_glob(abs_path)
    for lfile in label_files:
        lblname = Path(lfile).stem.replace(f"_{abs_path.stem}", "")
        if verbose:
            print(f"labels: >{lblname}<")
        make_label_track(lfile, lblname)


def custom_help_check() -> None:
    """
    Adds command line options -h and -? in addition to the default --help to
    show help output.
    """
    if "-h" in sys.argv or "-?" in sys.argv:
        sys.argv[1] = "--help"


def main():
    custom_help_check()
    typer.run(t_main)


if __name__ == "__main__":
    main()
