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

    Audacity is opened only when *some* label file is older than the ``.aup3``;
    once open, every label file is compared. With ``deep=True`` (the CLI's
    ``-c -f``) it is opened even when they are all newer. That costs an Audacity
    open on every run but never gives a false "nothing to do": mtimes lie when
    something rewrites a label file without touching the project (e.g. ``git
    checkout``, ``touch``, a restore), leaving it newer than the ``.aup3`` while
    its content has diverged.

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

    if not deep and not _any_label_file_older(label_files, filename):
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

    _check_label_age_via_getinfo(filename, label_files)

    # Close via AppleScript Cmd-W rather than pa.do("Close:") — avoids the
    # mod-script-pipe → lib-menus.dylib crash path that bites after a few
    # open/close cycles (see docs/audacity-quirks.md > Audacity cold-start race).
    # Cmd-W hits the frontmost window, so close_owned_window confirms the
    # project we opened is frontmost first; Audacity titles a project window
    # with its .aup3 stem. If focus moved to the user's own project, it
    # refuses and leaves both windows open rather than discarding their work.
    ap.close_owned_window(filename.stem, verbose)


def _short_label_name(label_file, stem):
    """The label *track* name a versioned file belongs to: ``chords_song.txt`` ->
    ``chords``."""
    return Path(label_file).stem.replace(f"_{stem}", "")


def _any_label_file_older(label_files, filename):
    """Whether any label file predates the ``.aup3``.

    The mtime gate is a whole-*project* decision -- "is opening Audacity worth
    it?" -- not a per-file filter. If every label file is newer, no project edit
    can have outrun them, so there is nothing an open could reveal. If even one is
    older, the project is opened and then *every* label file is compared: the
    ``GetInfo`` fetches all tracks in one call and comparing one more costs about
    0.2 ms, so filtering per file saved nothing measurable while silently hiding
    tracks -- a run would list three and give no hint a fourth existed, which is
    exactly what ``-q`` and ``-t`` cause by rewriting a ``.txt``.
    """
    aup3_mtime = filename.stat().st_mtime
    return any(f.stat().st_mtime < aup3_mtime for f in label_files)


def _check_label_age_via_getinfo(filename, label_files):
    """Non-interactive path: compare versioned files against GetInfo content in memory.

    Every label file for the project is compared -- see :func:`_any_label_file_older`
    for why there is no per-file gate.
    """
    contents = af.get_label_tracks_content_via_getinfo()
    # Beside the project being checked, not in the cwd: `rebuildap -c
    # /elsewhere/song.aup3` used to scatter export artifacts wherever it
    # happened to be run from.
    out_dir = Path(filename).expanduser().resolve().parent
    stem = Path(filename.name).stem
    for label_file in label_files:
        short_name = _short_label_name(label_file, stem)
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
    on_disk = {_short_label_name(lf, stem) for lf in label_files}
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
    transpose=None,
    sharps=False,
):
    if check:
        check_label_age(filename, verbose, deep=deep)
    elif quantize is not None:
        # Operates on the open project, no filename (guarded in main()).
        _quantize_open_project(quantize, verbose, force)
    elif transpose is not None:
        # Likewise open-project only, and mutually exclusive with -q.
        _transpose_open_project(transpose, sharps, verbose, force)
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
                    "never overwritten - it is your working copy and may hold edits "
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


def _resolve_project_dir(stem, flag):
    """Directory for an in-place mode's versioned ``.txt`` -- the open project's own.

    Unlike the no-arg export (which only ever writes cwd), the in-place modes
    (``-q``, ``-t``) write to wherever the project actually lives, so the file and
    the just-modified project stay consistent no matter what cwd the command was
    run from: cwd when it holds the project, else the single directory Audacity's
    Open Recent reports for this stem. Refuses (returns ``None``, explaining on
    stderr) when that directory is ambiguous (same stem in several places) or
    unknown, so a source-of-truth file is never scattered. ``flag`` names the mode
    in the advice, so the message says how to re-run what was actually attempted.
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
        print(
            f"cd into the right one and run `rebuildap {flag}` there.", file=sys.stderr
        )
        return None
    print(
        f"Could not locate the directory of the open project '{stem}' (it is not "
        "in Audacity's Open Recent), so its label file cannot be written. cd into "
        f"the project directory and run `rebuildap {flag}` there.",
        file=sys.stderr,
    )
    return None


def _quantize_open_project(quantize, verbose, force=False):
    """Quantize the selected label track to a beats track, in place, then persist.

    ``quantize`` is the CLI value: :data:`_QUANTIZE_AUTODETECT` for a bare ``-q``
    (find the beats track), or a track name from ``-q <name>``. Only boundaries
    inside the current time selection are snapped (whole track when nothing is
    selected); ``force`` (``-f``) quantizes the whole track without reading the
    selection, and is the escape hatch the message points at when the selection
    cannot be read. The quantized labels are written straight to the project's
    own directory (no read-back export).

    The write directory is resolved *before* the project is touched, so a
    location we cannot write to is a clean refusal rather than a quantized-but-
    unpersisted half-state.
    """
    reference_name = None if quantize is _QUANTIZE_AUTODETECT else quantize
    if not prerequisites_met(verbose):
        return
    stem = af.open_project_wave_stem()
    out_dir = _resolve_project_dir(stem, "-q")
    if out_dir is None:
        return
    try:
        target_name, _idx, _stem, content, changed = af.quantize_selected_label_track(
            reference_name, verbose, whole_track=force
        )
    except af.SelectionReadError as e:
        raise SystemExit(
            f"Could not read the Audacity selection: {e} Re-run with -f to "
            "quantize the whole track."
        ) from e
    except af.LabelTrackError as e:
        # The base catches both the shared precondition failures (no single label
        # track selected) and the quantize-specific QuantizeError.
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


def _transpose_open_project(semitones, sharps, verbose, force=False):
    """Transpose the selected label track's chords in place, then persist.

    Mirrors :func:`_quantize_open_project`: the write directory is resolved
    *before* the project is touched, only labels starting inside the current time
    selection are transposed (whole track when nothing is selected, or with
    ``-f``), and the result is written straight to the project's own directory.

    ``sharps`` (``-s``) opts out of the flat spelling ``-t`` defaults to.
    """
    if not prerequisites_met(verbose):
        return
    stem = af.open_project_wave_stem()
    out_dir = _resolve_project_dir(stem, f"-t {semitones}")
    if out_dir is None:
        return
    try:
        target_name, _idx, _stem, content, changed, skipped = (
            af.transpose_selected_label_track(
                semitones, prefer_flats=not sharps, verbose=verbose, whole_track=force
            )
        )
    except af.SelectionReadError as e:
        raise SystemExit(
            f"Could not read the Audacity selection: {e} Re-run with -f to "
            "transpose the whole track."
        ) from e
    except af.LabelTrackError as e:
        raise SystemExit(f"{e}") from e
    out_path = out_dir / af._derive_label_filename(target_name, stem)
    wrote = _write_if_divergent(out_path, content)
    _report_transpose_outcome(
        target_name, out_path, semitones, not sharps, changed, wrote, skipped
    )


# How many untransposed label texts to name before summarizing the rest -- a
# chord track can hold hundreds of non-chord labels and dumping them all would
# bury the result.
_MAX_SKIPPED_LISTED = 8


def _report_transpose_outcome(
    target_name, out_path, semitones, prefer_flats, changed, wrote, skipped
):
    """State plainly what ``-t`` did, naming the spelling it used.

    The spelling is always named: rebuildap defaults to flats while the sister
    library defaults to sharps, so a chart coming back respelled must never be a
    silent surprise (decisions.md 2026-07-25 12:45). Labels that held no chord are
    named too -- silence there is the failure mode ``-q`` and the no-arg export
    both had to fix.
    """
    spelling = "flats" if prefer_flats else "sharps"
    how = f"by {semitones:+d} half steps, spelled with {spelling}"
    if not changed and not skipped:
        print(f"Label track '{target_name}' is unchanged {how}; nothing to do.")
    elif not changed:
        print(
            f"Label track '{target_name}' holds no chords to transpose; nothing done."
        )
    else:
        print(f"Transposed label track '{target_name}' {how}:")
        print(f"{_PATH_INDENT}{out_path}")
        if not wrote:
            print(f"{_PATH_INDENT}(its label file was already up to date)")
    if skipped:
        shown = skipped[:_MAX_SKIPPED_LISTED]
        rest = len(skipped) - len(shown)
        print(f"Left alone ({len(skipped)} label(s) held no chord):")
        for text in shown:
            print(f"{_PATH_INDENT}{text}")
        if rest:
            print(f"{_PATH_INDENT}... and {rest} more")


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
            "  (nothing)    the open Audacity project is used - selected label\n"
            "               tracks are exported, or all of them if none are\n"
            "               selected; with -f, into the current directory even\n"
            "               when the project appears to live elsewhere\n"
            "\n"
            "Transform modes (-q, -t) take no filename: they rewrite the selected\n"
            "label track of the open project in place and update its versioned\n"
            ".txt. One at a time.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "filename",
        nargs="?",
        help=(
            "Audio to rebuild a project from, or an .aup3 to export labels "
            "from. Omit to export the open Audacity project's labels (see "
            "Input modes below)."
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
        help=(
            "Check whether Audacity file is newer than label files and show "
            "differences. Audacity is opened only when some label file is older "
            "than the .aup3; every label file is then compared. With -f, open "
            "even when all of them are newer - opens Audacity every run, and "
            "catches label files rewritten (by e.g. git checkout, touch) without "
            "changing the project."
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
        # Deliberately an index rather than an enumeration: each mode documents
        # what -f does *there* (-q and -t in their own help, the no-argument
        # export in the epilog), so no sentence appears twice and a new mode
        # cannot leave this entry stale -- as -t did.
        help=(
            "Force. What it overrides depends on the mode - see -c, -q, -t and "
            '"Input modes" below. Never overrides the never-overwrite rule for '
            "an existing .aup3."
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
            "label track already in it, in place: its label boundaries snap to "
            "the beats grid, the track is re-imported at its original position, "
            "and its versioned .txt is updated to match. Give a track name to "
            "pick the reference, or omit it to auto-detect it: the sole label "
            f"track whose name starts with '{af.BEATS_TRACK_PREFIX}' "
            "(case-insensitive). When a time "
            "selection is active, only the boundaries inside it are snapped; -f "
            "always quantizes the whole track."
        ),
    )
    parser.add_argument(
        "-t",
        "--transpose",
        type=int,
        default=None,
        metavar="SEMITONES",
        help=(
            "Transpose the chords in the selected label track of the open "
            "project by SEMITONES half steps (negative transposes down), in "
            "place, and update its versioned .txt to match. Label text that is "
            "not a chord (section markers, lyric cues, fingerings) is left alone "
            "and reported. Chords are spelled with flats unless -s is given. "
            "When a time selection is active, only the labels starting inside it "
            "are transposed; -f always transposes the whole track."
        ),
    )
    parser.add_argument(
        "-s",
        "--sharps",
        action="store_true",
        help=(
            "With -t, spell transposed chords with sharps (A#) instead of the "
            "default flats (Bb)."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=get_version_info(_pkg_version("rebuildap")),
    )
    args = parser.parse_args()
    # -f is valid for -c (compare every label file, past the mtime gate), -q, -t
    # and the no-argument export. It is meaningless for a rebuild or a label
    # import - note -c takes a filename, so a filename alone does not disqualify.
    if args.force and (args.label or (args.filename and not args.check)):
        parser.error("--force applies to -c, -q, -t or the no-argument export.")
    if args.quantize is not None and (args.filename or args.check or args.label):
        parser.error(
            "--quantize operates on the open project; not with a filename, -c, or -l."
        )
    if args.transpose is not None and (args.filename or args.check or args.label):
        parser.error(
            "--transpose operates on the open project; not with a filename, -c, or -l."
        )
    if args.quantize is not None and args.transpose is not None:
        parser.error(
            "--quantize and --transpose both rewrite the selected label track; "
            "run one at a time."
        )
    if args.sharps and args.transpose is None:
        parser.error("--sharps only applies with --transpose/-t.")
    rebuild(
        args.filename,
        args.verbose,
        args.label,
        args.check,
        save=not args.no_save,
        # The CLI surface is -f; the internal concept stays "deep" because that
        # is what it does - skip the mtime gate and compare everything.
        deep=args.force,
        force=args.force,
        quantize=args.quantize,
        transpose=args.transpose,
        sharps=args.sharps,
    )


if __name__ == "__main__":
    main()
