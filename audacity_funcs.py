#!/usr/bin/env python

import glob
import json
from pathlib import Path
from typing import List

import pyaudacity as pa
import typer

"""
audacity_funcs.py

See
https://manual.audacityteam.org/man/scripting_reference.html

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


def is_project_empty():
    return get_track_count() == 0


def get_tracks():
    return json.loads(pa.get_info(GET_INFO_TRACKS, GET_INFO_JSON)[: -len(RESPONSE_OK)])


def get_track_count():
    return len(get_tracks())


def make_label_track(label_file, label_track_name):
    pa.do(f"SelectTracks: Track=0 Mode={SELECT_MODE_SET}")
    pa.do(f"ImportLabels: fname={label_file}")
    pa.do(f"SelectTracks: Track={get_track_count() - 1} Mode={SELECT_MODE_SET}")
    pa.do(f"SetTrack: Name={label_track_name}")


def get_tracks_by_property(prop: str):
    return [track for track in get_tracks() if track[prop]]


def get_focused_tracks():
    return get_tracks_by_property(PROPERTY_FOCUSED)


def get_selected_tracks():
    return get_tracks_by_property(PROPERTY_SELECTED)


def get_muted_tracks():
    return get_tracks_by_property(PROPERTY_MUTED)


def get_solo_tracks():
    return get_tracks_by_property(PROPERTY_SOLO)


def select_tracks(tracks: List[int]):
    """
    Track - first track to select, tracks are numbered starting from 0
    TrackCount - how many tracks to select
    Mode - either one of SELECT_MODE_SET, SELECT_MODE_ADD, SELECT_MODE_REMOVE
    """
    unselect_tracks()
    for track in tracks:
        pa.do(f"SelectTracks: Track={track} Mode={SELECT_MODE_ADD}")


def unselect_track(idx: int):
    """
    See select_tracks.
    """
    pa.do(f"SelectTracks: Track={idx} Mode={SELECT_MODE_REMOVE}")


def unselect_tracks():
    pa.do("SelectNone:")


def select_tracks_by_kind(kind: str):
    return select_tracks(get_track_indices_by_kind(kind))


def select_label_tracks():
    return select_tracks_by_kind(KIND_LABEL)


def select_audio_tracks():
    return select_tracks_by_kind(KIND_AUDIO)


def get_tracks_by_kind(kind: str):
    return [track for track in get_tracks() if track[PROPERTY_KIND] == kind]


def get_selected_label_track_indices():
    return get_selected_track_indices_by_kind(KIND_LABEL)


def get_selected_audio_track_indices():
    return get_selected_track_indices_by_kind(KIND_AUDIO)


def get_selected_track_indices_by_kind(kind):
    tracks_kind = get_track_indices_by_kind(kind)
    tracks_selected = get_selected_track_indices()
    return list(set(tracks_kind) & set(tracks_selected))


def get_selected_track_indices():
    return [i for i, track in enumerate(get_tracks()) if track[PROPERTY_SELECTED]]


def get_track_indices_by_kind(kind):
    return [i for i, track in enumerate(get_tracks()) if track[PROPERTY_KIND] == kind]


def get_audio_track_indices():
    return get_track_indices_by_kind(KIND_AUDIO)


def get_label_track_indices():
    return get_track_indices_by_kind(KIND_LABEL)


def get_label_tracks():
    return get_tracks_by_kind(KIND_LABEL)


def get_audio_tracks():
    return get_tracks_by_kind(KIND_AUDIO)


def remove_selected_tracks():
    """
    Removes selected tracks.
    """
    return pa.do("RemoveTracks:")


def undo():
    """
    Undo
    """
    return pa.do("Undo:")


def redo():
    """
    Redo
    """
    return pa.do("Redo:")


def export_labels():
    """
    Unavoidably interactive.
    """
    return pa.do("ExportLabels:")


def export_labels_list(labels: List[int]):
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
    label_track_indices = get_label_track_indices()
    for idx in label_track_indices:
        select_label_tracks()
        unselect_track(idx)
        remove_selected_tracks()
        export_labels()
        undo()


def import_audio(filename):
    abs_path = Path(filename).expanduser().resolve()
    pa.import_audio(abs_path)


def open_project(filename):
    abs_path = Path(filename).expanduser().resolve()
    pa.do(f"OpenProject2: Filename={abs_path}")


def is_audacity_project(filename: str):
    return filename.lower().endswith(f".{AUDACITY_EXTENSION}")


def create_labels_glob(filename: str):
    abs_path = Path(filename).expanduser().resolve()
    dirname = abs_path.parent
    return glob.glob(f"{dirname}/*_{abs_path.stem}.txt")


def open_audio(filename: str, verbose=False):
    if is_audacity_project(filename):
        if verbose:
            print(f"Opening {filename}")
        open_project(filename)
        if verbose:
            print(f"Done opening {filename}")
    else:
        if verbose:
            print(f"Importing {filename}")
        import_audio(filename)
        if verbose:
            print(f"Done importing {filename}")
        abs_path = Path(filename).expanduser().resolve()
        label_files = create_labels_glob(filename)
        for lfile in label_files:
            lblname = Path(lfile).stem.replace(f"_{abs_path.stem}", "")
            if verbose:
                print(f"labels: >{lblname}<")
            make_label_track(lfile, lblname)


def main():
    print("This main is just for testing purposes.")


if __name__ == "__main__":
    typer.run(main)
