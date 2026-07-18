#!/usr/bin/env python
import argparse
import difflib
import re
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from . import audacity_funcs as af
from . import audacity_present as ap
from .utils import cut_trailing_zeros
from .version_info import get_version_info

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


def check_label_age(filename: str, verbose):
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
    if filename:
        filename = Path(filename)
    else:
        audacity_files = Path.cwd().glob(f"*.{af.AUDACITY_EXTENSION}")
        try:
            first = next(audacity_files)
        except StopIteration:
            print("No audacity files found in current directory.")
            return
        try:
            next(audacity_files)
            print(
                "Multiple audacity files found in current directory. Please specify a filename."
            )
            return
        except StopIteration:
            pass

        filename = first

    if verbose:
        print(f"Checking whether {filename.name} newer than label files.")
    label_files = af.reorder_labels(af.create_labels_glob(filename))
    audacity_file_mtime = filename.stat().st_mtime
    outdated_candidates = [
        Path(label_file)
        for label_file in label_files
        if label_file.stat().st_mtime < audacity_file_mtime
    ]

    if not outdated_candidates:
        if verbose:
            print(f"All label files are newer than {filename.name}. Nothing to do.")
        return

    # TODO: export only the label tracks that are older than the audacity file
    ap.assert_audacity(verbose)
    try:
        af.open_audio(filename, verbose)
    except af.ProjectAlreadyOpenError as e:
        # Report and move on: in a sweep across many projects one already-open
        # project must not abort the rest. Returning here also skips the
        # close_owned_window below — that window is not ours to close, and
        # closing someone else's project is the very mistake the ownership
        # check exists to prevent.
        print(f"Skipping {filename.name}: {e}", file=sys.stderr)
        return

    _check_label_age_via_getinfo(filename, outdated_candidates)

    # Close via AppleScript Cmd-W rather than pa.do("Close:") — avoids the
    # mod-script-pipe → lib-menus.dylib crash path that bites after a few
    # open/close cycles (see README > Comments > Audacity cold-start race).
    # Cmd-W hits the frontmost window, so close_owned_window confirms the
    # project we opened is frontmost first; Audacity titles a project window
    # with its .aup3 stem. If focus moved to the user's own project, it
    # refuses and leaves both windows open rather than discarding their work.
    ap.close_owned_window(filename.stem, verbose)


def _check_label_age_via_getinfo(filename, outdated_candidates):
    """Non-interactive path: compare versioned files against GetInfo content in memory."""
    contents = af.get_label_tracks_content_via_getinfo()
    # Beside the project being checked, not in the cwd: `rebuildap -c
    # /elsewhere/song.aup3` used to scatter export artifacts wherever it
    # happened to be run from.
    out_dir = Path(filename).expanduser().resolve().parent
    for label_file in outdated_candidates:
        short_name = label_file.stem.replace(f"_{Path(filename.name).stem}", "")
        expected = contents.get(short_name)
        if expected is None:
            print(f"No matching label track for {label_file.name}; skipping.")
            continue
        labels_from_file = process_lines(
            label_file.read_text().splitlines(keepends=True)
        )
        labels_from_proj = process_lines(expected.splitlines(keepends=True))
        _report_diff(label_file, short_name, labels_from_file, labels_from_proj)
        _maybe_write_divergent_export(
            expected_content=expected,
            label_file=label_file,
            short_name=short_name,
            out_dir=out_dir,
        )


def _maybe_write_divergent_export(
    expected_content: str,
    label_file: Path,
    short_name: str,
    out_dir: Path,
):
    """Write ``expected_content`` to ``<out_dir>/<short_name>.txt`` iff it differs
    (after ``process_lines`` normalization) from ``label_file``'s content.

    Returns the written Path on divergence, or None when the two are equivalent
    under normalization (in which case no file is written).
    """
    labels_from_file = process_lines(label_file.read_text().splitlines(keepends=True))
    labels_from_proj = process_lines(expected_content.splitlines(keepends=True))
    if labels_from_file == labels_from_proj:
        return None
    out_path = out_dir / f"{short_name}.txt"
    out_path.write_text(expected_content)
    return out_path


def _report_diff(label_file, exported_label_name, labels_from_file, labels_from_proj):
    sm = list(
        difflib.unified_diff(
            labels_from_file,
            labels_from_proj,
            label_file.name,
            f"Audacity-label track: {exported_label_name}",
        )
    )
    if sm:
        print(
            f"Label file {label_file.name} differs from exported label track {exported_label_name}:"
        )
        print("".join(sm))
    else:
        print(
            f"Label file {label_file.name} and exported label track {exported_label_name} are identical."
        )


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


def rebuild(filename=None, verbose=False, label=False, check=False, save=True):
    if check:
        check_label_age(filename, verbose)
    elif filename:
        filename = Path(filename)
        if label:
            if verbose:
                print("importing label into open audacity project.")
            af.make_label_track_from_file(filename)
            return
        ap.assert_audacity(verbose)
        try:
            af.open_audio(filename, verbose)
        except af.ProjectAlreadyOpenError as e:
            # A single explicit target, unlike the sweep in check mode: report
            # cleanly rather than with a traceback, but exit non-zero, since
            # the work the user asked for did not happen.
            raise SystemExit(f"{e}") from e
        if af.is_audacity_project(filename):
            if verbose:
                print(f"exporting labels from audacity project ({Path(filename).name})")
            af.export_label_tracks_via_getinfo(filename)
            # TODO: export audio tracks, same naming scheme as labels (but ending in mp3)
            #       song track: "orig"
            #       other tracks: guitar (etc.)
        else:
            if verbose:
                print(
                    f"rebuilt audacity project from audio and labels ({Path(filename).name})"
                )
            if save:
                saved = af.save_project_if_absent(filename, verbose)
                if saved is not None:
                    # Keep the label files from reading as stale to -c; they
                    # are what the project was just built from.
                    af.touch_label_files(filename, saved, verbose)
                    # Saving retitles the window to the .aup3 stem, which both
                    # identifies it as ours and makes Cmd-W close silently.
                    # An unsaved project would instead raise "Save changes?",
                    # a dialog that wedges the scripting pipe — so the window
                    # is only ever closed once it is safely on disk.
                    ap.close_owned_window(saved.stem, verbose)
                elif verbose:
                    print(
                        "Leaving the rebuilt project open: it was not saved, so "
                        "closing it would raise a 'Save changes?' dialog."
                    )
            elif verbose:
                print(
                    "Leaving the rebuilt project open (--no-save); closing an "
                    "unsaved project would raise a 'Save changes?' dialog."
                )

    elif prerequisites_met(verbose):
        if af.get_selected_label_track_indices():
            if verbose:
                print("exporting selected label track")
            af.export_selected_label_tracks_via_getinfo()
        else:
            if verbose:
                print("exporting all label tracks")
            af.export_label_tracks_via_getinfo()


def main():
    parser = argparse.ArgumentParser(description="rebuild Audacity project")
    parser.add_argument("filename", nargs="?", help="The audio file name.")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose mode."
    )
    parser.add_argument("-l", "--label", action="store_true", help="Import label file.")
    parser.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="Check whether audacity file newer than label files and show differences.",
    )
    parser.add_argument(
        "-n",
        "--no-save",
        action="store_true",
        help=(
            "Don't save the rebuilt project as <audio-stem>.aup3 beside the "
            "audio file. By default it is saved when no .aup3 exists yet; an "
            "existing one is never overwritten."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=get_version_info(_pkg_version("rebuildap")),
    )
    args = parser.parse_args()
    rebuild(
        args.filename,
        args.verbose,
        args.label,
        args.check,
        save=not args.no_save,
    )


if __name__ == "__main__":
    main()
