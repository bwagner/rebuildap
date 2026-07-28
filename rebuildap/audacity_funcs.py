#!/usr/bin/env python

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, Iterable, List, Optional, Tuple

import pyaudacity as pa
import pyperclip
from transpose import transpose_label_text

from .utils import LabelFormatError, normalize_label_line


def _pa_do_timed(command: str, timeout: float) -> str:
    """Run ``pa.do(command)`` in a daemon thread with a hard timeout.

    Raises ``TimeoutError`` if ``pa.do`` does not return within ``timeout``
    seconds. ``pa.do`` blocks on FIFO open when Audacity's mod-script-pipe
    is wedged, and stays blocked for any pending response; a plain
    ``try/except`` cannot bound it.
    """
    result: dict = {"resp": None, "err": None}

    def worker():
        try:
            result["resp"] = pa.do(command)
        except Exception as e:  # noqa: BLE001 — propagate any exception via result
            result["err"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"pa.do({command[:60]!r}) did not return within {timeout}s")
    if result["err"] is not None:
        raise result["err"]
    return result["resp"]


def _drain_read_pipe() -> bytes:
    """Non-blocking read-and-discard of any bytes pending in the from-Audacity FIFO.

    Stale bytes can linger after a ``_pa_do_timed`` timeout where the leaked
    daemon thread held the read pipe open — Audacity eventually writes the
    response, and those bytes sit in the kernel FIFO buffer, poisoning the
    next caller. Calling this before a fresh round-trip clears the slate.
    Returns whatever was drained so callers can log it.
    """
    path = f"/tmp/audacity_script_pipe.from.{os.getuid()}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return b""
    chunks = []
    try:
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def ping_pipe(timeout: float = 3.0) -> None:
    """Cheap end-to-end no-op round-trip to confirm mod-script-pipe is responsive.

    Drains any stale bytes first (from a previously timed-out response),
    then sends ``GetInfo: Type=Tracks`` — returns quickly on a healthy pipe
    and has no side effects. Use before issuing expensive or state-changing
    commands (``OpenProject2``, ``Close:``, ``ExportLabels:``) so we fail
    fast on a wedged pipe rather than burning each command's full timeout.

    Raises ``TimeoutError`` if Audacity doesn't reply within ``timeout``.
    """
    drained = _drain_read_pipe()
    if drained:
        preview = drained[:200].decode("utf-8", errors="replace")
        if len(drained) > 200:
            preview += "..."
        print(
            f"[pipe drain] discarded {len(drained)} stale bytes: {preview!r}",
            file=sys.stderr,
        )
    _pa_do_timed("GetInfo: Type=Tracks", timeout=timeout)


"""
audacity_funcs.py

References
[1] https://manual.audacityteam.org/man/scripting_reference.html

"""

LABEL_PART = "part"
LABEL_CHORD = "chord"
LABEL_LYRIC = "lyric"
LABEL_BAR = "bar"
LABEL_BEAT = "beat"
LABEL_PRIORITY_ORDER = [LABEL_PART, LABEL_CHORD, LABEL_LYRIC]

AUDACITY_EXTENSION = "aup3"

# Marker for the "Open Recent" submenu inside a GetInfo: Type=Menus response,
# and the pattern for the .aup3 paths its entries carry as their "label".
RECENT_MENU_MARKER = '"label":"Open Recent"'
RECENT_AUP3_LABEL_RE = rf'"label":"([^"]+\.{AUDACITY_EXTENSION})"'
MENU_TOP_LEVEL_RE = r'"depth":1'

# Prefix for one item of a list in user-facing output, one per line.
LIST_BULLET = " - "


class ProjectAlreadyOpenError(RuntimeError):
    """The project is already open in another Audacity window.

    Distinct from a timeout: sending ``OpenProject2:`` anyway would raise a
    modal alert that blocks the command and wedges the pipe, so this is raised
    *instead of* opening. The window is left alone — it may be the user's, and
    it may hold edits the label files do not have.
    """


class ProjectIdentityError(RuntimeError):
    """Which open project the commands apply to cannot be determined.

    Raised by :func:`open_project_stem` when several differently-named projects
    are open *and* the frontmost-window tiebreaker does not apply -- something
    other than a project is in front (a modal dialog, the About box), or the
    front window cannot be read at all. Since the stem decides which versioned
    ``.txt`` files get written, guessing here would overwrite another project's
    source of truth, so this refuses instead -- the same posture
    :func:`close_owned_window` takes on an ambiguous window title.
    """


class LabelTrackError(RuntimeError):
    """A request to transform a label track in place cannot be carried out.

    The shared base for the in-place label-track modes. Raised for the
    user-actionable precondition failures that are not specific to one mode --
    today only "no single label track is selected", which every such mode needs,
    since the mode acts on exactly one track and the swap removes the *selected*
    one. The CLI turns it into a clean ``SystemExit`` naming the problem.
    """


class QuantizeError(LabelTrackError):
    """The ``quantize`` request cannot be carried out as asked.

    Adds the quantize-specific precondition failures to
    :class:`LabelTrackError`: the named or auto-detected beats reference is
    missing or ambiguous, the reference is the target itself, or
    quantize_labels.py cannot be found.
    """


class SelectionReadError(RuntimeError):
    """The current Audacity time selection could not be read via Nyquist.

    Deliberately *not* a :class:`LabelTrackError`: the fix is different (re-run
    with ``-f`` to act on the whole track), so the CLI catches it separately and
    says so -- being outside the hierarchy keeps that from depending on except
    order. Raised when the Nyquist selection accessor produces no parseable
    result (e.g. it does not exist on this Audacity version).
    """


# when mod-script-pipe worked out fine:
RESPONSE_OK = "\nBatchCommand finshed: OK\n"

KIND_AUDIO = "wave"
KIND_LABEL = "label"
PROPERTY_CHANNELS = "channels"
PROPERTY_END = "end"
PROPERTY_FOCUSED = "focused"
PROPERTY_KIND = "kind"
PROPERTY_MUTED = "mute"
PROPERTY_NAME = "name"
PROPERTY_PAN = "pan"
PROPERTY_SELECTED = "selected"
PROPERTY_SOLO = "solo"
PROPERTY_START = "start"
PROPERTY_VOLUME = "volume"

SELECT_MODE_SET = "Set"
SELECT_MODE_ADD = "Add"
SELECT_MODE_REMOVE = "Remove"

# Scripting commands that move the *focused* track one row and carry the focus
# with it (Tracks > Move Track Up/Down). Used to restore a re-imported label
# track to the position its predecessor held.
CMD_TRACK_MOVE_UP = "TrackMoveUp"
CMD_TRACK_MOVE_DOWN = "TrackMoveDown"

# Auto-detecting the beats reference track: a label track whose name begins
# with this is taken to be the beats grid when ``quantize`` is given no explicit
# name.
BEATS_TRACK_PREFIX = "beat"

# The sister quantize_labels.py script is shelled out to (kept a separate repo
# so its snapping algorithm stays single-sourced). Expected on $PATH under
# either name -- the extension is often dropped when symlinking a script in.
QUANTIZE_SCRIPT_NAMES = ("quantize_labels.py", "quantize_labels")
QUANTIZE_SCRIPT_URL = "https://github.com/bwagner/quantize_labels"

# Reading the current time selection: mod-script-pipe can't, but a Nyquist script
# runs inside Audacity with the selection as context. This *nil-returning* snippet
# writes "start\nend\n" to {path}; it must return no value (ends at the close), or
# Audacity pops a modal Message dialog that would wedge the pipe. It reports
# Failed! on the pipe (no audio result) -- expected and ignored; the file is the
# real output. Escaped quotes survive the protocol. See ~/.claude/audacity.md.
_NY_SELECTION_TEMPLATE = (
    '(let ((fp (open \\"{path}\\" :direction :output))) '
    '(format fp \\"~a~%~a~%\\" '
    "(get (quote *selection*) (quote start)) "
    "(get (quote *selection*) (quote end))) (close fp))"
)
_SELECTION_READ_TIMEOUT = 15.0
# Selection narrower than this (a bare cursor) counts as "no region" -> whole track.
_NO_REGION_EPSILON = 1e-6
# How far off an edge a boundary may sit and still count as on it.
#
# The selection and the label times are two *independently rounded* views of the
# same instants: `GetInfo` reports label times to ~6 significant digits, and the
# Nyquist selection read prints its floats to ~6 as well, so the two can disagree
# by one unit in the last place. Clicking a label track selects exactly
# first-label-start .. last-label-end, which puts those labels *on* the edges --
# precisely where a strict `<=` drops them, silently leaving the first and last
# label of a click-selected track untransposed/unquantized.
#
# Measured 2026-07-29 (3.7.8): a region set to 5.13097 read back as 5.13098.
# The tolerance is *relative* because the absolute step grows with magnitude
# (~1e-5 at 5 s, ~1e-2 at 3600 s); 1e-5 covers one unit in the sixth significant
# digit at any magnitude, while staying far below any real gap between labels.
# The absolute floor covers times near zero, where the relative test collapses.
_SELECTION_EDGE_REL_TOL = 1e-5
_SELECTION_EDGE_ABS_TOL = 1e-6


def _within_selection(t: float, sel_start: float, sel_end: float) -> bool:
    """True when ``t`` is inside ``[sel_start, sel_end]`` -- or close enough to an
    edge that the difference is below what either side can report."""
    if sel_start <= t <= sel_end:
        return True
    return any(
        math.isclose(
            t, edge, rel_tol=_SELECTION_EDGE_REL_TOL, abs_tol=_SELECTION_EDGE_ABS_TOL
        )
        for edge in (sel_start, sel_end)
    )


# A boundary counts as "moved" for the -v summary if it shifted by more than this.
_ADJUSTMENT_EPSILON = 1e-9

GET_INFO_TRACKS = "Tracks"
GET_INFO_JSON = "JSON"


def is_track_focused(track):
    return track[PROPERTY_FOCUSED] == 1


def is_track_selected(track):
    return track[PROPERTY_SELECTED] == 1


def get_track_start(track):
    return track[PROPERTY_START]


def get_track_end(track):
    return track[PROPERTY_END]


def get_track_pan(track):
    return track[PROPERTY_PAN]


def get_track_volume(track):
    return track[PROPERTY_VOLUME]


def get_track_channels(track):
    return track[PROPERTY_CHANNELS]


def is_track_solo(track):
    return track[PROPERTY_SOLO] == 1


def is_track_muted(track):
    return track[PROPERTY_MUTED] == 1


def is_audio_track(track):
    return track[PROPERTY_KIND] == KIND_AUDIO


def get_track_name(track):
    return track[PROPERTY_NAME]


@contextmanager
def save_clipboard():
    original_content = pyperclip.paste()
    try:
        yield
    finally:
        pyperclip.copy(original_content)


@contextmanager
def save_selection():
    idx = get_selected_track_indices()
    try:
        yield
    finally:
        select_tracks(idx)


@contextmanager
def save_focus():
    idx = get_focused_track_index()
    try:
        yield
    finally:
        focus_track(idx)


def is_project_empty() -> bool:
    """
    Returns true if project is empty, i.e. has no tracks.
    Tested
    """
    return get_track_count() == 0


def get_tracks() -> List[Dict]:
    """
    Returns a list of dicts representing track meta info.
    Tested
    """
    return json.loads(pa.get_info(GET_INFO_TRACKS, GET_INFO_JSON)[: -len(RESPONSE_OK)])


def get_track_count() -> int:
    """
    Returns number of tracks.
    Tested
    """
    return len(get_tracks())


def quit_audacity():
    """
    Quits Audacity app.
    """
    pa.do("Exit:")


def close_project():
    """
    Closes the Audacity project.
    """
    # note:
    # undoing make_label_track will remove the label track
    # issuing redo after this will recreate the label track but not set its name as it was!
    pa.do("Close:")


def make_label_track(label_track_name: str):
    """
    Makes a label track and gives it the given name.
    Tested
    """
    # note:
    # undoing make_label_track will remove the label track
    # issuing redo after this will recreate the label track but not set its name as it was!
    pa.do("NewLabelTrack:")
    pa.do(f'SetTrack: Name="{label_track_name}"')


def select_first_audio_track():
    """
    Selects first audio track in a project.
    Nyquist needs an audio track to be selected for certain operations, like
    ImportLabels (though this is a particular plugin written by SteveDaulton)
    Tested
    """
    first_audio_track = get_track_indices_by_kind(KIND_AUDIO)[0]
    pa.do(f"SelectTracks: Track={first_audio_track} Mode={SELECT_MODE_SET}")


def assert_label_files_importable(audio_filename: Path) -> None:
    """Validate every label file for ``audio_filename`` before Audacity starts.

    Runs the same normalization the import uses, discarding the result, so a
    malformed label file fails fast with a legible :class:`LabelFormatError`
    and no Audacity window is ever created -- mirroring the aup3-exists and
    already-open guards, which also refuse before launch. ``make_label_track_from_file``
    re-validates as defense in depth for the ``import`` single-file path.
    """
    for label_file in create_labels_glob(audio_filename):
        normalize_label_file_for_import(label_file.expanduser().resolve())


def normalize_label_file_for_import(label_file: Path) -> str:
    """Return ``label_file``'s content in the canonical two-tab label form.

    ``ImportLabels.ny`` requires three tab-separated fields per line; the corpus
    also holds 1-column beat-time and 2-column ``time<TAB>beatnumber`` files,
    which crash it with a bare ``BatchCommand finished: Failed!``. Each line is
    mapped through :func:`normalize_label_line`; blank lines are dropped. A line
    that fits no known shape raises :class:`LabelFormatError` naming the file and
    line number, so the failure is legible. The source file is never modified.
    """
    normalized = []
    for lineno, raw in enumerate(label_file.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        line = normalize_label_line(raw)
        if line is None:
            raise LabelFormatError(
                f"{label_file.name} line {lineno}: not a recognized label "
                f"(expected time, time<TAB>text, or start<TAB>end<TAB>text): {raw!r}"
            )
        normalized.append(line)
    return "".join(normalized)


def make_label_track_from_file(label_file: Path, label_track_name: str = None):
    """
    Makes a new label track from the given file and names the label track according to the given name.

    The versioned file may be 1-, 2-, or 3-column; it is normalized to the
    two-tab form ``ImportLabels.ny`` requires and written to a throwaway temp
    file, which is what gets imported. The source file on disk is left untouched.
    """

    label_track_name = (
        label_track_name
        if label_track_name
        else re.sub(r"_?label_?", "", label_file.stem)
    )
    abs_path = label_file.expanduser().resolve()
    normalized = normalize_label_file_for_import(abs_path)

    # ImportLabels.ny only accepts a *.txt path (its file control filters on it).
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(normalized)
        tmp_path = Path(tf.name)

    try:
        with save_selection():
            select_first_audio_track()  # needed for nyquist
            pa.do("SelTrackStartToEnd:")  # needed for nyquist
            pa.do(f'ImportLabels: fname="{tmp_path}"')
            pa.do(f"SelectTracks: Track={get_track_count() - 1} Mode={SELECT_MODE_SET}")
            pa.do(f'SetTrack: Name="{label_track_name}"')
    finally:
        tmp_path.unlink(missing_ok=True)


def make_label_track_01(label_file: Path, label_track_name: str):
    """
    Makes a new label track from the given file and names the label track according to the given name.
    Uses an unreliable way, hence use not recommended, but might inspire ideas for other funcs.
    """
    pa.do("NewLabelTrack:")
    pa.do(f'SetTrack: Name="{label_track_name}"')
    count = 1
    with save_clipboard:
        with label_file.open() as f:
            for line in f:
                s_e_l = line.strip().split("\t")
                pa.do(
                    f"SelectTime: Start={s_e_l[0]} End={s_e_l[1]} RelativeTo=ProjectStart"
                )
                pyperclip.copy(s_e_l[2] if len(s_e_l) == 3 else str(count))
                count += 1
                pa.do("PasteNewLabel:")


def get_tracks_by_property(prop: str) -> List[Dict]:
    """
    Returns list of meta info dict for tracks conforming to property.
    Tested indirectly
    """
    return [track for track in get_tracks() if prop in track and track[prop]]


def get_track_indices_by_property(prop: str) -> List[int]:
    """
    Returns list of indices of tracks conforming to property.
    Tested indirectly
    """
    return [i for i, track in enumerate(get_tracks()) if prop in track and track[prop]]


def get_focused_tracks():
    """
    Returns list of meta info dict for focused tracks.
    Tested
    """
    return get_tracks_by_property(PROPERTY_FOCUSED)


def get_focused_track_index() -> Optional[int]:
    """
    Returns the index of the focused track.
    Tested
    """
    for i, track in enumerate(get_tracks()):
        if track[PROPERTY_FOCUSED]:
            return i
    return None


def get_selected_tracks():
    """
    Returns list of meta info dict for selected tracks.
    Tested
    """
    return get_tracks_by_property(PROPERTY_SELECTED)


def get_muted_tracks() -> List[Dict]:
    """
    Returns list of meta info dict for muted tracks.
    Tested
    """
    return get_tracks_by_property(PROPERTY_MUTED)


def get_solo_tracks() -> List[Dict]:
    """
    Returns list of meta info dict for solo tracks.
    Tested
    """
    return get_tracks_by_property(PROPERTY_SOLO)


def get_solo_track_indices() -> List[int]:
    """
    Returns list of indices of solo tracks.
    Tested
    """
    return get_track_indices_by_property(PROPERTY_SOLO)


def get_muted_track_indices() -> List[int]:
    """
    Returns list of indices of muted tracks.
    Tested
    """
    return get_track_indices_by_property(PROPERTY_MUTED)


def toggle_solo_track(track: int):
    """
    Toggles solo state of track
    Tested indirectly
    """
    with save_focus():
        focus_track(track)
        pa.do("TrackSolo:")


def solo_track(track: int):
    """
    Soloes the given track.
    Tested
    """
    if track in get_solo_track_indices():
        return
    toggle_solo_track(track)


def solo_tracks(tracks: List[int]):
    """
    Soloes the given tracks.
    Tested
    """
    for track in tracks:
        solo_track(track)


def unsolo_track(track: int):
    """
    Unsoloes the given track.
    Tested
    """
    if track not in get_solo_track_indices():
        return
    toggle_solo_track(track)


def unsolo_tracks(tracks: List[int]):
    """
    Unsoloes the given tracks.
    Tested
    """
    for track in tracks:
        unsolo_track(track)


def focus_track(track: int):
    """
    Focuses the given track.
    Optimizes by finding the closes of first, last, currently focused track to the desired
    new target track.
    Tested
    """
    tc = get_track_count()
    current_track = get_focused_track_index()

    # Calculate distances
    distance_from_first = track
    distance_from_last = tc - track - 1
    distance_from_current = abs(track - current_track)

    # Determine the minimum distance and the corresponding fixpoint
    if (
        distance_from_first <= distance_from_last
        and distance_from_first <= distance_from_current
    ):
        pa.do("FirstTrack:")
        for _ in range(distance_from_first):
            pa.do("NextTrack:")
    elif (
        distance_from_last <= distance_from_first
        and distance_from_last <= distance_from_current
    ):
        pa.do("LastTrack:")
        for _ in range(distance_from_last):
            pa.do("PrevTrack:")
    else:
        if track > current_track:
            for _ in range(distance_from_current):
                pa.do("NextTrack:")
        elif track < current_track:
            for _ in range(distance_from_current):
                pa.do("PrevTrack:")


def _track_move_commands(from_index: int, to_index: int) -> List[str]:
    """The sequence of TrackMove commands that walks a track from one index to
    another. Pure: ``TrackMoveUp`` for each row it must rise, ``TrackMoveDown``
    for each it must fall, empty when it is already in place.
    """
    if to_index < from_index:
        return [CMD_TRACK_MOVE_UP] * (from_index - to_index)
    if to_index > from_index:
        return [CMD_TRACK_MOVE_DOWN] * (to_index - from_index)
    return []


def move_track_to(from_index: int, to_index: int):
    """Move the track at ``from_index`` to ``to_index``.

    Focuses the track first, then issues the move commands; each moves the
    focused track one row and carries the focus with it, so a run of the same
    command walks it the whole way. Tested (arithmetic offline, move live).
    """
    focus_track(from_index)
    for cmd in _track_move_commands(from_index, to_index):
        pa.do(f"{cmd}:")


def mute_track(track: int):
    """
    Mutes the given track.
    Tested
    """
    with save_selection():
        select_track(track)
        pa.do("MuteTracks:")


def mute_tracks(tracks: List[int]):
    """
    Mutes the given tracks.
    Tested
    """
    with save_selection():
        select_tracks(tracks)
        pa.do("MuteTracks:")


def unmute_track(track: int):
    """
    Unmutes the given track.
    Tested
    """
    with save_selection():
        select_track(track)
        pa.do("UnmuteTracks:")


def unmute_tracks(track: List[int]):
    """
    Unmutes the given tracks.
    Tested
    """
    with save_selection():
        select_tracks(track)
        pa.do("UnmuteTracks:")


def select_track(track: int):
    """
    Selects track
    Tested
    """
    unselect_tracks()
    pa.do(f"SelectTracks: Track={track} Mode={SELECT_MODE_ADD}")


def select_tracks(tracks: List[int]):
    """
    Parameters to "SelectTracks:" command (see [1]):
    Track - first track to select, tracks are numbered starting from 0
    TrackCount - how many tracks to select
    Mode - either one of SELECT_MODE_SET, SELECT_MODE_ADD, SELECT_MODE_REMOVE
    Tested
    """
    unselect_tracks()
    for track in tracks:
        pa.do(f"SelectTracks: Track={track} Mode={SELECT_MODE_ADD}")


def unselect_track(idx: int):
    """
    See select_tracks.
    Tested
    """
    pa.do(f"SelectTracks: Track={idx} Mode={SELECT_MODE_REMOVE}")


def unselect_tracks():
    """
    Unselects all tracks.
    Tested
    """
    pa.do("SelectNone:")


def select_tracks_by_kind(kind: str):
    """
    Selects tracks by kind.
    Tested (indirectly)
    """
    return select_tracks(get_track_indices_by_kind(kind))


def select_label_tracks():
    """
    Selects label tracks.
    Tested
    """
    return select_tracks_by_kind(KIND_LABEL)


def select_audio_tracks():
    """
    Selects audio tracks.
    Tested
    """
    return select_tracks_by_kind(KIND_AUDIO)


def get_tracks_by_kind(kind: str) -> List[Dict]:
    """
    Returns list of track meta info for tracks of given kind.
    Tested (indirectly)
    """
    return [track for track in get_tracks() if track[PROPERTY_KIND] == kind]


def get_selected_label_track_indices() -> List[int]:
    """
    Returns list of selected label track indices.
    Tested
    """
    return get_selected_track_indices_by_kind(KIND_LABEL)


def get_selected_audio_track_indices() -> List[int]:
    """
    Returns list of selected audio track indices.
    Tested
    """
    return get_selected_track_indices_by_kind(KIND_AUDIO)


def get_selected_track_indices_by_kind(kind) -> List[int]:
    """
    Returns list of selected track indices by kind.
    Tested indirectly
    """
    tracks_kind = get_track_indices_by_kind(kind)
    tracks_selected = get_selected_track_indices()
    return list(set(tracks_kind) & set(tracks_selected))


def get_selected_track_indices() -> List[int]:
    """
    Returns list of selected track indices.
    Tested
    """
    return [i for i, track in enumerate(get_tracks()) if track[PROPERTY_SELECTED]]


def get_track_indices_by_kind(kind) -> List[int]:
    """
    Returns list of selected track indices by kind.
    Tested (indirectly)
    """
    return [i for i, track in enumerate(get_tracks()) if track[PROPERTY_KIND] == kind]


def get_audio_track_indices() -> List[int]:
    """
    Returns list of audio track indices.
    Tested
    """
    return get_track_indices_by_kind(KIND_AUDIO)


def get_label_track_indices() -> List[int]:
    """
    Returns list of label track indices.
    Tested
    """
    return get_track_indices_by_kind(KIND_LABEL)


def get_label_tracks() -> List[Dict]:
    """
    Returns list of track meta info for label tracks.
    Tested
    """
    return get_tracks_by_kind(KIND_LABEL)


def get_audio_tracks() -> List[Dict]:
    """
    Returns list of track meta info for audio tracks.
    Tested
    """
    return get_tracks_by_kind(KIND_AUDIO)


def remove_selected_tracks():
    """
    Removes selected tracks.
    Tested
    """
    return pa.do("RemoveTracks:")


def undo():
    """
    Undo
    Tested
    """
    return pa.do("Undo:")


def redo():
    """
    Redo
    Tested
    """
    return pa.do("Redo:")


# The interactive `ExportLabels:` export path was retired 2026-07-18. Audacity's
# ExportLabels command takes no parameters (verified against the scripting
# reference), so the save dialog alone decided where artifacts landed and the
# caller had to guess the location afterwards. Its only advantage over the
# GetInfo path below was 6-decimal instead of 3-decimal precision, which the
# 2026-04-21 decision had already judged inaudible. See decisions.md.


# --- non-interactive export via GetInfo --------------------------------------
#
# GetInfo: Type=Labels returns a JSON payload containing all label tracks in a
# single non-interactive call, which sidesteps the save-dialog of
# `ExportLabels:`. The precision trade-off is documented in
# docs/label-export.md.

_OK_MARKERS = ("\nBatchCommand finished: OK\n", "\nBatchCommand finshed: OK\n")


def _strip_ok_marker(raw: str) -> str:
    """Remove Audacity's trailing 'BatchCommand ... OK' line from a pa.do response."""
    for m in _OK_MARKERS:
        if m in raw:
            return raw.split(m)[0]
    return raw


def _parse_labels_response(raw: str) -> Dict[int, List[tuple]]:
    """Parse a GetInfo: Type=Labels response into {track_idx: [(start, end, text), ...]}.

    Times are always returned as floats, even when JSON omitted trailing zeros.
    """
    data = json.loads(_strip_ok_marker(raw))
    out: Dict[int, List[tuple]] = {}
    for entry in data:
        idx, labels = entry[0], entry[1]
        out[idx] = [(float(s), float(e), t) for s, e, t in labels]
    return out


def _parse_tracks_response(raw: str) -> List[dict]:
    """Parse a GetInfo: Type=Tracks response into a list of track dicts."""
    return json.loads(_strip_ok_marker(raw))


def _label_track_names_by_idx(tracks: Iterable[dict]) -> Dict[int, str]:
    """Return {0-based-track-idx: name} for every kind='label' track."""
    return {i: t["name"] for i, t in enumerate(tracks) if t.get("kind") == "label"}


def _format_label_line(start: float, end: float, text: str) -> str:
    """Format one label as Audacity's native .txt line (trailing tab even on empty text)."""
    return f"{start:.6f}\t{end:.6f}\t{text}\n"


def _format_track_txt(labels: Iterable[tuple]) -> str:
    """Concatenate per-label lines into full .txt content."""
    return "".join(_format_label_line(s, e, t) for s, e, t in labels)


def _derive_label_filename(track_name: str, aup3_stem: str) -> str:
    """Build the versioned label filename: `<track>_<stem>.txt`."""
    return f"{track_name}_{aup3_stem}.txt"


def _parse_recent_project_paths(menus_response: str) -> List[Path]:
    """Extract the Open Recent submenu's ``.aup3`` paths from a ``GetInfo:
    Type=Menus`` response.

    Regex-scoped rather than a full-tree ``json.loads``: an unrelated menu label
    in the payload carries an invalid JSON escape that makes strict parsing of
    the whole tree fail. We only need the Open Recent section, so the scan runs
    from its marker to the next top-level (``depth:1``) entry and pulls the
    ``.aup3`` labels in between — ignoring same-suffixed labels elsewhere in the
    menu tree.
    """
    start = menus_response.find(RECENT_MENU_MARKER)
    if start == -1:
        return []
    tail = menus_response[start + len(RECENT_MENU_MARKER) :]
    end = re.search(MENU_TOP_LEVEL_RE, tail)
    section = tail[: end.start()] if end else tail
    return [Path(m) for m in re.findall(RECENT_AUP3_LABEL_RE, section)]


def dir_holds_project(directory: Path, stem: str) -> bool:
    """True if ``directory`` looks like the project's own dir: it holds either
    ``<stem>.aup3`` or a versioned ``*_<stem>.txt`` label file.

    A bare ``export`` only ever writes into the current directory, so this is
    the gate that decides whether cwd is the right place to write — the
    source-of-truth label files must not be scattered into an unrelated dir.
    """
    if (directory / f"{stem}.{AUDACITY_EXTENSION}").exists():
        return True
    return any(directory.glob(f"*_{stem}.txt"))


def open_project_audio_stem(tracks: Optional[List[dict]] = None) -> str:
    """The open project's stem: the name of its first audio track.

    Fetches tracks via GetInfo unless an already-fetched list is passed, letting
    a caller that has them avoid a second round-trip.

    ("audio track" is this codebase's word for it; Audacity's own payload calls
    the kind ``wave``, which is what :data:`KIND_AUDIO` holds.)
    """
    if tracks is None:
        tracks = _parse_tracks_response(pa.do("GetInfo: Type=Tracks"))
    audio = next((t for t in tracks if t.get(PROPERTY_KIND) == KIND_AUDIO), None)
    if audio is None:
        raise RuntimeError("Cannot derive output context: no audio track in project")
    return audio["name"]


def find_recent_project_dirs(stem: str) -> List[Path]:
    """Directories of on-disk ``.aup3`` files named ``stem`` in Audacity's
    **Open Recent** menu. Sorted, de-duplicated; possibly empty.

    Advisory only — never used to auto-write, just to *suggest* where the open
    project lives when cwd is not it. Audacity exposes no per-window file path
    (``AXDocument`` is ``missing value``, there is no ``GetInfo: Type=Project``),
    so Open Recent is the one route to a path, and it is a recency list rather
    than "the file this window holds": it may miss a project evicted from the
    list, and it may list several same-stem projects in different directories.
    Both are fine here — the caller shows the list and lets the user pick.
    """
    try:
        menus = pa.do("GetInfo: Type=Menus")
    except Exception:  # noqa: BLE001 — a flaky pipe just means "no suggestions"
        return []
    dirs = {
        p.parent
        for p in _parse_recent_project_paths(menus)
        if p.stem == stem and p.exists()
    }
    return sorted(dirs)


def _open_project_aup3_stems() -> List[str]:
    """Stems of the ``.aup3`` files that are both **open** and **on disk**.

    Intersects the only two routes Audacity offers, each useless alone:

    - window titles (:func:`audacity_present.audacity_window_names`) say *which
      projects are open* but carry no path, and an unsaved project is titled
      with the stem of the audio it was built from -- a name with no file;
    - Open Recent (:func:`_parse_recent_project_paths`) gives real, full paths
      but is a recency list: it holds closed projects and can evict open ones.

    Taking the intersection keeps only names that are simultaneously an open
    window and a file that exists. Same-stem projects in different directories
    collapse to one entry, which is right here -- the stem is the answer either
    way, so there is nothing to be ambiguous about.
    """
    from . import audacity_present as ap  # deferred: audacity_present imports us

    titles = set(ap.audacity_window_names())
    if not titles:
        return []
    try:
        menus = pa.do("GetInfo: Type=Menus")
    except Exception:  # noqa: BLE001 -- a flaky pipe just means "cannot identify"
        return []
    return sorted(
        {
            p.stem
            for p in _parse_recent_project_paths(menus)
            if p.stem in titles and p.exists()
        }
    )


def open_project_stem() -> str:
    """The open project's identity: the stem of its ``.aup3`` file.

    This is what names the versioned ``<track>_<stem>.txt`` files, so it must be
    the *project file's* stem and not its audio track's name. The two differ
    whenever a project was made by Save-As from another one: a transposed
    variant ``song_G.aup3`` keeps the audio track called ``song``, and naming by
    the track wrote the variant's labels over the original's files.

    With several projects open, the **frontmost** one wins: measured on 3.7.8
    (2026-07-28), mod-script-pipe acts on the frontmost project window, so the
    project the commands will touch is the project whose window is in front.
    Refuses (:class:`ProjectIdentityError`) only when that tiebreaker cannot be
    applied -- something other than a project is frontmost (a modal dialog, the
    About box), or the front window cannot be read at all.

    Falls back to :func:`open_project_audio_stem` when no open window matches a
    file on disk -- a never-saved project has no ``.aup3`` stem to find, and
    blocking its export would be worse than naming it after its audio -- but
    says so on stderr, since a silent fallback is exactly how the audio-track
    name came to be used unnoticed.
    """
    from . import audacity_present as ap  # deferred: audacity_present imports us

    stems = _open_project_aup3_stems()
    if len(stems) == 1:
        # Already unambiguous. Deliberately *not* checked against the frontmost
        # window: a dialog in front of the only open project must not turn a
        # working export into a refusal.
        return stems[0]
    if len(stems) > 1:
        frontmost = ap.frontmost_audacity_window_name()
        if frontmost in stems:
            return frontmost
        # None arrives for an Accessibility refusal as well as for "no windows"
        # (shared -1719), so it is never an answer -- it lands here.
        listed = "\n".join(f"{LIST_BULLET}{s}" for s in stems)
        raise ProjectIdentityError(
            "Several projects are open and the frontmost window is not one of "
            f"them, so it is unclear which these commands apply to:\n{listed}\n"
            "Bring the project you mean to the front, then run rebuildap again."
        )
    stem = open_project_audio_stem()
    print(
        f"Could not identify the open project's .aup3 file, so its label files "
        f"will be named after its audio track ('{stem}'). This is right for a "
        f"project that has never been saved; if it has been saved, check that "
        f"it is still in Audacity's Open Recent menu.",
        file=sys.stderr,
    )
    return stem


def _resolve_output_context(aup3_path=None, stem=None):
    """Return (out_dir: Path, aup3_stem: str).

    When ``aup3_path`` is given, derive both from it. Otherwise this is the
    argument-less ``export`` workflow: write into the current directory, with
    the open project's own :func:`open_project_stem`. (The caller has already decided cwd
    is the right place - see :func:`dir_holds_project` - so no resolution
    happens here.)

    ``stem`` lets a caller that has already resolved it pass it in, so one
    command does not identify the project twice - and, on the fallback path,
    does not explain itself on stderr twice.
    """
    if aup3_path is not None:
        p = Path(aup3_path)
        return p.parent, p.stem
    return Path.cwd(), stem or open_project_stem()


def get_label_tracks_content_via_getinfo() -> Dict[str, str]:
    """Return ``{track_name: .txt content}`` via GetInfo. Non-interactive, no files written.

    Useful for comparing against on-disk versioned label files without the
    side-effect of overwriting them.
    """
    labels_by_idx = _parse_labels_response(
        _pa_do_timed("GetInfo: Type=Labels", timeout=5.0)
    )
    names_by_idx = _label_track_names_by_idx(
        _parse_tracks_response(_pa_do_timed("GetInfo: Type=Tracks", timeout=3.0))
    )
    return {
        names_by_idx[idx]: _format_track_txt(labels)
        for idx, labels in labels_by_idx.items()
        if idx in names_by_idx
    }


def _write_via_getinfo(
    indices: Iterable[int], aup3_path=None, stem=None
) -> List[Tuple[str, Path]]:
    """Shared core: write one .txt per given label-track index.

    Returns ``(track_name, written_path)`` pairs. The name is carried out
    alongside the path so callers can report both without re-deriving the name
    from the filename — which is lossy, since a track name may contain the same
    underscores ``_derive_label_filename`` uses as a separator.
    """
    out_dir, stem = _resolve_output_context(aup3_path, stem)
    labels_by_idx = _parse_labels_response(pa.do("GetInfo: Type=Labels"))
    names_by_idx = _label_track_names_by_idx(
        _parse_tracks_response(pa.do("GetInfo: Type=Tracks"))
    )
    written: List[Tuple[str, Path]] = []
    wanted = set(indices)
    for idx, labels in labels_by_idx.items():
        if idx not in wanted:
            continue
        name = names_by_idx.get(idx)
        if name is None:
            continue
        out_path = out_dir / _derive_label_filename(name, stem)
        out_path.write_text(_format_track_txt(labels))
        written.append((name, out_path))
    return written


def export_label_tracks_via_getinfo(
    aup3_path=None, stem=None
) -> List[Tuple[str, Path]]:
    """Export every label track, one file per track. Returns (name, path) pairs."""
    return _write_via_getinfo(get_label_track_indices(), aup3_path, stem)


def export_selected_label_tracks_via_getinfo(
    aup3_path=None, stem=None
) -> List[Tuple[str, Path]]:
    """Export the selected label tracks, one file per track. Returns (name, path) pairs."""
    return _write_via_getinfo(get_selected_label_track_indices(), aup3_path, stem)


def export_selected_or_all_label_tracks_via_getinfo(
    aup3_path=None, stem=None
) -> List[Tuple[str, Path]]:
    """Export the selected label tracks, or all when none is selected. Returns (name, path) pairs."""
    indices = get_selected_label_track_indices() or get_label_track_indices()
    return _write_via_getinfo(indices, aup3_path, stem)


# --- transforming the selected label track in place (shared spine) ----------
#
# The commands that rewrite one label track in the open project (`quantize`,
# `transpose`) share the same skeleton: resolve the single selected label track,
# resolve how much of it the current time selection covers, compute new content,
# then swap that content in. Only the *computation* differs per command, so it
# stays in that command's own function; the three steps around it live here.
#
# The label-format helpers these lean on (`_write_temp_label_txt`,
# `_labels_from_txt`, `read_time_selection`) are defined further down with the
# quantize block, matching this module's habit of putting helpers after their
# callers.


def _label_tracks(tracks: List[Dict]) -> List[Tuple[int, Dict]]:
    """The ``(index, track)`` pairs of ``tracks`` that are label tracks."""
    return [(i, t) for i, t in enumerate(tracks) if t.get(PROPERTY_KIND) == KIND_LABEL]


def resolve_selected_label_track(tracks: List[Dict]) -> Tuple[int, str]:
    """The single selected label track, as ``(index, name)``.

    Zero or several selected is a :class:`LabelTrackError` -- an in-place mode
    acts on exactly one track, and :func:`replace_label_track` removes whatever
    is *selected*, so "exactly one" is a safety precondition, not just a
    convenience. Pure over the ``tracks`` list, so every branch is offline-testable.
    """
    selected = [(i, t) for i, t in _label_tracks(tracks) if t.get(PROPERTY_SELECTED)]
    if not selected:
        raise LabelTrackError("Select exactly one label track; none is selected.")
    if len(selected) > 1:
        names = ", ".join(t[PROPERTY_NAME] for _, t in selected)
        raise LabelTrackError(
            f"Select exactly one label track; {len(selected)} are selected ({names})."
        )
    index, track = selected[0]
    return index, track[PROPERTY_NAME]


def resolve_selection_scope(whole_track: bool = False) -> Optional[Tuple[float, float]]:
    """The time selection to scope a transform to, or ``None`` for the whole track.

    ``None`` when ``whole_track`` is set (from ``-f``, which also skips the read
    entirely) or when the selection is a bare cursor rather than a region. May
    raise :class:`SelectionReadError`; callers resolve this *before* touching the
    project so a read failure aborts cleanly rather than half-way through.
    """
    if whole_track:
        return None
    selection = read_time_selection()
    if selection[1] - selection[0] <= _NO_REGION_EPSILON:
        return None  # bare cursor / no region -> whole track
    return selection


def replace_label_track(
    target_index: int, target_name: str, new_content: str, current_content: str
) -> bool:
    """Swap ``new_content`` in for the label track at ``target_index``. Returns
    whether the project was modified.

    **Order matters** and is locked by a test: remove the old track first, then
    re-import (which always appends at the bottom), then move the re-import back
    to the row the old track held, then re-select it -- ``make_label_track_from_file``
    restores the prior selection on exit, so the caller would otherwise lose it.

    Removal goes through ``remove_selected_tracks``, so the track at
    ``target_index`` must be the selected one; :func:`resolve_selected_label_track`
    guarantees that.

    When ``new_content`` equals ``current_content`` the whole swap is skipped and
    ``False`` returned -- the ``.aup3`` is left byte-identical, so an unchanged
    track costs no mtime bump and no undo-stack churn.
    """
    if new_content == current_content:
        return False
    tmp = _write_temp_label_txt(new_content)
    try:
        remove_selected_tracks()
        make_label_track_from_file(tmp, target_name)
        new_index = get_track_count() - 1
        move_track_to(new_index, target_index)
        select_tracks([target_index])
    finally:
        tmp.unlink(missing_ok=True)
    return True


# --- quantize a selected label track to a beats track (`rebuildap quantize`) -
#
# Snap the selected label track's boundaries onto the grid of a beats label
# track already in the project, in place: export both to temp files, hand them
# to the sister quantize_labels.py, remove the old track, re-import the
# quantized one under the same name, and move it back to the position the old
# one held. The quantized result is *also* the new source of truth, so it is
# handed back to the caller for writing straight to the versioned .txt -- no
# read-back export from Audacity, and the file and in-project track are
# identical by construction (built from the same bytes).


def _is_beats_track_name(name: str) -> bool:
    """Whether ``name`` names a beats track for auto-detection purposes."""
    return name.lower().startswith(BEATS_TRACK_PREFIX)


def resolve_quantize_targets(
    tracks: List[Dict], reference_name: Optional[str] = None
) -> Tuple[int, str, int, str]:
    """Decide which track to quantize and which to quantize against.

    Returns ``(target_index, target_name, reference_index, reference_name)``.

    - **target**: the single *selected* label track, via the shared
      :func:`resolve_selected_label_track` (raising :class:`LabelTrackError`).
    - **reference**: the label track named ``reference_name`` when given, else
      the sole label track whose name looks like a beats track. Missing,
      ambiguous, or coinciding with the target all raise :class:`QuantizeError`.

    Pure over the ``tracks`` list, so every branch is exercised offline.
    """
    label_tracks = _label_tracks(tracks)
    target_index, target_name = resolve_selected_label_track(tracks)

    if reference_name is None:
        beats = [
            (i, t) for i, t in label_tracks if _is_beats_track_name(t[PROPERTY_NAME])
        ]
        if not beats:
            raise QuantizeError(
                "No beats label track found to quantize against; name one "
                "explicitly with `rebuildap quantize <track>`."
            )
        if len(beats) > 1:
            names = ", ".join(t[PROPERTY_NAME] for _, t in beats)
            raise QuantizeError(
                f"Multiple beats label tracks found ({names}); name the one to "
                "use with `rebuildap quantize <track>`."
            )
        reference_index, reference = beats[0]
    else:
        matches = [
            (i, t) for i, t in label_tracks if t[PROPERTY_NAME] == reference_name
        ]
        if not matches:
            raise QuantizeError(
                f"No label track named '{reference_name}' to quantize against."
            )
        if len(matches) > 1:
            raise QuantizeError(
                f"Several label tracks are named '{reference_name}'; cannot tell "
                "which is the reference."
            )
        reference_index, reference = matches[0]

    if reference_index == target_index:
        raise QuantizeError(
            f"The selected track '{target_name}' is also the reference beats "
            "track; select the track to quantize and keep the beats track as "
            "the reference."
        )
    return target_index, target_name, reference_index, reference[PROPERTY_NAME]


def locate_quantize_script() -> Path:
    """Path to quantize_labels.py on ``$PATH`` (either name in
    :data:`QUANTIZE_SCRIPT_NAMES`). Raises :class:`QuantizeError` naming both
    when neither is found.
    """
    for name in QUANTIZE_SCRIPT_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    raise QuantizeError(
        "quantize_labels.py not found on $PATH (looked for "
        f"{' and '.join(QUANTIZE_SCRIPT_NAMES)}). Get it from "
        f"{QUANTIZE_SCRIPT_URL} and put it on your PATH, e.g. symlink it into a "
        "directory that is on $PATH."
    )


def quantize_command(
    script: Path, reference_file: Path, target_file: Path
) -> List[str]:
    """The argv to quantize ``target_file`` in place to ``reference_file``'s grid.

    quantize_labels.py takes ``reference_file target_file`` positionally; ``-i``
    rewrites the target rather than printing to stdout. Run directly -- it is an
    executable script with a uv shebang, so no interpreter prefix is needed.
    """
    return [str(script), "-i", str(reference_file), str(target_file)]


def quantize_selected_label_track(
    reference_name: Optional[str] = None,
    verbose: bool = False,
    whole_track: bool = False,
) -> Tuple[str, int, str, bool]:
    """Quantize the selected label track to a beats track, in place.

    Returns ``(target_name, target_index, quantized_content, changed)`` --
    the quantized track's name, the position it was restored to, the quantized
    labels in canonical ``.txt`` form (for the caller to write as the versioned
    source of truth), and whether the project was actually modified. Raises
    :class:`QuantizeError` on any precondition failure. See the module section
    header for the flow.

    The project stem is deliberately *not* returned: it is the caller's business
    (see :func:`open_project_stem`), and deriving it here from the audio track was
    how a transposed variant came to overwrite its original's label files.

    **Selection scope.** With ``whole_track`` false (the default), only label
    boundaries lying inside the current Audacity time selection are snapped --
    read via :func:`read_time_selection` (which may raise
    :class:`SelectionReadError`, resolved *before* the project is touched). A
    bare cursor / no region falls back to the whole track. ``whole_track`` (from
    ``-f``) skips the selection read and quantizes everything.

    When the result equals the track's current content (already on the grid, or
    the selection caught no boundary), the remove/import/move swap is skipped
    entirely -- the project is left byte-identical -- and ``changed`` is ``False``.
    """
    tracks = get_tracks()
    target_index, target_name, _reference_index, reference_name = (
        resolve_quantize_targets(tracks, reference_name)
    )
    contents = get_label_tracks_content_via_getinfo()
    script = locate_quantize_script()

    # Resolve the selection scope before mutating anything, so a selection-read
    # failure aborts cleanly (the caller turns it into "re-run with -f").
    selection = resolve_selection_scope(whole_track)

    ref_tmp = _write_temp_label_txt(contents[reference_name])
    target_tmp = _write_temp_label_txt(contents[target_name])
    try:
        if verbose:
            print(
                f"Quantizing '{target_name}' to '{reference_name}' (via {script.name})."
            )
        # quantize_labels rewrites target_tmp in place to the reference grid. Its
        # own summary is captured so it does not bleed into rebuildap's output
        # (and would misreport a scoped run anyway); surfaced only if it fails.
        try:
            subprocess.run(
                quantize_command(script, ref_tmp, target_tmp),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise QuantizeError(
                f"quantize_labels failed (exit {e.returncode}):\n{e.stderr}"
            ) from e
        # quantize_labels snapped every boundary; when a region is selected, keep
        # only the in-window boundaries and revert the rest to their originals.
        orig_labels = _labels_from_txt(contents[target_name])
        quantized_labels = _labels_from_txt(target_tmp.read_text())
        if selection is None:
            final_labels = quantized_labels
        else:
            final_labels = _scope_to_selection(orig_labels, quantized_labels, selection)
        if verbose:
            # Our own summary, scoped to what was actually applied -- not
            # quantize_labels' whole-track figures, which overstate a scoped run.
            _report_applied_adjustment(orig_labels, final_labels, selection)
        # This canonical form is both the versioned .txt and, re-written to the
        # temp, the exact bytes imported -- so file and track cannot diverge.
        quantized_content = _format_track_txt(final_labels)
    finally:
        ref_tmp.unlink(missing_ok=True)
        target_tmp.unlink(missing_ok=True)
    # contents[...] is canonical too, so replace_label_track's plain compare tells
    # whether anything actually moved, and skips the swap entirely when it did not.
    changed = replace_label_track(
        target_index, target_name, quantized_content, contents[target_name]
    )
    return target_name, target_index, quantized_content, changed


def _write_temp_label_txt(content: str) -> Path:
    """Write label ``.txt`` content to a throwaway temp file, returning its path."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(content)
        return Path(tf.name)


def _labels_from_txt(content: str) -> List[Tuple[float, float, str]]:
    """Parse ``.txt`` label content into ``(start, end, text)`` tuples.

    Reads the three-field Audacity form quantize_labels emits (``start<TAB>end
    <TAB>text``, text possibly empty). Blank lines are skipped; a tab inside the
    text is preserved. Feeding the result back through :func:`_format_track_txt`
    re-canonicalizes it, so the versioned file matches the export format.
    """
    labels: List[Tuple[float, float, str]] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        start = float(parts[0])
        end = float(parts[1])
        text = "\t".join(parts[2:])
        labels.append((start, end, text))
    return labels


def _nyquist_write_selection_command(out_path: Path) -> str:
    """The raw ``NyquistPrompt`` command that writes the selection to ``out_path``."""
    ny = _NY_SELECTION_TEMPLATE.format(path=out_path)
    return f'NyquistPrompt: Command="{ny}" Version="3"'


def _parse_selection_file(path: Path) -> Tuple[float, float]:
    """Parse ``start\\nend\\n`` into ``(start, end)`` (ordered). Raises
    :class:`SelectionReadError` when absent or non-numeric (a real Nyquist
    failure, e.g. the accessor returning ``NIL``)."""
    text = path.read_text() if path.exists() else ""
    fields = text.split()
    if len(fields) != 2:
        raise SelectionReadError(
            f"Nyquist did not report the selection (got {text!r})."
        )
    try:
        start, end = float(fields[0]), float(fields[1])
    except ValueError as e:
        raise SelectionReadError(
            f"Nyquist selection output is not numeric (got {text!r})."
        ) from e
    return (start, end) if start <= end else (end, start)


def read_time_selection() -> Tuple[float, float]:
    """Return the current Audacity time selection ``(start, end)`` in seconds.

    mod-script-pipe cannot query the selection, so this runs a nil-returning
    Nyquist snippet that writes the bounds to a temp file (see
    :data:`_NY_SELECTION_TEMPLATE`). The pipe reports ``Failed!`` -- expected,
    the file write is the real output, so the exception is swallowed -- and the
    file is parsed. Raises :class:`SelectionReadError` if nothing parseable was
    written.
    """
    fd, name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    out_path = Path(name)
    try:
        try:
            _pa_do_timed(
                _nyquist_write_selection_command(out_path), _SELECTION_READ_TIMEOUT
            )
        except pa.PyAudacityException:
            pass  # nil-return => Failed!; the file write already happened
        return _parse_selection_file(out_path)
    finally:
        out_path.unlink(missing_ok=True)


# --- transpose a selected label track's chords (`rebuildap transpose`) -------
#
# Snap-free sibling of `quantize`: same spine (resolve the selected track,
# resolve the selection scope, swap the new content in), but the *text* changes
# and the
# times do not. The chord parsing lives in the sister `transpose` package, which
# knows nothing about Audacity or the label format -- this module owns the
# format, that one owns the question "is this token a chord".


def _transposed_labels(
    labels: List[Tuple[float, float, str]],
    semitones: int,
    prefer_flats: bool,
    selection: Optional[Tuple[float, float]],
) -> Tuple[List[Tuple[float, float, str]], List[str]]:
    """Transpose the in-scope labels' chords. Returns ``(labels, skipped)``.

    Scoping is per **label, by start position** -- deliberately unlike ``quantize``'s
    per-boundary rule, because a chord belongs to its onset and there is no half
    a label to transpose. ``selection`` of ``None`` means the whole track.

    ``skipped`` names the in-scope labels whose text held no chord at all, so the
    caller can report them; a label that is merely out of scope was never
    attempted and is not listed, and neither is an empty label. Times are never
    touched.

    ``prefer_flats`` is passed on explicitly rather than left to the library's
    default, which is the opposite (see decisions.md 2026-07-25 12:45).
    """
    sel_start, sel_end = selection if selection else (None, None)

    def in_scope(start: float) -> bool:
        return selection is None or _within_selection(start, sel_start, sel_end)

    out: List[Tuple[float, float, str]] = []
    skipped: List[str] = []
    for start, end, text in labels:
        if not in_scope(start):
            out.append((start, end, text))
            continue
        transposed = transpose_label_text(text, semitones, prefer_flats)
        if transposed is None:
            if text.strip():
                skipped.append(text)
            out.append((start, end, text))
        else:
            out.append((start, end, transposed))
    return out, skipped


def transpose_selected_label_track(
    semitones: int,
    prefer_flats: bool = True,
    verbose: bool = False,
    whole_track: bool = False,
) -> Tuple[str, int, str, bool, List[str]]:
    """Transpose the selected label track's chords, in place.

    Returns ``(target_name, target_index, content, changed, skipped)`` --
    the same shape ``quantize`` returns plus the list of in-scope labels that held no
    chord. ``content`` is the canonical ``.txt`` form for the caller to write as
    the versioned source of truth; it is also exactly what was imported, so file
    and track cannot diverge.

    Flats by default: the corpus this serves is flat-heavy, unlike the sister
    library's own sharps default. See :func:`_transposed_labels`.

    As with ``quantize``, the selection is resolved *before* the project is touched, a
    track whose content does not change is left entirely alone (no mtime bump,
    no undo churn), and every precondition failure is a :class:`LabelTrackError`.
    """
    tracks = get_tracks()
    target_index, target_name = resolve_selected_label_track(tracks)
    contents = get_label_tracks_content_via_getinfo()
    selection = resolve_selection_scope(whole_track)

    orig_labels = _labels_from_txt(contents[target_name])
    final_labels, skipped = _transposed_labels(
        orig_labels, semitones, prefer_flats, selection
    )
    content = _format_track_txt(final_labels)
    if verbose:
        _report_transposed_count(orig_labels, final_labels, skipped, selection)
    changed = replace_label_track(
        target_index, target_name, content, contents[target_name]
    )
    return target_name, target_index, content, changed, skipped


def _report_transposed_count(orig_labels, final_labels, skipped, selection) -> None:
    """Under ``-v``: how many labels actually changed, scoped to the selection."""
    moved = sum(1 for o, f in zip(orig_labels, final_labels) if o[2] != f[2])
    where = (
        f" in selection {selection[0]:.3f}-{selection[1]:.3f}"
        if selection
        else " (whole track)"
    )
    print(f"Transposed {moved} label(s){where}; {len(skipped)} held no chord.")


def _scope_to_selection(
    orig: List[Tuple[float, float, str]],
    quantized: List[Tuple[float, float, str]],
    selection: Tuple[float, float],
) -> List[Tuple[float, float, str]]:
    """Keep each *quantized* boundary only when its *original* position lay inside
    ``selection``; revert the rest to the original.

    Per boundary, not per label, so a label straddling a selection edge gets only
    its in-window boundary snapped. ``orig`` and ``quantized`` are index-aligned
    (quantize_labels preserves order and count). Text comes from ``orig``.
    """
    sel_start, sel_end = selection

    def inside(t: float) -> bool:
        return _within_selection(t, sel_start, sel_end)

    scoped: List[Tuple[float, float, str]] = []
    for (o_start, o_end, o_text), (q_start, q_end, _q_text) in zip(orig, quantized):
        scoped.append(
            (
                q_start if inside(o_start) else o_start,
                q_end if inside(o_end) else o_end,
                o_text,
            )
        )
    return scoped


def _applied_adjustment(
    orig: List[Tuple[float, float, str]], final: List[Tuple[float, float, str]]
) -> Tuple[int, float]:
    """Return ``(#boundaries moved, total absolute adjustment in seconds)`` between
    the original and the final (post-scoping) labels -- i.e. what was *applied*."""
    moved = 0
    total = 0.0
    for (o_start, o_end, _), (f_start, f_end, _) in zip(orig, final):
        for before, after in ((o_start, f_start), (o_end, f_end)):
            delta = abs(after - before)
            if delta > _ADJUSTMENT_EPSILON:
                moved += 1
                total += delta
    return moved, total


def _report_applied_adjustment(orig_labels, final_labels, selection) -> None:
    """Print the applied-adjustment summary to stderr (under ``-v``), scoped to the
    selection -- unlike quantize_labels' own whole-track figures."""
    moved, total = _applied_adjustment(orig_labels, final_labels)
    where = ""
    if selection is not None:
        where = f" in selection [{selection[0]:.3f}, {selection[1]:.3f}]"
    if moved == 0:
        print(f"Nothing to quantize{where}; already on the grid.", file=sys.stderr)
        return
    plural = "boundary" if moved == 1 else "boundaries"
    print(
        f"Quantized {moved} {plural}{where}: total adjustment {total:.6f}s, "
        f"average {total / moved:.6f}s.",
        file=sys.stderr,
    )


def import_audio(filename: Path):
    """
    Imports audio into Audacity.
    """
    abs_path = filename.expanduser().resolve()
    pa.import_audio(abs_path)


def project_already_open(filename: Path) -> bool:
    """True if opening ``filename`` would hit the "already open" alert.

    Checked *before* ``assert_audacity``, not just inside :func:`open_project`,
    so a project that is going to be skipped costs nothing: an already-open
    project has tracks, so it fails the empty-project probe and
    ``assert_audacity_window`` answers with a Cmd-N — leaving a stray empty
    window behind for every project a sweep skips.

    Two guards, both load-bearing:

    - **Only ``.aup3`` input.** Audio rebuilds into a *new* project and can
      never raise the alert, yet it shares its stem with the project it builds
      (``angie.opus`` -> ``angie.aup3``), so a stem check alone would refuse to
      rebuild whenever the old project happened to be open.
    - **Only when Audacity is running.** Listing the windows of a process that
      does not exist is an osascript *error*, which ``audacity_window_names``
      duly reports on stderr — so asking unconditionally would put a spurious
      failure in front of the user on every cold start. A stopped Audacity
      also cannot have anything open, so there is nothing to learn.
    """
    from . import audacity_present as ap

    if not is_audacity_project(filename):
        return False
    if not ap.is_audacity_running():
        return False
    return ap.project_window_open(filename.expanduser().resolve().stem)


def assert_not_already_open(filename: Path) -> None:
    """Raise :class:`ProjectAlreadyOpenError` if the project is already open."""
    if not project_already_open(filename):
        return
    name = filename.expanduser().resolve().name
    raise ProjectAlreadyOpenError(
        f'"{name}" is already open in another Audacity window. Opening it '
        "again would raise a modal alert that blocks the scripting pipe, so "
        "it was left alone. Close that window (or save and close it, if it "
        "has unsaved edits) and run again."
    )


def open_project(
    filename: Path,
    retries: int = 1,
    retry_delay: float = 0.3,
    per_attempt_timeout: float = 5.0,
    verbose: bool = False,
):
    """
    Opens the Audacity project given by filename.

    One retry on ``BatchCommand finished: Failed!`` (recently-closed project
    still mid-unload). Bails immediately on ``TimeoutError`` since a wedged
    pipe won't recover from retrying.

    A project saved by an older Audacity raises a modal "project needs
    updating" dialog that would otherwise hold this command open until it times
    out, so a watcher acknowledges that one dialog while the open is in flight.
    See ``audacity_present.dismissing_upgrade_dialog``.

    A project that is *already open* raises a different modal alert, which is
    refused rather than dismissed — see ``ProjectAlreadyOpenError`` and
    ``audacity_present.project_window_open``.
    """
    # Imported here rather than at module scope: audacity_present imports this
    # module, so a top-level import would be circular.
    from . import audacity_present as ap

    abs_path = filename.expanduser().resolve()

    # Before anything touches the pipe: sending the command is what raises the
    # "already open in another window" alert, and that alert then blocks the
    # command until acknowledged. Callers check this earlier too, to avoid
    # starting Audacity at all for a project they will skip; this one is the
    # backstop for callers that don't.
    assert_not_already_open(abs_path)

    cmd = f'OpenProject2: Filename="{abs_path}"'
    # No-op ping first — fail fast if pipe is wedged, rather than waiting out
    # the per-attempt timeout on an expensive command that would modify state.
    ping_pipe()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with ap.dismissing_upgrade_dialog(verbose):
                _pa_do_timed(cmd, per_attempt_timeout)
            return
        except pa.PyAudacityException as e:
            last_err = e
            if attempt < retries:
                time.sleep(retry_delay)
        except TimeoutError as e:
            # The check above can be raced — the user can open the project in
            # the moment between it and this command — and that lands here,
            # looking exactly like a wedged pipe. Name the likely cause.
            raise TimeoutError(
                f'{e} This can mean "{abs_path.name}" is already open in '
                "another Audacity window, which raises a modal alert that "
                "blocks the open until it is acknowledged."
            ) from e
    raise last_err


def is_audacity_project(filename: Path) -> bool:
    """
    Returns true if the given filename represents an audacity project.
    Tested
    """
    return filename.name.lower().endswith(f".{AUDACITY_EXTENSION}")


def aup3_path_for(audio_path: Path) -> Path:
    """Where the .aup3 for ``audio_path`` belongs: beside the audio, same stem.

    Derived from the audio file, never from the cwd, so running rebuildap from
    another directory still writes into the project.
    """
    abs_path = audio_path.expanduser().resolve()
    return abs_path.parent / f"{abs_path.stem}.{AUDACITY_EXTENSION}"


def save_project_if_absent(audio_path: Path, verbose: bool = False) -> Path | None:
    """Save the rebuilt project next to its audio, unless an .aup3 already exists.

    Returns the path written, or None if one was already there.

    Never overwrites: an existing .aup3 is the user's working copy and may hold
    edits that are not in the label files. (``pa.save`` would not merely
    overwrite it — it unlinks the target first to dodge Audacity's confirm
    dialog — so guarding here matters.)
    """
    target = aup3_path_for(audio_path)
    if target.exists():
        if verbose:
            print(f"{target.name} already exists; not overwriting.")
        return None
    pa.save(str(target), add_to_history=False, allow_overwrite=False)
    if verbose:
        print(f"Saved {target}")
    return target


def touch_label_files(audio_path: Path, reference: Path, verbose: bool = False) -> None:
    """Mark the label files as at least as new as ``reference`` (the saved .aup3).

    ``check`` treats any label file older than the .aup3 as possibly stale and
    re-exports it to diff. Straight after a rebuild the two provably agree — the
    project was just built from those very files — so without this every later
    ``check`` would open, export and diff the project forever, finding nothing.
    Check mode only rewrites label files when content diverges, so the condition
    never clears on its own.
    """
    stamp = reference.stat().st_mtime
    for label_file in create_labels_glob(audio_path):
        if label_file.stat().st_mtime < stamp:
            os.utime(label_file, (stamp, stamp))
            if verbose:
                print(f"Touched {label_file.name} to match {reference.name}")


def create_labels_glob(filename: Path) -> Generator[Path]:
    """
    Finds all label files associated with the audio
    file give by name.
    """
    abs_path = filename.expanduser().resolve()
    return abs_path.parent.glob(f"*_{abs_path.stem}.txt")


def reorder_labels(paths: Iterable[Path]) -> List[Path]:
    """
    Reorders given label files by this order:
       1. part
       2. chord
       3. lyric

       4. unrecognized

       5. bar (second-to-last)
       6. beat (last)
    """

    def get_priority(path: Path):
        # Recognized labels first
        for i, substring in enumerate(LABEL_PRIORITY_ORDER):
            if substring in path.name:
                return i
        # bar before beat
        if LABEL_BAR in path.name:
            return len(LABEL_PRIORITY_ORDER) + 1  # bar come just before beat
        # beat come last
        if LABEL_BEAT in path.name:
            return len(LABEL_PRIORITY_ORDER) + 2  # beat come after bar
        # Unrecognized labels come just before bars and beat
        return len(
            LABEL_PRIORITY_ORDER
        )  # Unrecognized labels go between recognized and bar/beat

    # Sort filenames using the modified priority
    return sorted(paths, key=get_priority)


def open_audio(filename: Path, verbose=False):
    """
    Opens the audio file given by name.
    If it's an audacity project, simply opens it.
    If it's any other format, imports it and any
    labels associated with it.
    """
    if is_audacity_project(filename):
        if verbose:
            print(f'Opening "{filename}"')
        open_project(filename, verbose=verbose)
        if verbose:
            print(f'Done opening "{filename}"')
    else:
        if verbose:
            print(f'Importing "{filename}"')
        import_audio(filename)
        if verbose:
            print(f'Done importing "{filename}"')
        abs_path = filename.expanduser().resolve()
        label_files = reorder_labels(create_labels_glob(filename))
        for lfile in label_files:
            lblname = lfile.stem.replace(f"_{abs_path.stem}", "")
            if verbose:
                print(f"labels: >{lblname}<")
            make_label_track_from_file(lfile, lblname)


def main():
    print("This main is just for testing purposes.")


if __name__ == "__main__":
    main()
