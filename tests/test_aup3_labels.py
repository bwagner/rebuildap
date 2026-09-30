"""Offline tests for reading label tracks at full precision from an ``.aup3``.

``GetInfo: Type=Labels`` rounds label times to six significant digits (1 ms at
100 s), which let ``quantize`` write rounded beat times and call an off-grid track
"already quantized". The ``.aup3`` holds the exact doubles.

The fixtures hold real bytes written by Audacity 3.7.9, not an encoding of our
own - a hand-built blob would only agree with the parser's reading of the format.
They were cut from a throwaway project on 2026-09-30 (only the ``project`` /
``autosave`` rows; no audio):

- ``label_tracks.aup3``: a saved project, ``autosave`` empty.
- ``label_tracks_unsaved.aup3``: ``project`` holds an older save, ``autosave`` the
  newer state - the shape of a project with unsaved edits.

The expected times are the values that were *set* in Audacity (via ``SetLabel`` and
``ImportLabels``), not values read back with the parser under test.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from rebuildap import aup3_labels

FIXTURES = Path(__file__).parent / "fixtures"
SAVED = FIXTURES / "label_tracks.aup3"
UNSAVED = FIXTURES / "label_tracks_unsaved.aup3"

# What the saved project holds: three label tracks, the audio track left out.
SAVED_TRACKS = [
    (
        "probe",
        [
            (68.000123456789, 68.000123456789, "L2-nudge"),
            (69.000123456789, 69.000123456789, "L1-edited"),
            (100.68907563025209, 100.68907563025209, "L2-after-save"),
        ],
    ),
    (
        "digits",
        [
            (1.234e-07, 1.234e-07, "tiny"),
            (1e-06, 1e-06, "small"),
            (100.68907563025209, 100.68907563025209, "seventeen_digits"),
            (100.689076, 100.689076, "six_decimals"),
        ],
    ),
    ("m3", [(5.0, 5.0, "m3a"), (6.0, 6.0, "m3b")]),
]

# The older save inside the unsaved fixture: one track, times GetInfo showed as
# 2 and 2 although they differ by 1.2 microseconds.
OLDER_TRACKS = [
    (
        "probe",
        [
            (2.0, 2.0, "L2"),
            (2.00000123456789, 2.00000123456789, "L1-edited"),
            (100.68907563025209, 100.68907563025209, "L2-after-save"),
        ],
    ),
]


def _copy(src, tmp_path):
    dst = tmp_path / src.name
    shutil.copy2(src, dst)
    return dst


def test_reads_every_label_track_at_full_precision():
    assert aup3_labels.read_label_tracks(SAVED) == SAVED_TRACKS


def test_unsaved_edits_win_over_the_last_save():
    """``autosave`` is the live state; ``project`` is only the last Save."""
    assert aup3_labels.read_label_tracks(UNSAVED) == SAVED_TRACKS


def test_the_last_save_is_used_when_there_are_no_unsaved_edits(tmp_path):
    path = _copy(UNSAVED, tmp_path)
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM autosave")
    assert aup3_labels.read_label_tracks(path) == OLDER_TRACKS


def test_reading_needs_no_write_access(tmp_path):
    """Opened read-only: the file is the user's working copy."""
    path = _copy(SAVED, tmp_path)
    path.chmod(0o444)
    tmp_path.chmod(0o555)  # no sidecar files can be created either
    try:
        assert aup3_labels.read_label_tracks(path) == SAVED_TRACKS
    finally:
        tmp_path.chmod(0o755)


def test_the_connection_is_opened_read_only(monkeypatch):
    """Asserted on the connect call itself: SQLite silently falls back to read-only
    when it cannot write, so a read-write open passes every file-based test above
    and only differs on a live, writable working copy - the one case that matters."""
    uris = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        uris.append((database, kwargs.get("uri")))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(aup3_labels.sqlite3, "connect", spy)
    aup3_labels.read_label_tracks(SAVED)

    assert len(uris) == 1
    database, uri = uris[0]
    assert uri is True
    assert database.endswith("?mode=ro")


def test_reading_leaves_the_file_byte_identical(tmp_path):
    path = _copy(SAVED, tmp_path)
    before = path.read_bytes()
    aup3_labels.read_label_tracks(path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


def test_a_missing_file_is_a_read_error(tmp_path):
    with pytest.raises(aup3_labels.Aup3ReadError, match="nope.aup3"):
        aup3_labels.read_label_tracks(tmp_path / "nope.aup3")


def test_a_file_that_is_not_sqlite_is_a_read_error(tmp_path):
    path = tmp_path / "empty.aup3"
    path.write_bytes(b"")
    with pytest.raises(aup3_labels.Aup3ReadError):
        aup3_labels.read_label_tracks(path)


def test_a_project_with_no_saved_state_is_a_read_error(tmp_path):
    path = _copy(SAVED, tmp_path)
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM project")
    with pytest.raises(aup3_labels.Aup3ReadError, match="no project"):
        aup3_labels.read_label_tracks(path)


def _corrupt_doc(tmp_path, mutate):
    path = _copy(SAVED, tmp_path)
    with sqlite3.connect(path) as con:
        (doc,) = con.execute("SELECT doc FROM project WHERE id=1").fetchone()
        con.execute("UPDATE project SET doc=? WHERE id=1", (mutate(bytes(doc)),))
    return path


def test_an_unknown_field_type_is_a_read_error_not_a_guess(tmp_path):
    """A future format change must fail loudly, never yield plausible times."""
    path = _corrupt_doc(tmp_path, lambda doc: doc + bytes([0xFE]))
    with pytest.raises(aup3_labels.Aup3ReadError, match="field type"):
        aup3_labels.read_label_tracks(path)


def test_a_truncated_document_is_a_read_error(tmp_path):
    path = _corrupt_doc(tmp_path, lambda doc: doc[:-1])
    with pytest.raises(aup3_labels.Aup3ReadError):
        aup3_labels.read_label_tracks(path)
