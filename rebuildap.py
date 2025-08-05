#!/usr/bin/env python
import difflib
import re
from pathlib import Path

import typer
from typing_extensions import Annotated

import audacity_funcs as af
import audacity_present as ap
from utils import cut_trailing_zeros

"""
rebuildap.py song.mp3


"""


def is_float(s):
    """Return True if s is a valid Python float literal (excluding scientific notation)."""
    float_like = re.compile(r"^-?(?:\d+\.\d*|\.\d+|\d+)$")
    return bool(float_like.match(s))


def process_lines(lines):
    result = []
    for line in lines:
        parts = line.strip().split("\t")

        if len(parts) == 1 and is_float(parts[0]):
            f = cut_trailing_zeros(parts[0])
            result.append(f"{f}\t{f}\n")
        elif len(parts) == 2 and is_float(parts[0]) and not is_float(parts[1]):
            f = cut_trailing_zeros(parts[0])
            result.append(f"{f}\t{f}\t{parts[1]}\n")
        elif len(parts) == 2 and is_float(parts[0]) and is_float(parts[1]):
            f1 = cut_trailing_zeros(parts[0])
            f2 = cut_trailing_zeros(parts[1])
            result.append(f"{f1}\t{f2}\n")
        elif len(parts) == 3 and is_float(parts[0]) and is_float(parts[1]):
            f1 = cut_trailing_zeros(parts[0])
            f2 = cut_trailing_zeros(parts[1])
            result.append(f"{f1}\t{f2}\t{parts[2]}\n")
        else:
            result.append(line)

    return result


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

        dismissed_labels = []

        for label_file in outdated_candidates:
            exported_label_name = (
                Path(label_file.name).stem.replace(f"_{Path(filename.name).stem}", "")
                + Path(label_file.name).suffix
            )
            if verbose:
                print(f"{label_file.name} is older than {filename.name}")
                labels_from_file = process_lines(
                    label_file.read_text().splitlines(keepends=True)
                )
                labels_from_proj = process_lines(
                    Path(exported_label_name).read_text().splitlines(keepends=True)
                )
                sm = difflib.unified_diff(
                    labels_from_file,
                    labels_from_proj,
                    fromfile=str(label_file),
                    tofile=f"exported label file {exported_label_name}",
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
                    dismissed_labels.append(exported_label_name)
            else:
                if verbose:
                    print(
                        f"{label_file.name} is newer than {filename.name} (nothing to do)"
                    )
                    dismissed_labels.append(exported_label_name)

        # delete the exported label files that are identical to the label files
        for label_file in dismissed_labels:
            if verbose:
                print(f"Deleting exported label file {label_file}")
            Path(label_file).unlink(missing_ok=True)
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
            "-c",
            "--check",
            help="Check whether audacity file newer than label files and show differences.",
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


def custom_help_check() -> None:
    """
    Adds command line options -h and -? in addition to the default --help to
    show help output.
    """
    import sys

    if "-h" in sys.argv or "-?" in sys.argv:
        sys.argv[1] = "--help"


def main():
    custom_help_check()
    typer.run(rebuild)


if __name__ == "__main__":
    main()
