#!/usr/bin/env python

import glob
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List

import pyaudacity as pa
import pyperclip
import typer

"""
audacity_funcs.py

References
[1] https://manual.audacityteam.org/man/scripting_reference.html

"""

AUDACITY_EXTENSION = "aup3"

# when mod-script-pipe worked out fine:
RESPONSE_OK = "\nBatchCommand finshed: OK\n"

KIND_LABEL = "label"
KIND_AUDIO = "wave"
PROPERTY_KIND = "kind"
PROPERTY_FOCUSED = "focused"
PROPERTY_SELECTED = "selected"
PROPERTY_MUTED = "mute"
PROPERTY_SOLO = "solo"

SELECT_MODE_SET = "Set"
SELECT_MODE_ADD = "Add"
SELECT_MODE_REMOVE = "Remove"

GET_INFO_TRACKS = "Tracks"
GET_INFO_JSON = "JSON"


@contextmanager
def temporary_clipboard():
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


def make_label_track_from_file(label_file: str, label_track_name: str):
    """
    Makes a new label track from the given file and names the label track according to the given name.
    """
    select_first_audio_track()  # needed for nyquist
    pa.do(f'ImportLabels: fname="{label_file}"')
    pa.do(f"SelectTracks: Track={get_track_count() - 1} Mode={SELECT_MODE_SET}")
    pa.do(f'SetTrack: Name="{label_track_name}"')


def make_label_track_01(label_file: str, label_track_name: str):
    pa.do("NewLabelTrack:")
    pa.do(f'SetTrack: Name="{label_track_name}"')
    count = 1
    with temporary_clipboard:
        with open(label_file) as f:
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


def get_focused_tracks():
    """
    Returns list of meta info dict for focused tracks.
    """
    return get_tracks_by_property(PROPERTY_FOCUSED)


def get_selected_tracks():
    """
    Returns list of meta info dict for selected tracks.
    Tested
    """
    return get_tracks_by_property(PROPERTY_SELECTED)


def get_muted_tracks():
    """
    Returns list of meta info dict for muted tracks.
    """
    return get_tracks_by_property(PROPERTY_MUTED)


def get_solo_tracks():
    """
    Returns list of meta info dict for solo tracks.
    """
    return get_tracks_by_property(PROPERTY_SOLO)


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
    """
    with save_selection():
        select_tracks(tracks)
        pa.do("MuteTracks:")


def unmute_track(track: int):
    """
    Unmutes the given track.
    """
    with save_selection():
        select_track(track)
        pa.do("UnmuteTracks:")


def unmute_tracks(track: List[int]):
    """
    Unmutes the given tracks.
    """
    with save_selection():
        select_tracks(track)
        pa.do("UnmuteTracks:")


def select_track(track: int):
    """
    Selects track
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


def get_tracks_by_kind(kind: str):
    """
    Returns list of track meta info for tracks of given kind.
    Tested (indirectly)
    """
    return [track for track in get_tracks() if track[PROPERTY_KIND] == kind]


def get_selected_label_track_indices() -> List[int]:
    """
    Returns list of selected label track indices.
    """
    return get_selected_track_indices_by_kind(KIND_LABEL)


def get_selected_audio_track_indices() -> List[int]:
    """
    Returns list of selected audio track indices.
    """
    return get_selected_track_indices_by_kind(KIND_AUDIO)


def get_selected_track_indices_by_kind(kind) -> List[int]:
    """
    Returns list of selected track indices by kind.
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


def export_labels():
    """
    Unavoidably interactive.
    """
    return pa.do("ExportLabels:")


def export_labels_list(labels: List[int]):
    """
    Interactive due to export_labels' interactivity

    Exports label tracks given by track number.
    """
    for idx in labels:
        select_label_tracks()
        unselect_track(idx)
        remove_selected_tracks()
        export_labels()
        undo()


def export_selected_label_tracks():
    """
    Interactive due to export_labels' interactivity

    Goes through the selected label tracks one by one:
        removes all other label tracks,
        exports label (interactively) undoes the removing of all label tracks,
    """
    export_labels_list(get_selected_label_track_indices())


def export_label_tracks():
    """
    Interactive due to export_labels' interactivity

    Goes through the label tracks one by one:
        removes all other label tracks,
        exports label (interactively) undoes the removing of all label tracks,
    """
    export_labels_list(get_label_track_indices())


def export_selected_or_all_label_tracks():
    """
    Interactive due to export_labels' interactivity

    Goes through the label tracks one by one:
        removes all other label tracks,
        exports label (interactively) undoes the removing of all label tracks,
    """
    label_track_indices = (
        get_selected_label_track_indices() or get_label_track_indices()
    )
    export_labels_list(label_track_indices)


def import_audio(filename: str):
    """
    Imports audio into Audacity.
    """
    abs_path = Path(filename).expanduser().resolve()
    pa.import_audio(abs_path)


def open_project(filename: str):
    """
    Opens the Audacity project given by filename.
    """
    abs_path = Path(filename).expanduser().resolve()
    pa.do(f'OpenProject2: Filename="{abs_path}"')


def is_audacity_project(filename: str) -> bool:
    """
    Returns true if the given filename represents an audacity project.
    Tested
    """
    return filename.lower().endswith(f".{AUDACITY_EXTENSION}")


def create_labels_glob(filename: str) -> List[str]:
    """
    Finds all label files associated with the audio
    file give by name.
    """
    abs_path = Path(filename).expanduser().resolve()
    dirname = abs_path.parent
    return glob.glob(f"{dirname}/*_{abs_path.stem}.txt")


def open_audio(filename: str, verbose=False):
    """
    Opens the audio file given by name.
    If it's an audacity project, simply opens it.
    If it's any other format, imports it and any
    labels associated with it.
    """
    if is_audacity_project(filename):
        if verbose:
            print(f'Opening "{filename}"')
        open_project(filename)
        if verbose:
            print(f'Done opening "{filename}"')
    else:
        if verbose:
            print(f'Importing "{filename}"')
        import_audio(filename)
        if verbose:
            print(f'Done importing "{filename}"')
        abs_path = Path(filename).expanduser().resolve()
        label_files = create_labels_glob(filename)
        for lfile in label_files:
            lblname = Path(lfile).stem.replace(f"_{abs_path.stem}", "")
            if verbose:
                print(f"labels: >{lblname}<")
            make_label_track_from_file(lfile, lblname)


def main():
    print("This main is just for testing purposes.")


if __name__ == "__main__":
    typer.run(main)
