#!/usr/bin/env python
import difflib
from pathlib import Path

import typer
from typing_extensions import Annotated

import audacity_funcs as af
import audacity_present as ap

"""
rebuildap.py song.mp3


"""


def check_label_age(filename, verbose):
    """
    Check whether the audacity file is newer than the label files.
    Export those label tracks whose corresponding label files are older than the audacity file
    and compare their contents with their corresponding label files.

    Unfortunately, opening an audacity project and applying changes that are undone still
    updates the modification time of the project file. Filed an issue with Audacity:
    https://github.com/audacity/audacity/issues/9161

    """
    if verbose:
        print(
            f"Check whether audacity file newer than label files. ({filename or 'current dir'})"
        )
        # check whether multiple audacity files in current directory
    if not filename:
        audacity_files = list(Path.cwd().glob(f"*.{af.AUDACITY_EXTENSION}"))
        if len(audacity_files) > 1:
            print(
                "Multiple audacity files found in current directory. Please specify a filename."
            )
            return
        elif len(audacity_files) == 0:
            print("No audacity files found in current directory.")
            return
        filename = str(audacity_files[0])
    filename = Path(filename)
    if verbose:
        print(f"Checking whether {filename.name} newer than label files.")
    label_files = af.reorder_labels(
        af.create_labels_glob(str(filename))
    )  # TODO: make these Paths instead of strs
    audacity_file_mtime = filename.stat().st_mtime
    outdated_candidates = [
        Path(label_file)
        for label_file in label_files
        if Path(label_file).stat().st_mtime < audacity_file_mtime
    ]
    # once label_file is Path instead of str, remove this ctor Path(label_file)

    if outdated_candidates:
        # export all label tracks and compare their contents with their corresponding label files.
        # TODO: export only the label tracks that are older than the audacity file
        ap.assert_audacity(verbose)
        af.open_audio(str(filename), verbose)
        af.export_label_tracks()
        af.close_project()
        # NOTE: these files are typically named "chords.txt", not "chords_songname.txt"

        for label_file in outdated_candidates:
            exported_label_name = (
                Path(label_file.name).stem.replace(f"_{Path(filename.name).stem}", "")
                + Path(label_file.name).suffix
            )
            if verbose:
                print(f"{label_file.name} is older than {filename.name}")
                sm = difflib.unified_diff(
                    label_file.read_text().splitlines(keepends=True),
                    Path(exported_label_name).read_text().splitlines(keepends=True),
                    fromfile=str(label_file),
                    tofile=exported_label_name,
                )
                if any(sm):
                    print(
                        f"Label file {label_file.name} differs from exported label file {exported_label_name}:"
                    )
                    print("".join(sm))
                else:
                    print(
                        f"Label file {label_file.name} and exported label file {exported_label_name} are identical."
                    )
            else:
                if verbose:
                    print(
                        f"{label_file.name} is newer than {filename.name} (nothing to do)"
                    )
    else:
        if verbose:
            print(f"All label files are newer than {filename.name}. Nothing to do.")


def prerequisites_met(verbose: bool) -> bool:
    if not ap.is_audacity_running:
        if verbose:
            print("No filename passed, Audacity not running. Quitting.")
        return False
    if not ap.is_audacity_window_open():
        if verbose:
            print("No Audacity window open. Quitting.")
        return False
    if af.is_project_empty():
        if verbose:
            print("Audacity project empty. Quitting.")
        return False
    if not af.get_label_tracks():
        if verbose:
            print("Audacity project has no label tracks. Quitting.")
        return False

    return True


def rebuild(
    filename: Annotated[str, typer.Argument(..., help="The audio file name.")] = None,
    verbose: Annotated[
        bool, typer.Option("-v", "--verbose", help="Enable verbose mode.")
    ] = False,
    label: Annotated[
        bool, typer.Option("-l", "--label", help="Import label file.")
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "-c", "--check", help="Check whether audacity file newer than label files."
        ),
    ] = False,
):
    if check:
        check_label_age(filename, verbose)
    elif filename:
        if label:
            if verbose:
                print("importing label into open audacity project.")
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

    elif prerequisites_met(verbose):
        if af.get_selected_label_track_indices():
            if verbose:
                print("exporting selected label track")
            af.export_selected_label_tracks()
        else:
            if verbose:
                print("exporting all label tracks")
            af.export_label_tracks()


if __name__ == "__main__":
    import sys

    def custom_help_check() -> None:
        """
        Adds command line options -h and -? in addition to the default --help to
        show help output.
        """
        if "-h" in sys.argv or "-?" in sys.argv:
            sys.argv[1] = "--help"

    def main():
        typer.run(rebuild)

    custom_help_check()
    main()
