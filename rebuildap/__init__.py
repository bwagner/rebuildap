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

# Sentinel for a bare ``-q`` (no track named): auto-detect the beats reference
# track. Distinct from ``-q <name>`` (the string) and no ``-q`` at all (None).
_QUANTIZE_AUTODETECT = object()


def process_lines(lines):
    """Normalize label lines to the shared canonical form for comparison.

    Unrecognized lines pass through unchanged (the diff will surface them). The
    import path uses the same :func:`normalize_label_line` but treats an
    unrecognized line as an error instead. See ``rebuildap.utils``.
    """
    return [normalize_label_line(line) or line for line in lines]


def check_label_age(filename: str, verbose, deep=False):
    """
    Check whether the Audacity file is newer than the label files.
    Export those label tracks whose corresponding label files are older than the Audacity file
    and compare their contents with their corresponding label files.

    With ``deep=True`` the mtime gate is skipped and every label file is
    compared against the project's label tracks, regardless of mtimes. This
    costs an Audacity open on every run but never gives a false "nothing to
    do": mtimes lie when something rewrites a label file without touching the
    project (``git checkout``, ``touch``, a restore), leaving it newer than the
    ``.aup3`` while its content has diverged.

    Unfortunately, opening an Audacity project and applying changes that are undone still
    updates the modification time of the project file. Filed an issue with Audacity:
    https://github.com/audacity/audacity/issues/9161 — **closed as not-planned**:
    every edit is written to the project file before you press Save, so undo and
    crash recovery can work, and that changes the file even when the project data
    is intact. Inherent to the format, so design around it rather than wait.
    See docs/audacity-quirks.md > When an .aup3 changes on disk.
    """
    if verbose:
        print(
            f"Check whether Audacity file is newer than label files. ({filename or 'current dir'})"
        )
        # check whether multiple Audacity files in current directory
    if filename:
        filename = Path(filename)
    else:
        audacity_files = Path.cwd().glob(f"*.{af.AUDACITY_EXTENSION}")
        try:
            first = next(audacity_files)
        except StopIteration:
            print("No Audacity files found in current directory.")
            return
        try:
            next(audacity_files)
            print(
                "Multiple Audacity files found in current directory. Please specify a filename."
            )
            return
        except StopIteration:
            pass

        filename = first

    if verbose:
        print(f"Checking whether {filename.name} is newer than label files.")
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

    # TODO: export only the label tracks that are older than the Audacity file
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

    _check_label_age_via_getinfo(filename, candidates, label_files)

    # Close via AppleScript Cmd-W rather than pa.do("Close:") — avoids the
    # mod-script-pipe → lib-menus.dylib crash path that bites after a few
    # open/close cycles (see docs/audacity-quirks.md > Audacity cold-start race).
    # Cmd-W hits the frontmost window, so close_owned_window confirms the
    # project we opened is frontmost first; Audacity titles a project window
    # with its .aup3 stem. If focus moved to the user's own project, it
    # refuses and leaves both windows open rather than discarding their work.
    ap.close_owned_window(filename.stem, verbose)


def _check_label_age_via_getinfo(filename, candidates, label_files):
    """Non-interactive path: compare versioned files against GetInfo content in memory.

    ``candidates`` is the mtime-gated subset actually compared; ``label_files``
    is every versioned label file for the project, used only to tell a track
    that is genuinely absent from disk apart from one merely gated out by mtime.
    """
    contents = af.get_label_tracks_content_via_getinfo()
    # Beside the project being checked, not in the cwd: `rebuildap -c
    # /elsewhere/song.aup3` used to scatter export artifacts wherever it
    # happened to be run from.
    out_dir = Path(filename).expanduser().resolve().parent
    stem = Path(filename.name).stem
    for label_file in candidates:
        short_name = label_file.stem.replace(f"_{stem}", "")
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

    _report_audacity_only_tracks(contents, label_files, stem, out_dir)


def _report_audacity_only_tracks(contents, label_files, stem, out_dir):
    """Report label tracks present in Audacity that have no ``.txt`` on disk.

    The comparison loop above is file-driven, so a track that lives only in the
    project — added in Audacity and never exported — is otherwise invisible to
    ``-c``. Report each and point at the export route, but never auto-write it:
    the versioned ``.txt`` files are the source of truth, and the no-arg export
    is the deliberate path for creating them.
    """
    on_disk = {lf.stem.replace(f"_{stem}", "") for lf in label_files}
    audacity_only = [name for name in contents if name not in on_disk]
    if not audacity_only:
        return
    for name in audacity_only:
        print(
            f"Label track '{name}' is in the project but has no label file in {out_dir}."
        )
    print(
        "To export it, select the label track(s) in Audacity, then run "
        "`rebuildap` (no arguments) in that directory."
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
    quantize=None,
):
    if check:
        check_label_age(filename, verbose, deep=deep)
    elif quantize is not None:
        # Operates on the open project, no filename (guarded in main()).
        _quantize_open_project(quantize, verbose)
    elif filename:
        filename = Path(filename)
        if label:
            if verbose:
                print("importing label into open Audacity project.")
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
                print(f"exporting labels from Audacity project ({Path(filename).name})")
            af.export_label_tracks_via_getinfo(filename)
            # TODO: export audio tracks, same naming scheme as labels (but ending in mp3)
            #       song track: "orig"
            #       other tracks: guitar (etc.)
        else:
            if verbose:
                print(
                    f"rebuilt Audacity project from audio and labels ({Path(filename).name})"
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


def _resolve_export_dir(stem, force=False):
    """Where the open project's versioned ``.txt`` files may be written, or
    ``None`` to refuse.

    These are the source-of-truth files, so they only ever go into the current
    directory. When cwd is not the project's own directory: point at where
    Audacity's Open Recent says it lives and refuse (return ``None``) so the
    user cd's there; ``force`` overrides that and returns cwd anyway; and when
    nothing can be suggested, warn but fall back to cwd rather than block.
    Messaging goes to stderr. Shared by the no-arg export and ``-q``.
    """
    cwd = Path.cwd()
    if af.dir_holds_project(cwd, stem):
        return cwd
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
        return None
    if candidates:  # force is set: use cwd despite the suggestion
        print(
            f"-f given: exporting '{stem}' into the current directory:",
            file=sys.stderr,
        )
        print(f"{_PATH_INDENT}{cwd}", file=sys.stderr)
        print("even though it appears to live in:", file=sys.stderr)
        for directory in candidates:
            print(f"{_PATH_INDENT}{directory}", file=sys.stderr)
        return cwd
    print(
        f"Could not locate the project directory for '{stem}' "
        f"(it is not in Audacity's Open Recent). Exporting into the "
        f"current directory:",
        file=sys.stderr,
    )
    print(f"{_PATH_INDENT}{cwd}", file=sys.stderr)
    return cwd


def _export_open_project_labels(verbose, force=False):
    """No-arg export: write the open project's label tracks as versioned .txt.

    Writes only into the current directory, and never anywhere else — these are
    the source-of-truth files. The cwd/project-dir decision (and ``-f``) lives
    in :func:`_resolve_export_dir`; a ``None`` result means refuse.
    """
    stem = af.open_project_wave_stem()
    if _resolve_export_dir(stem, force) is None:
        return

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


def _resolve_quantize_dir(stem):
    """Directory for ``-q``'s versioned ``.txt`` -- the open project's own directory.

    Unlike the no-arg export (which only ever writes cwd), ``-q`` writes to
    wherever the project actually lives, so the file and the just-quantized
    project stay consistent no matter what cwd the command was run from: cwd when
    it holds the project, else the single directory Audacity's Open Recent
    reports for this stem. Refuses (returns ``None``, explaining on stderr) when
    that directory is ambiguous (same stem in several places) or unknown, so a
    source-of-truth file is never scattered.
    """
    cwd = Path.cwd()
    if af.dir_holds_project(cwd, stem):
        return cwd
    candidates = af.find_recent_project_dirs(stem)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        print(
            f"The open project '{stem}' lives in more than one place; cannot tell "
            "which to update:",
            file=sys.stderr,
        )
        for directory in candidates:
            print(f"{_PATH_INDENT}{directory}", file=sys.stderr)
        print("cd into the right one and run `rebuildap -q` there.", file=sys.stderr)
        return None
    print(
        f"Could not locate the directory of the open project '{stem}' (it is not "
        "in Audacity's Open Recent), so its label file cannot be written. cd into "
        "the project directory and run `rebuildap -q` there.",
        file=sys.stderr,
    )
    return None


def _quantize_open_project(quantize, verbose):
    """Quantize the selected label track to a beats track, in place, then persist.

    ``quantize`` is the CLI value: :data:`_QUANTIZE_AUTODETECT` for a bare ``-q``
    (find the beats track), or a track name from ``-q <name>``. The selected
    label track is snapped to that beats grid and re-imported at its original
    position; the quantized labels come back in hand and are written straight to
    the project's own directory (no read-back export).

    The write directory is resolved *before* the project is touched, so a
    location we cannot write to is a clean refusal rather than a quantized-but-
    unpersisted half-state.
    """
    reference_name = None if quantize is _QUANTIZE_AUTODETECT else quantize
    if not prerequisites_met(verbose):
        return
    stem = af.open_project_wave_stem()
    out_dir = _resolve_quantize_dir(stem)
    if out_dir is None:
        return
    try:
        target_name, _idx, _stem, content, changed = af.quantize_selected_label_track(
            reference_name, verbose
        )
    except af.QuantizeError as e:
        raise SystemExit(f"{e}") from e
    out_path = out_dir / af._derive_label_filename(target_name, stem)
    wrote = _write_if_divergent(out_path, content)
    _report_quantize_outcome(target_name, out_path, changed, wrote)


def _write_if_divergent(out_path, content):
    """Write ``content`` to ``out_path`` unless the file already holds equivalent
    labels. Returns True iff it wrote.

    Equivalence uses the same normalization ``-c`` compares with
    (:func:`process_lines` -> ``cut_trailing_zeros``), so a file that differs only
    in float formatting is left untouched -- source-of-truth files are never
    rewritten with identical content.
    """
    if out_path.exists():
        existing = process_lines(out_path.read_text().splitlines(keepends=True))
        incoming = process_lines(content.splitlines(keepends=True))
        if existing == incoming:
            return False
    out_path.write_text(content)
    return True


def _report_quantize_outcome(target_name, out_path, changed, wrote):
    """State plainly what ``-q`` did, across the four project-changed/file-written
    combinations -- so an already-quantized track reads as 'nothing to do', not as
    a silent success."""
    if changed and wrote:
        print(f"Quantized label track '{target_name}':")
        print(f"{_PATH_INDENT}{out_path}")
    elif not changed and not wrote:
        print(f"Label track '{target_name}' is already quantized; nothing to do.")
    elif not changed and wrote:
        # Track was already on the grid, but its versioned file was stale.
        print(f"Label track '{target_name}' was already quantized; updated its file:")
        print(f"{_PATH_INDENT}{out_path}")
    else:  # changed and not wrote
        print(
            f"Quantized label track '{target_name}'; its label file was already "
            "up to date."
        )


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
    parser = argparse.ArgumentParser(
        description="rebuild Audacity project",
        epilog=(
            "Input modes:\n"
            "  audio file   imported; matching *_<stem>.txt become label tracks\n"
            "  .aup3        its label tracks are exported to .txt files\n"
            "  (nothing)    the open Audacity project is used — selected label\n"
            "               tracks are exported, or all of them if none are selected\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "filename",
        nargs="?",
        help=(
            "Audio to rebuild from, or an .aup3 to export from. Omit to "
            "export the open Audacity project's labels (see Input modes below)."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose mode."
    )
    parser.add_argument("-l", "--label", action="store_true", help="Import label file.")
    parser.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="Check whether Audacity file is newer than label files and show differences.",
    )
    parser.add_argument(
        "-d",
        "--deep",
        action="store_true",
        help=(
            "With -c, compare every label file against "
            "the project's label tracks, even ones newer than the .aup3. Opens "
            "Audacity every run; catches label files rewritten (git checkout, "
            "touch) without changing the project."
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
        "-q",
        "--quantize",
        nargs="?",
        const=_QUANTIZE_AUTODETECT,
        default=None,
        metavar="BEATS_TRACK",
        help=(
            "Quantize the selected label track in the open project to a beats "
            "label track already in it, in place: the boundaries snap to the "
            "beats grid, the track is re-imported at its original position, and "
            "its versioned .txt is updated to match. Give a track name to pick "
            "the reference, or omit it to auto-detect the beats track."
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
    if args.quantize is not None and (args.filename or args.check or args.label):
        parser.error(
            "--quantize operates on the open project; not with a filename, -c, or -l."
        )
    rebuild(
        args.filename,
        args.verbose,
        args.label,
        args.check,
        save=not args.no_save,
        deep=args.deep,
        force=args.force,
        quantize=args.quantize,
    )


if __name__ == "__main__":
    main()
