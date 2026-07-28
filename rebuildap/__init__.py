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
    Check whether the Audacity file is newer than the label files.
    Export those label tracks whose corresponding label files are older than the Audacity file
    and compare their contents with their corresponding label files.

    Audacity is opened only when *some* label file is older than the ``.aup3``;
    once open, every label file is compared. With ``deep=True`` (the CLI's
    ``check -f``) it is opened even when they are all newer. That costs an Audacity
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
    exactly what ``quantize`` and ``transpose`` cause by rewriting a ``.txt``.
    """
    aup3_mtime = filename.stat().st_mtime
    return any(f.stat().st_mtime < aup3_mtime for f in label_files)


def _check_label_age_via_getinfo(filename, label_files):
    """Non-interactive path: compare versioned files against GetInfo content in memory.

    Every label file for the project is compared -- see :func:`_any_label_file_older`
    for why there is no per-file gate.
    """
    contents = af.get_label_tracks_content_via_getinfo()
    # Beside the project being checked, not in the cwd: `rebuildap check
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
    ``check``. Report each and point at the export route, but never auto-write it:
    the versioned ``.txt`` files are the source of truth, and a bare ``export``
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


def _report_other_open_projects():
    """Name the other projects that are open, when the frontmost one is a dead end.

    The pipe acts on the *frontmost* project window, so "this project is empty"
    is a riddle when the window in front is a scratch project and the ones the
    user meant are sitting behind it. Silent when there is nowhere to point.
    """
    stems = af._open_project_aup3_stems()
    if not stems:
        return
    print(
        "Other projects are open - bring the one you mean to the front:",
        file=sys.stderr,
    )
    for stem in stems:
        print(f"{af.LIST_BULLET}{stem}", file=sys.stderr)


def prerequisites_met() -> bool:
    """Whether the open Audacity project can be worked with, saying why if not.

    Every branch reports unconditionally rather than under ``-v``: these all end
    with the command doing nothing, and a silent do-nothing run is
    indistinguishable from a broken one -- the same bug `check`'s "nothing to do"
    and a bare `export`'s silent success were both fixed for.
    """
    if not ap.is_audacity_running():
        print("Audacity is not running; nothing to export.", file=sys.stderr)
        return False
    if not ap.is_audacity_window_open():
        print("No Audacity window is open; nothing to export.", file=sys.stderr)
        return False
    if af.is_project_empty():
        print(
            "The frontmost Audacity project is empty; nothing to export.",
            file=sys.stderr,
        )
        _report_other_open_projects()
        return False
    if not af.get_label_tracks():
        print(
            "The frontmost Audacity project has no label tracks; nothing to export.",
            file=sys.stderr,
        )
        _report_other_open_projects()
        return False

    return True


def _open_in_audacity(path, verbose):
    """Start Audacity if needed and open ``path``, refusing if it is already open.

    Shared by ``build`` and a file-named ``export`` -- the two commands that hand
    Audacity a path. A single explicit target, unlike the sweep in ``check``:
    report cleanly rather than with a traceback, but exit non-zero, since the
    work the user asked for did not happen.
    """
    try:
        af.assert_not_already_open(path)
        ap.assert_audacity(verbose)
        af.open_audio(path, verbose)
    except af.ProjectAlreadyOpenError as e:
        raise SystemExit(f"{e}") from e


def _build_project(audio, verbose=False, save=True):
    """``build``: import an audio file plus the label files beside it, and save.

    The versioned ``*_<stem>.txt`` files next to ``audio`` become label tracks;
    the result is saved as ``<stem>.aup3`` beside the audio unless ``save`` is
    False (the CLI's ``-n``).
    """
    audio = Path(audio)
    if af.is_audacity_project(audio):
        raise SystemExit(
            f"{audio.name} is already an Audacity project, so there is nothing to "
            f"rebuild. Use `rebuildap export {audio.name}` to export its label "
            "tracks."
        )
    # Rebuilding audio into a project whose .aup3 already exists throws the
    # result away: save_project_if_absent will decline to overwrite, leaving
    # the rebuilt project open and *unsaved* - and an unsaved project cannot
    # be closed safely, since Cmd-W on one raises "Save changes?", the dialog
    # that wedges the scripting pipe. The existing file is knowable up front,
    # so none of that work is started. With -n the throwaway window is what
    # the user asked for, so this does not apply.
    if save:
        existing = af.aup3_path_for(audio)
        if existing.exists():
            raise SystemExit(
                f"{existing.name} already exists beside {audio.name} and is "
                "never overwritten - it is your working copy and may hold edits "
                "the label files don't have. Nothing was rebuilt. Use -n to "
                "rebuild into an unsaved window anyway, or move the existing "
                f"{existing.name} aside first."
            )
    # Validate label files before starting Audacity: a malformed one otherwise
    # crashes mid-rebuild, after the audio is imported and the window is open
    # but unsaved (nothing can then close it safely).
    try:
        af.assert_label_files_importable(audio)
    except af.LabelFormatError as e:
        raise SystemExit(f"{e}") from e
    _open_in_audacity(audio, verbose)
    if verbose:
        print(f"rebuilt Audacity project from audio and labels ({audio.name})")
    if not save:
        if verbose:
            print(
                "Leaving the rebuilt project open (--no-save); closing an "
                "unsaved project would raise a 'Save changes?' dialog."
            )
        return
    saved = af.save_project_if_absent(audio, verbose)
    if saved is None:
        if verbose:
            print(
                "Leaving the rebuilt project open: it was not saved, so "
                "closing it would raise a 'Save changes?' dialog."
            )
        return
    # Keep the label files from reading as stale to `check`; they are what the
    # project was just built from.
    af.touch_label_files(audio, saved, verbose)
    # Saving retitles the window to the .aup3 stem, which both identifies it as
    # ours and makes Cmd-W close silently. An unsaved project would instead
    # raise "Save changes?", a dialog that wedges the scripting pipe - so the
    # window is only ever closed once it is safely on disk.
    ap.close_owned_window(saved.stem, verbose)


def _export_labels(aup3=None, verbose=False, force=False):
    """``export``: write label tracks out as versioned ``.txt`` files.

    With ``aup3`` the named project is opened and every label track exported
    beside it; without one the already-open project is used, which is where
    ``force`` applies (see :func:`_resolve_export_dir`).
    """
    if aup3 is None:
        if prerequisites_met():
            _export_open_project_labels(verbose, force)
        return
    aup3 = Path(aup3)
    if not af.is_audacity_project(aup3):
        raise SystemExit(
            f"{aup3.name} is not an Audacity project, so it has no label tracks "
            f"to export. Use `rebuildap build {aup3.name}` to rebuild a project "
            "from audio and its label files."
        )
    _open_in_audacity(aup3, verbose)
    if verbose:
        print(f"exporting labels from Audacity project ({aup3.name})")
    af.export_label_tracks_via_getinfo(aup3)
    # TODO: export audio tracks, same naming scheme as labels (but ending in mp3)
    #       song track: "orig"
    #       other tracks: guitar (etc.)


def _import_label_file(labelfile, verbose=False):
    """``import``: add a label file to the open project as a label track."""
    if verbose:
        print("importing label into open Audacity project.")
    try:
        af.make_label_track_from_file(Path(labelfile))
    except af.LabelFormatError as e:
        raise SystemExit(f"{e}") from e


def _open_project_stem_or_exit():
    """The open project's ``.aup3`` stem, or a clean exit when it is ambiguous.

    Shared by every command that acts on whatever project is open (a bare
    ``export``, ``quantize``, ``transpose``) -- all three name their versioned
    ``.txt`` files after it, so all three must refuse identically rather than
    write one project's labels into another's files.
    """
    try:
        return af.open_project_stem()
    except af.ProjectIdentityError as e:
        raise SystemExit(f"{e}") from e


def _resolve_export_dir(stem, force=False):
    """Where the open project's versioned ``.txt`` files may be written, or
    ``None`` to refuse.

    These are the source-of-truth files, so they only ever go into the current
    directory. When cwd is not the project's own directory: point at where
    Audacity's Open Recent says it lives and refuse (return ``None``) so the
    user cd's there; ``force`` overrides that and returns cwd anyway; and when
    nothing can be suggested, warn but fall back to cwd rather than block.
    Messaging goes to stderr. Shared by a bare ``export`` and ``quantize``.
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
    stem = _open_project_stem_or_exit()
    if _resolve_export_dir(stem, force) is None:
        return

    if af.get_selected_label_track_indices():
        if verbose:
            print("exporting selected label track")
        exported = af.export_selected_label_tracks_via_getinfo(stem=stem)
    else:
        if verbose:
            print("exporting all label tracks")
        exported = af.export_label_tracks_via_getinfo(stem=stem)
    # Reported unconditionally, not only under -v: a silent export looked
    # like nothing happened. Names each track and the full path written.
    _report_exports(exported)


def _resolve_project_dir(stem, flag):
    """Directory for an in-place mode's versioned ``.txt`` -- the open project's own.

    Unlike a bare ``export`` (which only ever writes cwd), the in-place commands
    (``quantize``, ``transpose``) write to wherever the project actually lives,
    so the file and the just-modified project stay consistent no matter what cwd
    it was run from: cwd when it holds the project, else the single directory Audacity's
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


def _quantize_open_project(beats_track=None, verbose=False, force=False):
    """Quantize the selected label track to a beats track, in place, then persist.

    ``beats_track`` names the reference track, or is None to find it. Only
    boundaries inside the current time selection are snapped (whole track when
    nothing is selected); ``force`` (``-f``) quantizes the whole track without
    reading the selection, and is the escape hatch the message points at when the
    selection cannot be read. The quantized labels are written straight to the
    project's own directory (no read-back export).

    The write directory is resolved *before* the project is touched, so a
    location we cannot write to is a clean refusal rather than a quantized-but-
    unpersisted half-state.
    """
    if not prerequisites_met():
        return
    stem = _open_project_stem_or_exit()
    out_dir = _resolve_project_dir(stem, "quantize")
    if out_dir is None:
        return
    try:
        target_name, _idx, content, changed = af.quantize_selected_label_track(
            beats_track, verbose, whole_track=force
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

    Equivalence uses the same normalization ``check`` compares with
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
    """State plainly what ``quantize`` did, across the four project-changed/file-written
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


def _transpose_open_project(semitones, sharps=False, verbose=False, force=False):
    """Transpose the selected label track's chords in place, then persist.

    Mirrors :func:`_quantize_open_project`: the write directory is resolved
    *before* the project is touched, only labels starting inside the current time
    selection are transposed (whole track when nothing is selected, or with
    ``-f``), and the result is written straight to the project's own directory.

    ``sharps`` (``-s``) opts out of the flat spelling ``transpose`` defaults to.
    """
    if not prerequisites_met():
        return
    stem = _open_project_stem_or_exit()
    out_dir = _resolve_project_dir(stem, f"transpose {semitones}")
    if out_dir is None:
        return
    try:
        target_name, _idx, content, changed, skipped = (
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
    """State plainly what ``transpose`` did, naming the spelling it used.

    The spelling is always named: rebuildap defaults to flats while the sister
    library defaults to sharps, so a chart coming back respelled must never be a
    silent surprise (decisions.md 2026-07-25 12:45). Labels that held no chord are
    named too -- silence there is the failure mode ``quantize`` and a bare ``export``
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


_PROG = "rebuildap"

# The commands, in the order --help lists them: the two that take a file first,
# then the two that read a project, then the two that rewrite one in place.
_COMMANDS = ("build", "export", "check", "import", "quantize", "transpose")

# A first token that is not a command gets this one prepended, so `rebuildap`,
# `rebuildap -v` and `rebuildap song.aup3` all keep working.
_DEFAULT_COMMAND = "export"

# Asked at the top level these are about rebuildap itself, not about a command,
# so they must reach the top-level parser rather than _DEFAULT_COMMAND's.
_TOP_LEVEL_OPTIONS = ("-h", "--help", "-V", "--version")

# The mode flags this CLI used to have, and the command that replaced each.
# Retired flags are in muscle memory and in shell history, and argparse's bare
# "unrecognized arguments" would not say where they went.
_RETIRED_FLAGS = {
    "-c": "check",
    "--check": "check",
    "-l": "import",
    "--label": "import",
    "-q": "quantize",
    "--quantize": "quantize",
    "-t": "transpose",
    "--transpose": "transpose",
    "-n": "build",
    "--no-save": "build",
    "-s": "transpose",
    "--sharps": "transpose",
}


def _resolve_command(parser, argv):
    """Return ``argv`` with a command in front of it, or exit naming one.

    Only the leading token decides: a command runs as given, ``-h``/``-V`` go to
    the top-level parser, and anything else is treated as an argument to
    :data:`_DEFAULT_COMMAND`. A retired mode flag is caught here rather than
    handed on, since ``export -c`` would otherwise fail as an unknown option.
    """
    if argv and (argv[0] in _COMMANDS or argv[0] in _TOP_LEVEL_OPTIONS):
        return argv
    for token in argv:
        command = _RETIRED_FLAGS.get(token.split("=", 1)[0])
        if command:
            parser.error(
                f"{token} is no longer an option - it is now a command: "
                f"`{_PROG} {command}`. See `{_PROG} --help`."
            )
    return [_DEFAULT_COMMAND, *argv]


def _build_parser():
    """The command-line surface: one subparser per command.

    Modes used to be flags on a single parser, which took five hand-written
    guards to reject the combinations argparse could not express, and left ``-f``
    with four context-specific meanings. Each command now declares its own
    arguments, so those combinations are unrepresentable and every ``--force``
    means exactly one thing. See decisions.md 2026-07-28 18:09.
    """
    parser = argparse.ArgumentParser(
        prog=_PROG,
        # Hard-wrapped: RawDescriptionHelpFormatter (needed for the epilog's
        # layout) prints the description verbatim rather than reflowing it.
        description=(
            "Keep Audacity label tracks in plain text: export them, check them\n"
            "against the project, transform them in place, and rebuild the whole\n"
            "project from them."
        ),
        epilog=(
            f"Run `{_PROG} COMMAND --help` for a command's own options.\n"
            f"\n"
            f"`{_PROG}` on its own means `{_PROG} export`: the open project's\n"
            f"label tracks are written into the current directory - the selected\n"
            f"ones, or all of them if none are selected.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=get_version_info(_pkg_version(_PROG)),
    )
    # -v lives on a shared parent, so it follows the command. Declaring it on the
    # top-level parser as well would not work: argparse writes the subparser's
    # defaults into the same namespace afterwards, silently resetting it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose mode."
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    build = commands.add_parser(
        "build",
        parents=[common],
        help="Rebuild a project from an audio file and its label files.",
        description=(
            "Import AUDIO into a new Audacity project, turning every "
            "*_<stem>.txt file beside it into a label track, and save the "
            "result as <stem>.aup3 next to the audio."
        ),
    )
    build.add_argument(
        "audio",
        metavar="AUDIO",
        help=(
            "Audio file to rebuild from (.mp3, .wav, anything Audacity "
            "imports). Every *_<stem>.txt beside it becomes a label track."
        ),
    )
    build.add_argument(
        "-n",
        "--no-save",
        action="store_true",
        help=(
            "Don't save the rebuilt project as <audio-stem>.aup3 beside the "
            "audio file. By default it is saved when no .aup3 exists yet; an "
            "existing one is never overwritten."
        ),
    )
    build.set_defaults(
        func=lambda args: _build_project(
            audio=args.audio, verbose=args.verbose, save=not args.no_save
        )
    )

    export = commands.add_parser(
        "export",
        parents=[common],
        help="Export label tracks to versioned .txt files.",
        description=(
            "Write label tracks out as the versioned *_<stem>.txt files, named "
            "after the project's .aup3 stem. With AUP3 they land beside that "
            "file; with no argument the open project is used and they land in "
            "the current directory, which is where these source-of-truth files "
            "belong."
        ),
    )
    export.add_argument(
        "aup3",
        nargs="?",
        metavar="AUP3",
        help=(
            "Project whose label tracks to export. Omit to export the open "
            "Audacity project's - the selected tracks, or all of them if none "
            "are selected."
        ),
    )
    export.add_argument(
        "-f",
        "--force",
        action="store_true",
        help=(
            "Export the open project into the current directory even when it "
            "appears to live elsewhere. Applies only without AUP3, which "
            "exports beside itself."
        ),
    )
    export.set_defaults(
        func=lambda args: _export_labels(
            aup3=args.aup3, verbose=args.verbose, force=args.force
        )
    )

    check = commands.add_parser(
        "check",
        parents=[common],
        help="Compare a project's label tracks with the versioned .txt files.",
        description=(
            "Report where the project and its versioned label files have "
            "diverged, and update any .txt whose content changed. Label tracks "
            "that exist only in Audacity are flagged too."
        ),
    )
    check.add_argument(
        "aup3",
        nargs="?",
        metavar="AUP3",
        help="Project to check. Omit to use the sole .aup3 in the current directory.",
    )
    check.add_argument(
        "-f",
        "--force",
        action="store_true",
        help=(
            "Open Audacity and compare every label file even when all of them "
            "are newer than the .aup3. Opens Audacity every run, and catches "
            "label files rewritten (by e.g. git checkout, touch) without "
            "changing the project."
        ),
    )
    check.set_defaults(
        func=lambda args: check_label_age(
            filename=args.aup3,
            verbose=args.verbose,
            # The CLI surface is --force; the internal concept stays "deep"
            # because that is what it does - skip the mtime gate and compare
            # everything.
            deep=args.force,
        )
    )

    import_ = commands.add_parser(
        "import",
        parents=[common],
        help="Import a label file into the open project as a label track.",
        description=(
            "Add LABELFILE to the open Audacity project as a label track. The "
            "file may be 1-, 2- or 3-column; it is normalized on import and "
            "never rewritten."
        ),
    )
    import_.add_argument(
        "labelfile",
        metavar="LABELFILE",
        help="Label file to import; its <name>_<stem>.txt prefix names the track.",
    )
    import_.set_defaults(
        func=lambda args: _import_label_file(
            labelfile=args.labelfile, verbose=args.verbose
        )
    )

    quantize = commands.add_parser(
        "quantize",
        parents=[common],
        help="Snap the selected label track to a beats track, in place.",
        description=(
            "Snap the selected label track's boundaries to a beats label track "
            "already in the open project, re-import it at its original "
            "position, and update its versioned .txt to match."
        ),
    )
    quantize.add_argument(
        "beats_track",
        nargs="?",
        metavar="BEATS_TRACK",
        help=(
            "Label track to snap to. Omit to auto-detect it: the sole label "
            f"track whose name starts with '{af.BEATS_TRACK_PREFIX}' "
            "(case-insensitive)."
        ),
    )
    quantize.add_argument(
        "-f",
        "--force",
        action="store_true",
        help=(
            "Quantize the whole track without reading the time selection. By "
            "default only the boundaries inside an active selection are "
            "snapped."
        ),
    )
    quantize.set_defaults(
        func=lambda args: _quantize_open_project(
            beats_track=args.beats_track, verbose=args.verbose, force=args.force
        )
    )

    transpose = commands.add_parser(
        "transpose",
        parents=[common],
        help="Transpose the selected label track's chords, in place.",
        description=(
            "Transpose the chords in the open project's selected label track by "
            "SEMITONES half steps and update its versioned .txt to match. Label "
            "text that is not a chord (section markers, lyric cues, fingerings) "
            "is left alone and reported."
        ),
    )
    transpose.add_argument(
        "semitones",
        type=int,
        metavar="SEMITONES",
        help="Half steps to transpose by; negative transposes down.",
    )
    transpose.add_argument(
        "-s",
        "--sharps",
        action="store_true",
        help=(
            "Spell transposed chords with sharps (A#) instead of the default "
            "flats (Bb)."
        ),
    )
    transpose.add_argument(
        "-f",
        "--force",
        action="store_true",
        help=(
            "Transpose the whole track without reading the time selection. By "
            "default only the labels starting inside an active selection are "
            "transposed."
        ),
    )
    transpose.set_defaults(
        func=lambda args: _transpose_open_project(
            semitones=args.semitones,
            sharps=args.sharps,
            verbose=args.verbose,
            force=args.force,
        )
    )
    return parser


def main(argv=None):
    parser = _build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_resolve_command(parser, argv))
    # The one combination still worth rejecting by hand: a named .aup3 exports
    # beside itself, so there is no current-directory decision for -f to
    # override. Accepting and ignoring it is how a flag comes to mean nothing.
    if args.command == "export" and args.aup3 and args.force:
        parser.error(
            "--force applies to the open-project export; "
            "`export AUP3` writes beside that file."
        )
    args.func(args)


if __name__ == "__main__":
    main()
