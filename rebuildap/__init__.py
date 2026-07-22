#!/usr/bin/env python
import argparse
import difflib
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from . import audacity_funcs as af
from . import audacity_present as ap
from .utils import normalize_label_line
from .version_info import get_version_info

"""
rebuildap.py song.mp3


"""

# Indent for a path printed on its own line, so long paths stay readable.
_PATH_INDENT = "  "


def process_lines(lines):
    """Normalize label lines to the shared canonical form for comparison.

    Unrecognized lines pass through unchanged (the diff will surface them). The
    import path uses the same :func:`normalize_label_line` but treats an
    unrecognized line as an error instead. See ``rebuildap.utils``.
    """
    return [normalize_label_line(line) or line for line in lines]


def check_label_age(filename: str, verbose, deep=False):
    """
    Check whether the audacity file is newer than the label files.
    Export those label tracks whose corresponding label files are older than the audacity file
    and compare their contents with their corresponding label files.

    With ``deep=True`` the mtime gate is skipped and every label file is
    compared against the project's label tracks, regardless of mtimes. This
    costs an Audacity open on every run but never gives a false "nothing to
    do": mtimes lie when something rewrites a label file without touching the
    project (``git checkout``, ``touch``, a restore), leaving it newer than the
    ``.aup3`` while its content has diverged.

    Unfortunately, opening an audacity project and applying changes that are undone still
    updates the modification time of the project file. Filed an issue with Audacity:
    https://github.com/audacity/audacity/issues/9161 — **closed as not-planned**:
    every edit is written to the project file before you press Save, so undo and
    crash recovery can work, and that changes the file even when the project data
    is intact. Inherent to the format, so design around it rather than wait.
    See docs/audacity-quirks.md > When an .aup3 changes on disk.
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
    if not label_files:
        print(f"No label files found for {filename.name}. Nothing to do.")
        return

    if deep:
        # Skip the mtime gate: compare every label file, regardless of age.
        candidates = list(label_files)
    else:
        audacity_file_mtime = filename.stat().st_mtime
        candidates = [
            Path(label_file)
            for label_file in label_files
            if label_file.stat().st_mtime < audacity_file_mtime
        ]
        if not candidates:
            # Printed unconditionally, not only under -v: a check run that
            # concludes there is nothing to do must say so, or it looks broken.
            print(f"All label files are newer than {filename.name}. Nothing to do.")
            return

    # TODO: export only the label tracks that are older than the audacity file
    try:
        # Ahead of assert_audacity, so a project we are going to skip costs
        # nothing: it has tracks, so it fails the empty-project probe and
        # assert_audacity would answer with a Cmd-N, leaving a stray empty
        # window behind for every skipped project in a sweep.
        af.assert_not_already_open(filename)
        ap.assert_audacity(verbose)
        # Still guarded inside open_project, which catches the race where the
        # user opens the project between the check above and the command.
        af.open_audio(filename, verbose)
    except af.ProjectAlreadyOpenError as e:
        # Report and move on: in a sweep across many projects one already-open
        # project must not abort the rest. Returning here also skips the
        # close_owned_window below — that window is not ours to close, and
        # closing someone else's project is the very mistake the ownership
        # check exists to prevent.
        print(f"Skipping {filename.name}: {e}", file=sys.stderr)
        return

    _check_label_age_via_getinfo(filename, candidates)

    # Close via AppleScript Cmd-W rather than pa.do("Close:") — avoids the
    # mod-script-pipe → lib-menus.dylib crash path that bites after a few
    # open/close cycles (see docs/audacity-quirks.md > Audacity cold-start race).
    # Cmd-W hits the frontmost window, so close_owned_window confirms the
    # project we opened is frontmost first; Audacity titles a project window
    # with its .aup3 stem. If focus moved to the user's own project, it
    # refuses and leaves both windows open rather than discarding their work.
    ap.close_owned_window(filename.stem, verbose)


def _check_label_age_via_getinfo(filename, candidates):
    """Non-interactive path: compare versioned files against GetInfo content in memory."""
    contents = af.get_label_tracks_content_via_getinfo()
    # Beside the project being checked, not in the cwd: `rebuildap -c
    # /elsewhere/song.aup3` used to scatter export artifacts wherever it
    # happened to be run from.
    out_dir = Path(filename).expanduser().resolve().parent
    for label_file in candidates:
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
    if not ap.is_audacity_running():
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
    filename=None,
    verbose=False,
    label=False,
    check=False,
    save=True,
    deep=False,
    force=False,
):
    if check:
        check_label_age(filename, verbose, deep=deep)
    elif filename:
        filename = Path(filename)
        if label:
            if verbose:
                print("importing label into open audacity project.")
            try:
                af.make_label_track_from_file(filename)
            except af.LabelFormatError as e:
                raise SystemExit(f"{e}") from e
            return
        # Rebuilding audio into a project whose .aup3 already exists throws the
        # result away: save_project_if_absent will decline to overwrite, leaving
        # the rebuilt project open and *unsaved* — and an unsaved project cannot
        # be closed safely, since Cmd-W on one raises "Save changes?", the dialog
        # that wedges the scripting pipe. The existing file is knowable up front,
        # so none of that work is started. With -n the throwaway window is what
        # the user asked for, so this does not apply.
        if save and not af.is_audacity_project(filename):
            existing = af.aup3_path_for(filename)
            if existing.exists():
                raise SystemExit(
                    f"{existing.name} already exists beside {filename.name} and is "
                    "never overwritten — it is your working copy and may hold edits "
                    "the label files don't have. Nothing was rebuilt. Use -n to "
                    "rebuild into an unsaved window anyway, or move the existing "
                    f"{existing.name} aside first."
                )
        # Validate label files before starting Audacity: a malformed one
        # otherwise crashes mid-rebuild, after the audio is imported and the
        # window is open but unsaved (nothing can then close it safely). Only
        # for audio input — .aup3 input exports labels, it does not import them.
        if not af.is_audacity_project(filename):
            try:
                af.assert_label_files_importable(filename)
            except af.LabelFormatError as e:
                raise SystemExit(f"{e}") from e
        try:
            af.assert_not_already_open(filename)
            ap.assert_audacity(verbose)
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
        _export_open_project_labels(verbose, force)


def _export_open_project_labels(verbose, force=False):
    """No-arg export: write the open project's label tracks as versioned .txt.

    Writes only into the current directory, and never anywhere else — these are
    the source-of-truth files. When cwd is not the project's own directory:
    point at where Audacity's Open Recent says it lives and export nothing (the
    user should cd there); or, when nothing can be suggested, say so and export
    into cwd anyway rather than block. ``force`` overrides the refusal and
    exports into cwd even when the project appears to live elsewhere.
    """
    stem = af.open_project_wave_stem()
    cwd = Path.cwd()
    if not af.dir_holds_project(cwd, stem):
        candidates = af.find_recent_project_dirs(stem)
        if candidates and not force:
            print(
                f"The open project '{stem}' is not in the current directory:",
                file=sys.stderr,
            )
            print(f"{_PATH_INDENT}{cwd}", file=sys.stderr)
            print("It looks like it lives in:", file=sys.stderr)
            for directory in candidates:
                print(f"{_PATH_INDENT}{directory}", file=sys.stderr)
            print(
                "cd into that directory and run rebuildap again, or pass -f to "
                "export into the current directory anyway (nothing was exported).",
                file=sys.stderr,
            )
            return
        if candidates:  # force is set: export here despite the suggestion
            print(
                f"-f given: exporting '{stem}' into the current directory:",
                file=sys.stderr,
            )
            print(f"{_PATH_INDENT}{cwd}", file=sys.stderr)
            print("even though it appears to live in:", file=sys.stderr)
            for directory in candidates:
                print(f"{_PATH_INDENT}{directory}", file=sys.stderr)
        else:
            print(
                f"Could not locate the project directory for '{stem}' "
                f"(it is not in Audacity's Open Recent). Exporting into the "
                f"current directory:",
                file=sys.stderr,
            )
            print(f"{_PATH_INDENT}{cwd}", file=sys.stderr)

    if af.get_selected_label_track_indices():
        if verbose:
            print("exporting selected label track")
        exported = af.export_selected_label_tracks_via_getinfo()
    else:
        if verbose:
            print("exporting all label tracks")
        exported = af.export_label_tracks_via_getinfo()
    # Reported unconditionally, not only under -v: a silent export looked
    # like nothing happened. Names each track and the full path written.
    _report_exports(exported)


def _report_exports(exported):
    """Print one line per exported label track: its name and the full path written.

    ``exported`` is the ``(track_name, path)`` list returned by the
    ``export_*_via_getinfo`` functions. An empty list means the project had a
    label track selected/present but nothing came back to write.
    """
    if not exported:
        print("No label tracks were exported.")
        return
    for name, path in exported:
        print(f"Exported label track '{name}':")
        print(f"{_PATH_INDENT}{path}")


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
        "-d",
        "--deep",
        action="store_true",
        help=(
            "With -c, skip the mtime gate and compare every label file against "
            "the project's label tracks, even ones newer than the .aup3. Opens "
            "Audacity every run but never reports a false 'nothing to do' when a "
            "label file was rewritten (git checkout, touch) without the project "
            "changing."
        ),
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
        "-f",
        "--force",
        action="store_true",
        help=(
            "For the no-argument export only: export into the current directory "
            "even when the open project appears to live elsewhere (Open Recent). "
            "Without it, rebuildap points at where the project is and exports "
            "nothing. Does not override the never-overwrite rule for a rebuild."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=get_version_info(_pkg_version("rebuildap")),
    )
    args = parser.parse_args()
    if args.deep and not args.check:
        parser.error("--deep only applies with --check/-c.")
    if args.force and (args.filename or args.check or args.label):
        parser.error("--force only applies to the no-argument export.")
    rebuild(
        args.filename,
        args.verbose,
        args.label,
        args.check,
        save=not args.no_save,
        deep=args.deep,
        force=args.force,
    )


if __name__ == "__main__":
    main()
