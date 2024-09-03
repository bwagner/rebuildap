#!/usr/bin/env python
import pyaudacity as pa
import pytest

import audacity_funcs as af
import audacity_present as ap

AUDIO_TRACK_1_NAME = "First Audio Track"
AUDIO_TRACK_2_NAME = "Second Audio Track"

LABEL_TRACK_1_NAME = "First Label Track"
LABEL_TRACK_2_NAME = "Second Label Track"


def create_audio_track(track_name: str = "Audio Track"):
    pa.do("NewMonoTrack")
    pa.do('SelectTime: Start="1" End="3"')
    pa.do('Noise: Type="Brownian" Amplitude="0.8"')
    pa.do(f'SetTrack: Name="{track_name}"')


@pytest.fixture(scope="function", autouse=False)
def undo():
    try:
        # nothing to do here
        yield
    finally:
        af.undo()


@pytest.fixture(scope="function", autouse=False)
def audio_track(undo):
    create_audio_track()
    yield


@pytest.fixture(scope="function", autouse=False)
def four_tracks():
    try:
        i = 0
        assert af.get_track_count() == i
        i += 1
        af.make_label_track(LABEL_TRACK_1_NAME)
        assert af.get_track_count() == i
        i += 1
        create_audio_track(AUDIO_TRACK_1_NAME)
        assert af.get_track_count() == i
        i += 1
        af.make_label_track(LABEL_TRACK_2_NAME)
        assert af.get_track_count() == i
        i += 1
        create_audio_track(AUDIO_TRACK_2_NAME)
        assert af.get_track_count() == i
        i += 1
        yield
    finally:
        for i in range(af.get_track_count()):
            af.undo()


@pytest.fixture(scope="function", autouse=False)
def four_tracks_sel(four_tracks):
    af.select_tracks([0, 1, 2])


@pytest.fixture(scope="module", autouse=True)
def setup():
    ap.assert_audacity(False)
    yield
    ap.close_audacity_window_as()


def test_empty():
    assert af.is_project_empty()


def test_track_count0():
    assert af.get_track_count() == 0


def test_audio_track(audio_track):
    tracks = af.get_tracks()
    assert len(tracks) == 1
    track = tracks[0]
    assert track["focused"] == 1
    assert track["selected"] == 1
    assert track["kind"] == "wave"
    assert track["start"] == 1
    assert track["end"] == 3
    assert track["pan"] == 0
    assert track["gain"] == 1
    assert track["channels"] == 1
    assert track["solo"] == 0
    assert track["mute"] == 0


def test_make_label(undo):
    label = "the label title"
    af.make_label_track(label)
    tracks = af.get_tracks()
    assert len(tracks) == 1


def test_select_first_audio(four_tracks):
    af.select_first_audio_track()
    tracks = af.get_selected_tracks()
    assert len(tracks) == 1
    track = tracks[0]
    assert track["name"] == AUDIO_TRACK_1_NAME


def test_select_all_audio(four_tracks):
    tracks = af.get_audio_tracks()
    assert len(tracks) == 2
    track = tracks[0]
    assert track["name"] == AUDIO_TRACK_1_NAME
    track = tracks[1]
    assert track["name"] == AUDIO_TRACK_2_NAME
    assert af.get_audio_track_indices() == [1, 3]


def test_select_all_label(four_tracks):
    tracks = af.get_label_tracks()
    assert len(tracks) == 2
    track = tracks[0]
    assert track["name"] == LABEL_TRACK_1_NAME
    track = tracks[1]
    assert track["name"] == LABEL_TRACK_2_NAME
    assert af.get_label_track_indices() == [0, 2]


def test_select_label_track_list(four_tracks_sel):
    assert af.get_selected_track_indices() == [0, 1, 2]


def test_unselect_track(four_tracks_sel):
    af.unselect_track(1)
    assert af.get_selected_track_indices() == [0, 2]


def test_unselect_tracks(four_tracks_sel):
    af.unselect_tracks()
    assert not af.get_selected_track_indices()


def test_select_audio(four_tracks):
    af.select_audio_tracks()
    assert af.get_selected_track_indices() == [1, 3]
    assert len(af.get_selected_tracks()) == 2


def test_remove_sel_tracks(four_tracks):
    af.select_audio_tracks()
    af.remove_selected_tracks()
    tracks = af.get_tracks()
    assert len(tracks) == 2
    assert tracks[0]["name"] == LABEL_TRACK_1_NAME
    assert tracks[1]["name"] == LABEL_TRACK_2_NAME

    for i in range(3):
        af.undo()


def test_select(four_tracks):

    af.select_label_tracks()
    assert af.get_selected_track_indices() == [0, 2]

    af.select_audio_tracks()
    assert af.get_selected_track_indices() == [1, 3]


def test_mute(four_tracks):
    pass


def test_is_audacity_project():
    assert af.is_audacity_project("bla.aup3")


def test_is_not_audacity_project():
    assert not af.is_audacity_project("bla.mp3")


def main():
    pass


if __name__ == "__main__":
    main()
