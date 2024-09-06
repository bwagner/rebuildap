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
    label: Annotated[
        bool, typer.Option("-l", "--label", help="Import label file.")
    ] = False,
):
    if filename:
        if label:
            if verbose:
                print(f"importing label into open audacity project.")
            af.make_label_track_from_file(filename)
            return
        ap.assert_audacity(verbose)
        af.open_audio(filename, verbose)
        if af.is_audacity_project(filename):
            if verbose:
                print(f"exporting labels from audacity project ({Path(filename).name})")
            af.export_label_tracks()
            # TODO: export audio tracks, same naming scheme as labels (but ending in mp3)
            #       song track: "orig"
            #       other tracks: guitar (etc.)
        else:
            if verbose:
                print(
                    f"rebuilt audacity project from audio and labels ({Path(filename).name})"
                )

    else:
        if not ap.is_audacity_running:
            if verbose:
                print("No filename passed, Audacity not running. Quitting.")
            return
        if not ap.is_audacity_window_open():
            if verbose:
                print("No Audacity window open. Quitting.")
            return
        if af.is_project_empty():
            if verbose:
                print("Audacity project empty. Quitting.")
            return
        if not af.get_label_tracks():
            if verbose:
                print("Audacity project has no label tracks. Quitting.")
            return

        if af.get_selected_label_track_indices():
            if verbose:
                print("exporting selected label track")
            af.export_selected_label_tracks()
        else:
            if verbose:
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
