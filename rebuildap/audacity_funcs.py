#!/usr/bin/env python

import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, Iterable, List, Optional

import pyaudacity as pa
import pyperclip


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


def make_label_track_from_file(label_file: Path, label_track_name: str = None):
    """
    Makes a new label track from the given file and names the label track according to the given name.
    """

    label_track_name = (
        label_track_name
        if label_track_name
        else re.sub(r"_?label_?", "", label_file.stem)
    )
    abs_path = label_file.expanduser().resolve()

    with save_selection():
        select_first_audio_track()  # needed for nyquist
        pa.do("SelTrackStartToEnd:")  # needed for nyquist
        pa.do(f'ImportLabels: fname="{abs_path}"')
        pa.do(f"SelectTracks: Track={get_track_count() - 1} Mode={SELECT_MODE_SET}")
        pa.do(f'SetTrack: Name="{label_track_name}"')


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
# `ExportLabels:`. The precision trade-off is documented in README.

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


def _resolve_output_context(aup3_path=None):
    """Return (out_dir: Path, aup3_stem: str).

    When ``aup3_path`` is given, derive both from it. Otherwise fall back to
    cwd + the name of the first wave track in the open project (conventional
    project-dir workflow).
    """
    if aup3_path is not None:
        p = Path(aup3_path)
        return p.parent, p.stem
    tracks = _parse_tracks_response(pa.do("GetInfo: Type=Tracks"))
    wave = next((t for t in tracks if t.get("kind") == "wave"), None)
    if wave is None:
        raise RuntimeError("Cannot derive output context: no wave track in project")
    return Path.cwd(), wave["name"]


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


def _write_via_getinfo(indices: Iterable[int], aup3_path=None) -> List[Path]:
    """Shared core: write one .txt per given label-track index. Returns written paths."""
    out_dir, stem = _resolve_output_context(aup3_path)
    labels_by_idx = _parse_labels_response(pa.do("GetInfo: Type=Labels"))
    names_by_idx = _label_track_names_by_idx(
        _parse_tracks_response(pa.do("GetInfo: Type=Tracks"))
    )
    written: List[Path] = []
    wanted = set(indices)
    for idx, labels in labels_by_idx.items():
        if idx not in wanted:
            continue
        name = names_by_idx.get(idx)
        if name is None:
            continue
        out_path = out_dir / _derive_label_filename(name, stem)
        out_path.write_text(_format_track_txt(labels))
        written.append(out_path)
    return written


def export_label_tracks_via_getinfo(aup3_path=None) -> List[Path]:
    """Export every label track, one file per track."""
    return _write_via_getinfo(get_label_track_indices(), aup3_path)


def export_selected_label_tracks_via_getinfo(aup3_path=None) -> List[Path]:
    """Export the selected label tracks, one file per track."""
    return _write_via_getinfo(get_selected_label_track_indices(), aup3_path)


def export_selected_or_all_label_tracks_via_getinfo(aup3_path=None) -> List[Path]:
    """Export the selected label tracks, or all of them when none is selected."""
    indices = get_selected_label_track_indices() or get_label_track_indices()
    return _write_via_getinfo(indices, aup3_path)


def import_audio(filename: Path):
    """
    Imports audio into Audacity.
    """
    abs_path = filename.expanduser().resolve()
    pa.import_audio(abs_path)


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
    """
    # Imported here rather than at module scope: audacity_present imports this
    # module, so a top-level import would be circular.
    from . import audacity_present as ap

    abs_path = filename.expanduser().resolve()
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

    ``-c`` treats any label file older than the .aup3 as possibly stale and
    re-exports it to diff. Straight after a rebuild the two provably agree — the
    project was just built from those very files — so without this every later
    ``-c`` would open, export and diff the project forever, finding nothing.
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
