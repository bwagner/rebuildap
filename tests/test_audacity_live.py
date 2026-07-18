"""Tests that drive a real, running Audacity.

Marked ``audacity`` and deselected by default (see ``addopts`` in
``pyproject.toml``), so a plain ``uv run pytest`` stays offline. Run them with:

    uv run pytest -m audacity

Requirements, none of which CI can provide: macOS with an unlocked GUI login
session, Audacity running with ``mod-script-pipe`` enabled, and Accessibility
permission granted to the calling terminal.

History: these lived in the repo-root ``test.py`` and had never actually run --
the filename did not match pytest's ``test_*.py`` glob, so nothing collected
them. Every function they reference still exists, but treat failures here as
un-vetted rather than as regressions.
"""

import random
import time

import pyaudacity as pa
import pytest

from rebuildap import audacity_funcs as af
from rebuildap import audacity_present as ap

pytestmark = pytest.mark.audacity

AUDIO_TRACK_1_NAME = "First Audio Track"
AUDIO_TRACK_2_NAME = "Second Audio Track"

LABEL_TRACK_1_NAME = "First Label Track"
LABEL_TRACK_2_NAME = "Second Label Track"

SLEEP_BETWEEN_TESTS = 0.3

NOISE_START = "1"
NOISE_END = "3"
NOISE_TYPE = "Brownian"
NOISE_AMPLITUDE = "0.8"


def create_audio_track(track_name: str = "Audio Track"):
    pa.do("NewMonoTrack")
    pa.do(f'SelectTime: Start="{NOISE_START}" End="{NOISE_END}"')
    pa.do(f'Noise: Type="{NOISE_TYPE}" Amplitude="{NOISE_AMPLITUDE}"')
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
        af.make_label_track(LABEL_TRACK_1_NAME)  # 0
        assert af.get_track_count() == i
        i += 1
        create_audio_track(AUDIO_TRACK_1_NAME)  # 1
        assert af.get_track_count() == i
        i += 1
        af.make_label_track(LABEL_TRACK_2_NAME)  # 2
        assert af.get_track_count() == i
        i += 1
        create_audio_track(AUDIO_TRACK_2_NAME)  # 3
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
    # Deliberately does NOT close the window. The only window this fixture can
    # have created is an empty project, and Audacity titles *every* empty
    # project "Audacity" -- so close_owned_window cannot prove ownership and
    # would refuse anyway. A bare Cmd-W is forbidden (see CLAUDE.md): it closes
    # whatever is frontmost, which may be the user's own project. Leaking a
    # window is the cheaper failure.


@pytest.fixture(autouse=True)
def sleep_between_tests(request):
    if SLEEP_BETWEEN_TESTS > 0:
        print(
            f"\nSleeping for {SLEEP_BETWEEN_TESTS} seconds before running the next test..."
        )
        time.sleep(SLEEP_BETWEEN_TESTS)


def test_empty():
    assert af.is_project_empty()


def test_track_count0():
    assert af.get_track_count() == 0


def test_audio_track(audio_track):
    tracks = af.get_tracks()
    assert len(tracks) == 1
    track = tracks[0]
    assert af.is_track_focused(track)
    assert af.is_track_selected(track)
    assert af.is_audio_track(track)
    assert af.get_track_start(track) == int(NOISE_START)
    assert af.get_track_end(track) == int(NOISE_END)
    assert af.get_track_pan(track) == 0
    assert af.get_track_volume(track) == 1
    assert af.get_track_channels(track) == 1
    assert not af.is_track_solo(track)
    assert not af.is_track_muted(track)


def test_make_label(undo):
    label = "the label title"
    af.make_label_track(label)
    tracks = af.get_tracks()
    assert len(tracks) == 1
    assert af.get_track_name(tracks[0]) == label


def test_select_first_audio(four_tracks):
    af.select_first_audio_track()
    tracks = af.get_selected_tracks()
    assert len(tracks) == 1
    assert af.get_track_name(tracks[0]) == AUDIO_TRACK_1_NAME


def test_select_all_audio(four_tracks):
    tracks = af.get_audio_tracks()
    assert len(tracks) == 2
    assert af.get_track_name(tracks[0]) == AUDIO_TRACK_1_NAME
    assert af.get_track_name(tracks[1]) == AUDIO_TRACK_2_NAME
    assert af.get_audio_track_indices() == [1, 3]


def test_select_all_label(four_tracks):
    tracks = af.get_label_tracks()
    assert len(tracks) == 2
    assert af.get_track_name(tracks[0]) == LABEL_TRACK_1_NAME
    assert af.get_track_name(tracks[1]) == LABEL_TRACK_2_NAME
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
    assert af.get_track_name(tracks[0]) == LABEL_TRACK_1_NAME
    assert af.get_track_name(tracks[1]) == LABEL_TRACK_2_NAME

    for i in range(3):
        af.undo()


def test_select(four_tracks):
    af.select_label_tracks()
    assert af.get_selected_track_indices() == [0, 2]

    af.select_audio_tracks()
    assert af.get_selected_track_indices() == [1, 3]


def test_undo_redo(four_tracks):
    af.undo()
    assert af.get_track_count() == 3
    af.redo()
    assert af.get_track_count() == 4


def test_mute(four_tracks):
    af.mute_track(1)
    muted_tracks = af.get_muted_tracks()
    assert len(muted_tracks) == 1
    assert af.get_track_name(muted_tracks[0]) == AUDIO_TRACK_1_NAME


def test_solo(four_tracks):
    af.solo_track(3)
    solo_tracks = af.get_solo_tracks()
    assert len(solo_tracks) == 1
    assert af.get_track_name(solo_tracks[0]) == AUDIO_TRACK_2_NAME


def test_unsolo(four_tracks):
    af.solo_tracks([1, 3])
    solo_tracks = af.get_solo_tracks()
    assert len(solo_tracks) == 2
    af.unsolo_track(1)
    solo_tracks = af.get_solo_tracks()
    assert len(solo_tracks) == 1


def test_unsolo_tracks(four_tracks):
    tracks = [1, 3]
    af.solo_tracks(tracks)
    solo_tracks = af.get_solo_tracks()
    assert len(solo_tracks) == 2
    af.unsolo_tracks(tracks)
    solo_tracks = af.get_solo_tracks()
    assert len(solo_tracks) == 0


def test_get_solo_track_indices(four_tracks):
    tracks = [1, 3]
    af.solo_tracks(tracks)
    assert af.get_solo_track_indices() == tracks


def test_get_muted_track_indices(four_tracks):
    tracks = [1, 3]
    af.mute_tracks(tracks)
    assert af.get_muted_track_indices() == tracks


def test_unmute_track(four_tracks):
    tracks = [1, 3]
    af.mute_tracks(tracks)
    assert af.get_muted_track_indices() == tracks
    af.unmute_track(1)
    assert af.get_muted_track_indices() == [3]


def test_unmute_tracks(four_tracks):
    tracks = [1, 3]
    af.mute_tracks(tracks)
    assert af.get_muted_track_indices() == tracks
    af.unmute_tracks([1])
    assert af.get_muted_track_indices() == [3]


def test_get_selected_label_track_indices(four_tracks_sel):
    tracks = af.get_selected_label_track_indices()
    assert len(tracks) == 2


def test_get_selected_audio_track_indices(four_tracks_sel):
    tracks = af.get_selected_audio_track_indices()
    assert len(tracks) == 1


def test_select_track(four_tracks):
    track = 2

    af.select_track(track)
    assert af.get_selected_track_indices() == [track]


def test_focus_track(four_tracks):
    track = 2

    af.focus_track(track)
    assert af.get_focused_track_index() == track


def test_focus_track2(four_tracks):
    track = 2

    af.focus_track(track)
    assert len(af.get_focused_tracks()) == 1
    assert af.get_track_name(af.get_focused_tracks()[0]) == LABEL_TRACK_2_NAME


def test_focus(four_tracks):
    create_audio_track()
    create_audio_track()
    create_audio_track()
    for i in range(af.get_track_count()):
        af.focus_track(i)
        assert af.get_focused_track_index() == i
    for _ in range(af.get_track_count()):
        j = random.randrange(af.get_track_count())
        af.focus_track(j)
        assert af.get_focused_track_index() == j
