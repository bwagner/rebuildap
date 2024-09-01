#!/usr/bin/env python

import sys
from pathlib import Path

import typer
from typing_extensions import Annotated

import audacity_funcs as af
import audacity_present as ap

"""
rebuildap.py song.mp3


"""


def rebuild(
    filename: Annotated[str, typer.Argument(..., help="The audio file name.")] = None,
    verbose: Annotated[
        bool, typer.Option("-v", "--verbose", help="Enable verbose mode.")
    ] = False,
):
    if filename:
        ap.assert_audacity(verbose)
        af.open_audio(filename, verbose)
        if af.is_audacity_project(filename):
            if verbose:
                print(f"exporting labels from audacity project ({Path(filename).name})")
            af.export_label_tracks()
        else:
            if verbose:
                print(
                    f"rebuilt audacity project from audio and labels ({Path(filename).name})"
                )

    else:
        if not ap.is_audacity_running:
            print("No filename passed, Audacity not running. Quitting.")
            return
        if not ap.is_audacity_window_open():
            print("No Audacity window open. Quitting.")
            return
        if af.is_project_empty():
            print("Audacity project empty. Quitting.")
            return
        if not af.get_label_tracks():
            print("Audacity project has no label tracks. Quitting.")
            return

        if af.get_selected_label_track_indices():
            print("exporting selected label track")
            af.export_selected_label_tracks()
        else:
            print("exporting all label tracks")
            af.export_label_tracks()


def custom_help_check() -> None:
    """
    Adds command line options -h and -? in addition to the default --help to
    show help output.
    """
    if "-h" in sys.argv or "-?" in sys.argv:
        sys.argv[1] = "--help"


def main():
    custom_help_check()
    typer.run(rebuild)


if __name__ == "__main__":
    main()
