"""Offline tests for ``check`` comparing label files against exact label times.

``check`` compared each versioned ``.txt`` with GetInfo's label times, which are
rounded to six significant digits. A file ``quantize`` had written with the exact
beat (100.689076) then read as differing from the project (100.689), and ``check``
wrote an inspection copy for a difference that was not there. It now reads the
checked ``.aup3`` itself; when that read falls back to GetInfo, a file whose
times round to GetInfo's counts as matching.
"""

import rebuildap
from rebuildap import audacity_funcs as af

BEAT = 100.68907563025209


def _check(monkeypatch, tmp_path, file_text, project_labels, precise=True):
    """Run check's comparison for one chords file against ``project_labels``;
    returns (stdout, the source the label read got, the inspection copy path)."""
    aup3 = tmp_path / "song.aup3"
    aup3.write_text("x")
    chords = tmp_path / "chords_song.txt"
    chords.write_text(file_text)
    monkeypatch.setattr(rebuildap.git_tracking, "unversioned", lambda _f: None)
    seen = {}

    def read(source):
        seen["source"] = source
        return {"chords": project_labels}, precise

    monkeypatch.setattr(af, "read_label_tracks_precisely", read)
    rebuildap._check_label_age_via_getinfo(aup3, [chords])
    return seen["source"], tmp_path / "chords.txt"


def test_check_reads_the_project_file_it_checks(monkeypatch, tmp_path, capsys):
    source, _ = _check(
        monkeypatch, tmp_path, "100.689076\t100.689076\tC\n", [(BEAT, BEAT, "C")]
    )
    assert source == tmp_path / "song.aup3"


def test_a_file_holding_the_exact_times_matches(monkeypatch, tmp_path, capsys):
    """The case that used to be a false difference."""
    _, inspection = _check(
        monkeypatch, tmp_path, "100.689076\t100.689076\tC\n", [(BEAT, BEAT, "C")]
    )
    assert "are identical" in capsys.readouterr().out
    assert not inspection.exists()


def test_a_difference_below_getinfos_rounding_is_reported(
    monkeypatch, tmp_path, capsys
):
    """75 us - the bar-59 case - is a real difference once the times are exact."""
    _, inspection = _check(
        monkeypatch, tmp_path, "100.689000\t100.689000\tC\n", [(BEAT, BEAT, "C")]
    )
    assert "differs" in capsys.readouterr().out
    assert inspection.read_text() == "100.689076\t100.689076\tC\n"


def test_a_fallback_check_matches_a_file_that_rounds_to_getinfo(
    monkeypatch, tmp_path, capsys
):
    _, inspection = _check(
        monkeypatch,
        tmp_path,
        "100.689076\t100.689076\tC\n",
        [(100.689, 100.689, "C")],
        precise=False,
    )
    assert "are identical" in capsys.readouterr().out
    assert not inspection.exists()


def test_a_fallback_check_still_reports_what_it_can_see(monkeypatch, tmp_path, capsys):
    _, inspection = _check(
        monkeypatch,
        tmp_path,
        "100.5\t100.5\tC\n",
        [(100.689, 100.689, "C")],
        precise=False,
    )
    assert "differs" in capsys.readouterr().out
    assert inspection.exists()
