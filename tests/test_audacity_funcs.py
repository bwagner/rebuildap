"""Tests for the pure helpers in ``audacity_funcs`` that need no live Audacity.

These were stranded in the repo-root ``test.py``, which pytest never collected
because its name does not match the ``test_*.py`` glob. They exercise plain
functions over paths, so they belong with the rest of the offline suite; the
tests that actually drive Audacity live in ``test_audacity_live.py``.
"""

from pathlib import Path

import pytest

from rebuildap import audacity_funcs as af


@pytest.mark.parametrize(
    "identifiers, expected",
    [
        # Basic ordering with one match per identifier
        (
            ["part_001", "chord_A", "lyrics_01", "bar_10", "beat_100"],
            ["part_001", "chord_A", "lyrics_01", "bar_10", "beat_100"],
        ),
        # No matches, so unrecognized items go before bars and beats
        (
            ["alpha", "beta", "gamma", "bar_10", "beat_100"],
            ["alpha", "beta", "gamma", "bar_10", "beat_100"],
        ),
        # Bars and beats go last even when others are recognized
        (
            ["chord_A", "lyrics_01", "random", "beat_100", "bar_10"],
            ["chord_A", "lyrics_01", "random", "bar_10", "beat_100"],
        ),
        # Unrecognized still sorts before bars and beats
        (
            ["part_001", "random_label", "beat_100", "bar_10"],
            ["part_001", "random_label", "bar_10", "beat_100"],
        ),
        # No recognized labels at all, bars and beats still last
        (
            ["random_01", "random_02", "bar_10", "beat_100"],
            ["random_01", "random_02", "bar_10", "beat_100"],
        ),
        # Only bars and beats, bar before beat
        (["beat_100", "bar_10"], ["bar_10", "beat_100"]),
        # Mixed recognized and unrecognized
        (
            ["beat_100", "part_002", "chord_B", "random_label", "bar_10"],
            ["part_002", "chord_B", "random_label", "bar_10", "beat_100"],
        ),
    ],
)
def test_reorder_labels(identifiers, expected):
    assert af.reorder_labels([Path(p) for p in identifiers]) == [
        Path(p) for p in expected
    ]


def test_is_audacity_project():
    assert af.is_audacity_project(Path("bla.aup3"))


def test_is_not_audacity_project():
    assert not af.is_audacity_project(Path("bla.mp3"))
