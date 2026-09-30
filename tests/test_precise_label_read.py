"""Offline tests for choosing between the ``.aup3``'s exact label times and GetInfo's.

The ``.aup3`` read is preferred, but only when it agrees with what Audacity reports
live: every exact time, rounded the way ``GetInfo`` rounds (six significant
digits), must equal GetInfo's value, and names, order, counts and texts must match.
Disagreement means the file is stale, is the wrong file, or was misparsed; then
GetInfo's rounded times are used and a note on stderr says so and why -- stderr,
because the hotkey alerts on a successful run that wrote there.
"""

import json
from pathlib import Path

import pytest

from rebuildap import audacity_funcs as af
from rebuildap import aup3_labels

FIXTURES = Path(__file__).parent / "fixtures"
SAVED = FIXTURES / "label_tracks.aup3"

# What GetInfo displayed for the fixture's tracks (observed 2026-09-30), and what
# the file holds for them.
GETINFO_TRACKS = [
    (
        "probe",
        [
            (68.0001, 68.0001, "L2-nudge"),
            (69.0001, 69.0001, "L1-edited"),
            (100.689, 100.689, "L2-after-save"),
        ],
    ),
    (
        "digits",
        [
            (1.234e-07, 1.234e-07, "tiny"),
            (1e-06, 1e-06, "small"),
            (100.689, 100.689, "seventeen_digits"),
            (100.689, 100.689, "six_decimals"),
        ],
    ),
    ("m3", [(5.0, 5.0, "m3a"), (6.0, 6.0, "m3b")]),
]
FILE_TRACKS = aup3_labels.read_label_tracks(SAVED)


def _with(tracks, name, labels):
    return [(n, labels if n == name else ls) for n, ls in tracks]


# --- the cross-check ----------------------------------------------------------


def test_exact_times_that_round_to_getinfos_agree():
    assert af.cross_check_label_tracks(FILE_TRACKS, GETINFO_TRACKS) is None


def test_a_time_off_by_more_than_getinfos_rounding_disagrees():
    """A stale file: the track moved in Audacity since the file was written."""
    stale = _with(FILE_TRACKS, "m3", [(5.0, 5.0, "m3a"), (6.002, 6.002, "m3b")])
    reason = af.cross_check_label_tracks(stale, GETINFO_TRACKS)
    assert reason is not None
    assert "m3" in reason


def test_a_different_text_disagrees():
    renamed = _with(FILE_TRACKS, "m3", [(5.0, 5.0, "m3a"), (6.0, 6.0, "other")])
    assert "m3" in af.cross_check_label_tracks(renamed, GETINFO_TRACKS)


def test_a_different_label_count_disagrees():
    fewer = _with(FILE_TRACKS, "m3", [(5.0, 5.0, "m3a")])
    assert "m3" in af.cross_check_label_tracks(fewer, GETINFO_TRACKS)


def test_a_missing_track_disagrees():
    """The shape a scripted import leaves behind until the next undo step."""
    assert af.cross_check_label_tracks(FILE_TRACKS[:-1], GETINFO_TRACKS) is not None


def test_the_same_tracks_in_another_order_disagree():
    """Identical labels under swapped names, so only the order can tell them apart
    - the case where the caller would read one track's times for the other."""
    labels = FILE_TRACKS[2][1]
    live_labels = GETINFO_TRACKS[2][1]
    file_order = [("chords", labels), ("parts", labels)]
    live_order = [("parts", live_labels), ("chords", live_labels)]
    assert af.cross_check_label_tracks(file_order, live_order) is not None


def test_a_duplicated_track_name_disagrees_even_when_everything_matches():
    """By name is how the caller looks tracks up, so a repeated name is ambiguous."""
    twice = [("m3", FILE_TRACKS[2][1]), FILE_TRACKS[2]]
    live = [("m3", GETINFO_TRACKS[2][1]), GETINFO_TRACKS[2]]
    reason = af.cross_check_label_tracks(twice, live)
    assert reason is not None
    assert "m3" in reason


# --- reading GetInfo in track order -------------------------------------------


def _fake_pipe(monkeypatch, tracks, labels):
    responses = {
        "GetInfo: Type=Tracks": json.dumps(tracks),
        "GetInfo: Type=Labels": json.dumps(labels),
    }
    monkeypatch.setattr(
        af.pa, "do", lambda cmd: responses[cmd] + "\nBatchCommand finished: OK\n"
    )


def test_getinfo_label_tracks_come_in_track_order_with_repeated_names_kept(
    monkeypatch,
):
    _fake_pipe(
        monkeypatch,
        [
            {"name": "song", "kind": "wave"},
            {"name": "chords", "kind": "label"},
            {"name": "chords", "kind": "label"},
        ],
        [[2, [[2, 3, "b"]]], [1, [[0, 1.5, "a"]]]],
    )
    assert af.get_label_tracks_via_getinfo() == [
        ("chords", [(0.0, 1.5, "a")]),
        ("chords", [(2.0, 3.0, "b")]),
    ]


# --- choosing the source ----------------------------------------------------------


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(af, "get_label_tracks_via_getinfo", lambda: GETINFO_TRACKS)


def test_the_exact_times_are_used_when_they_agree_and_nothing_is_said(live, capsys):
    assert af.read_label_tracks_precisely(SAVED) == (dict(FILE_TRACKS), True)
    assert capsys.readouterr().err == ""


def test_disagreement_falls_back_to_getinfo_and_says_why(live, monkeypatch, capsys):
    stale = _with(FILE_TRACKS, "m3", [(5.0, 5.0, "m3a")])
    monkeypatch.setattr(af.aup3_labels, "read_label_tracks", lambda path: stale)

    assert af.read_label_tracks_precisely(SAVED) == (dict(GETINFO_TRACKS), False)

    err = capsys.readouterr().err
    assert "1 ms" in err
    assert "m3" in err


def test_an_unreadable_file_falls_back_to_getinfo_and_says_why(live, tmp_path, capsys):
    missing = tmp_path / "gone.aup3"

    assert af.read_label_tracks_precisely(missing) == (dict(GETINFO_TRACKS), False)

    err = capsys.readouterr().err
    assert "1 ms" in err
    assert "gone.aup3" in err


def test_no_known_project_file_falls_back_to_getinfo_and_says_so(live, capsys):
    assert af.read_label_tracks_precisely(None) == (dict(GETINFO_TRACKS), False)
    assert "1 ms" in capsys.readouterr().err


def test_the_fallback_is_one_line(live, capsys):
    """The hotkey shows stderr in an alert of limited length."""
    af.read_label_tracks_precisely(None)
    assert capsys.readouterr().err.count("\n") == 1
