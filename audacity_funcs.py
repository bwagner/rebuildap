#!/usr/bin/env python

import json
from pathlib import Path

import pyaudacity as pa
import typer

"""
audacity_funcs.py


"""

# when mod-script-pipe worked out fine:
RESPONSE_OK = "\nBatchCommand finshed: OK\n"


def is_project_empty():
    return get_track_count() == 0


def get_tracks():
    return json.loads(pa.get_info("Tracks", "JSON")[: -len(RESPONSE_OK)])


def get_track_count():
    return len(get_tracks())


def make_label_track(label_file, label_track_name):
    pa.do("SelectTracks: Track=0 Mode=Set")
    pa.do(f"ImportLabels: fname={label_file}")
    pa.do(f"SelectTracks: Track={get_track_count() - 1} Mode=Set")
    pa.do(f"SetTrack: Name={label_track_name}")


def get_focused_tracks():
    return [track for track in get_tracks() if track["focused"]]


def get_selected_tracks():
    return [track for track in get_tracks() if track["selected"]]


def import_audio(filename):
    abs_path = Path(filename).expanduser().resolve()
    pa.import_audio(abs_path)


def main():
    print("This main is just for testing purposes.")


if __name__ == "__main__":
    typer.run(main)
