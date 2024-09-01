#!/usr/bin/env python

import glob
import sys
from pathlib import Path

import typer

import audacity_funcs as af
import audacity_present as ap

"""
rebuildap.py song.mp3


"""


def create_labels_glob(filename: str):
    abs_path = Path(filename).expanduser().resolve()
    dirname = abs_path.parent
    return glob.glob(f"{dirname}/*_{abs_path.stem}.txt")


def t_main(
    filename: str = typer.Argument(..., help="The audio file name."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose mode."),
):

    if len(sys.argv) < 2:
        print(f"{Path(__file__).name} filename")
        return

    ap.assert_audacity(verbose)

    if verbose:
        print(f"Importing {filename}")
    af.import_audio(filename)
    if verbose:
        print(f"Done importing {filename}")
    abs_path = Path(filename).expanduser().resolve()
    label_files = create_labels_glob(filename)
    for lfile in label_files:
        lblname = Path(lfile).stem.replace(f"_{abs_path.stem}", "")
        if verbose:
            print(f"labels: >{lblname}<")
        af.make_label_track(lfile, lblname)


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
